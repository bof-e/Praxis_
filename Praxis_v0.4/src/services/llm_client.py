"""
LLM Client - Phase 3

Every caller in this codebase treats the LLM as *optional augmentation*,
never a hard dependency: §1.3 principle 2 ("Praxis ne devine jamais
silencieusement") means a missing/failing LLM call must fall back to the
existing deterministic heuristic, not crash or block the task. That
fallback contract lives here once, so agents/services don't each
reimplement it.

Two providers, selected by settings.LLM_PROVIDER:

- "anthropic" (default): api.anthropic.com. Set LLM_API_KEY in .env, or
  just export ANTHROPIC_API_KEY (the SDK's own default env var).

- "openai_compatible": any server implementing OpenAI's /chat/completions
  schema - this is how Qwen is normally reached, either:
    * Alibaba Cloud DashScope (hosted): LLM_BASE_URL=
      https://dashscope.aliyuncs.com/compatible-mode/v1, LLM_MODEL=
      qwen-plus (or qwen-max, qwen-turbo, ...), LLM_API_KEY=<DashScope key>.
    * Self-hosted via Ollama (fully local, no key, no data leaves the
      machine): LLM_BASE_URL=http://localhost:11434/v1, LLM_MODEL=
      qwen2.5:7b (whatever you `ollama pull`ed), LLM_API_KEY can be
      anything non-empty ("ollama" works - Ollama doesn't check it).
  The same LLM_PROVIDER=openai_compatible path also reaches DeepSeek,
  OpenRouter, vLLM, text-generation-inference, or OpenAI itself - only
  LLM_BASE_URL/LLM_MODEL/LLM_API_KEY change. See .env.example.

Both SDKs (anthropic, openai) were verified against their actual installed
API surface before writing this (constructor signatures, response shapes)
- see docs/REFONTE_v0.4.md. Neither provider's live endpoint could be
called from this development sandbox (no network route to api.anthropic.com
with a real key, none at all to dashscope.aliyuncs.com or a local Ollama
instance) - test the actual connection yourself with the snippet in
.env.example once configured.
"""
import json
import os
import re
from typing import Optional, Dict, Any

from ..config import settings

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import openai
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


def _provider() -> str:
    return (settings.LLM_PROVIDER or "anthropic").strip().lower()


def is_configured() -> bool:
    """Whether a real LLM call can be attempted at all, for the currently
    configured provider."""
    provider = _provider()
    if provider == "anthropic":
        return _ANTHROPIC_AVAILABLE and bool(settings.LLM_API_KEY or os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "openai_compatible":
        api_key = settings.LLM_API_KEY or os.environ.get("LLM_API_KEY")
        return _OPENAI_AVAILABLE and bool(api_key) and bool(settings.LLM_BASE_URL)
    return False


class LLMClient:
    """Minimal wrapper. Every method returns None on any failure
    (missing key, network error, timeout, malformed response, unknown
    provider) instead of raising - callers are expected to fall back to
    the heuristic path."""

    def __init__(self):
        self.provider = _provider()
        self._client = None
        if not is_configured():
            return

        if self.provider == "anthropic":
            self._client = anthropic.Anthropic(
                api_key=settings.LLM_API_KEY, timeout=settings.LLM_TIMEOUT_SECONDS
            )
        elif self.provider == "openai_compatible":
            api_key = settings.LLM_API_KEY or os.environ.get("LLM_API_KEY")
            self._client = openai.OpenAI(
                api_key=api_key, base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete(self, system: str, user: str, max_tokens: Optional[int] = None) -> Optional[str]:
        """Raw text completion. Returns None on any failure."""
        if not self._client:
            return None
        try:
            if self.provider == "anthropic":
                response = self._client.messages.create(
                    model=settings.LLM_MODEL,
                    max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                    temperature=settings.LLM_TEMPERATURE,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
                return "".join(parts).strip() or None

            elif self.provider == "openai_compatible":
                response = self._client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                    temperature=settings.LLM_TEMPERATURE,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                content = response.choices[0].message.content
                return content.strip() if content else None
        except Exception:
            return None
        return None

    def complete_json(self, system: str, user: str, max_tokens: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Completion expected to be a single JSON object. Tolerates markdown
        code fences and leading/trailing prose around the object (open-weight
        models served through Ollama/vLLM are noticeably less reliable about
        "JSON only" instructions than Claude - this extraction step matters
        more for those than for Anthropic). Returns None if no valid JSON
        object can be extracted."""
        text = self.complete(
            system + "\n\nRéponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après, sans balises markdown.",
            user,
            max_tokens=max_tokens,
        )
        if not text:
            return None
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
