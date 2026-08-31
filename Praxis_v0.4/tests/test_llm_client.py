"""LLMClient.complete_json must tolerate the messy ways a model can wrap
JSON (markdown fences, a sentence before/after) without needing a real
API call - we test the parsing logic by stubbing complete()."""
from src.services.llm_client import LLMClient


def _client_with_stubbed_text(text):
    client = LLMClient.__new__(LLMClient)  # skip __init__ (no API key needed)
    client._client = object()  # truthy, so complete_json's plumbing runs
    client.complete = lambda system, user, max_tokens=None: text
    return client


def test_complete_json_parses_plain_json():
    client = _client_with_stubbed_text('{"a": 1, "b": "x"}')
    assert client.complete_json("s", "u") == {"a": 1, "b": "x"}


def test_complete_json_strips_markdown_fences():
    client = _client_with_stubbed_text('```json\n{"a": 1}\n```')
    assert client.complete_json("s", "u") == {"a": 1}


def test_complete_json_extracts_object_from_surrounding_prose():
    client = _client_with_stubbed_text('Voici le résultat :\n{"a": 1}\nJ\'espère que ça aide !')
    assert client.complete_json("s", "u") == {"a": 1}


def test_complete_json_returns_none_on_garbage():
    client = _client_with_stubbed_text("ceci n'est pas du json")
    assert client.complete_json("s", "u") is None


def test_complete_json_returns_none_when_underlying_completion_is_none():
    client = _client_with_stubbed_text(None)
    assert client.complete_json("s", "u") is None


# --- Provider selection / openai_compatible (Qwen via DashScope or Ollama,
# DeepSeek, OpenRouter, ...) - no real network call, but the actual
# construction + dispatch path is exercised with a fake SDK object so a
# broken kwarg name or response-shape assumption would fail these tests. ---

from unittest.mock import MagicMock
from src.services import llm_client as llm_client_module


def test_is_configured_false_for_openai_compatible_without_base_url(monkeypatch):
    monkeypatch.setattr(llm_client_module.settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(llm_client_module.settings, "LLM_API_KEY", "some-key")
    monkeypatch.setattr(llm_client_module.settings, "LLM_BASE_URL", None)
    assert llm_client_module.is_configured() is False


def test_is_configured_true_for_openai_compatible_with_base_url_and_key(monkeypatch):
    monkeypatch.setattr(llm_client_module.settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(llm_client_module.settings, "LLM_API_KEY", "some-key")
    monkeypatch.setattr(llm_client_module.settings, "LLM_BASE_URL", "http://localhost:11434/v1")
    assert llm_client_module.is_configured() is True


def test_unknown_provider_is_never_configured(monkeypatch):
    monkeypatch.setattr(llm_client_module.settings, "LLM_PROVIDER", "something_made_up")
    assert llm_client_module.is_configured() is False


def test_openai_compatible_client_construction_and_dispatch(monkeypatch):
    """Simulates the Qwen-via-Ollama setup: LLM_BASE_URL pointed at a local
    server, an arbitrary non-empty API key (Ollama ignores it), a Qwen
    model name. Checks the real construction/dispatch path - kwarg names,
    response-shape parsing - not just that *something* gets called."""
    monkeypatch.setattr(llm_client_module.settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(llm_client_module.settings, "LLM_API_KEY", "ollama")
    monkeypatch.setattr(llm_client_module.settings, "LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(llm_client_module.settings, "LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(llm_client_module, "_OPENAI_AVAILABLE", True)

    captured = {}

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="Bonjour depuis Qwen"))]

    fake_openai_client = MagicMock()
    fake_openai_client.chat.completions.create.side_effect = (
        lambda **kwargs: captured.update(kwargs) or fake_response
    )

    monkeypatch.setattr(llm_client_module.openai, "OpenAI", MagicMock(return_value=fake_openai_client))

    client = LLMClient()
    assert client.available is True

    result = client.complete("Tu es un assistant.", "Dis bonjour.")
    assert result == "Bonjour depuis Qwen"
    assert captured["model"] == "qwen2.5:7b"
    assert captured["messages"] == [
        {"role": "system", "content": "Tu es un assistant."},
        {"role": "user", "content": "Dis bonjour."},
    ]

    llm_client_module.openai.OpenAI.assert_called_once_with(
        api_key="ollama", base_url="http://localhost:11434/v1",
        timeout=llm_client_module.settings.LLM_TIMEOUT_SECONDS,
    )


def test_openai_compatible_dispatch_returns_none_on_sdk_exception(monkeypatch):
    """A dead Ollama server, a wrong model name, a network blip - any of
    these must degrade to None (heuristic fallback), never raise up into
    the caller (llm_assist, ResearchAgent, ...)."""
    monkeypatch.setattr(llm_client_module.settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(llm_client_module.settings, "LLM_API_KEY", "ollama")
    monkeypatch.setattr(llm_client_module.settings, "LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(llm_client_module, "_OPENAI_AVAILABLE", True)

    fake_openai_client = MagicMock()
    fake_openai_client.chat.completions.create.side_effect = ConnectionError("connection refused")
    monkeypatch.setattr(llm_client_module.openai, "OpenAI", MagicMock(return_value=fake_openai_client))

    client = LLMClient()
    assert client.complete("s", "u") is None


def test_openrouter_configuration_dispatches_correctly(monkeypatch):
    """This project's actual deployment: Qwen via OpenRouter, not DashScope
    - see .env.example Option B1. Same openai_compatible code path as
    Ollama, different base_url/model/key; pinned here so a future change
    to the dispatch logic can't silently break this specific, real setup."""
    monkeypatch.setattr(llm_client_module.settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(llm_client_module.settings, "LLM_API_KEY", "sk-or-v1-test-key")
    monkeypatch.setattr(llm_client_module.settings, "LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm_client_module.settings, "LLM_MODEL", "qwen/qwen3.5-7b-instruct")
    monkeypatch.setattr(llm_client_module, "_OPENAI_AVAILABLE", True)

    captured = {}
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="Réponse de Qwen via OpenRouter"))]

    fake_openai_client = MagicMock()
    fake_openai_client.chat.completions.create.side_effect = (
        lambda **kwargs: captured.update(kwargs) or fake_response
    )
    monkeypatch.setattr(llm_client_module.openai, "OpenAI", MagicMock(return_value=fake_openai_client))

    client = LLMClient()
    assert client.available is True
    assert client.complete("s", "u") == "Réponse de Qwen via OpenRouter"
    assert captured["model"] == "qwen/qwen3.5-7b-instruct"

    llm_client_module.openai.OpenAI.assert_called_once_with(
        api_key="sk-or-v1-test-key", base_url="https://openrouter.ai/api/v1",
        timeout=llm_client_module.settings.LLM_TIMEOUT_SECONDS,
    )
