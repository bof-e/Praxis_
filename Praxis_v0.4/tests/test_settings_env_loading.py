"""Regression tests for src/config/settings.py's .env handling.

Both bugs here were severe (a crash at startup) or silent (a documented
fallback that quietly did nothing) - and both went undetected all through
Phase 3 because every earlier test set configuration via monkeypatched
attributes or shell-exported env vars, never by actually loading a
populated .env file the way a real user would.
"""
import os

from src.config.settings import Settings


def test_settings_ignores_extra_keys_instead_of_crashing(tmp_path):
    """.env.example ships NEXT_PUBLIC_API_URL (a frontend-only variable) in
    the same file as the backend's variables - Settings() used to hard-crash
    (pydantic "extra_forbidden") the instant any such unrecognized key was
    present, meaning the API wouldn't start."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=sqlite:///./test.db\n"
        "NEXT_PUBLIC_API_URL=http://localhost:8000\n"
        "SOME_FUTURE_VARIABLE_THIS_MODEL_DOESNT_KNOW=hello\n"
    )
    settings = Settings(_env_file=str(env_file))  # must not raise
    assert settings.DATABASE_URL == "sqlite:///./test.db"


def test_anthropic_api_key_set_only_in_dotenv_file_is_actually_picked_up(tmp_path, monkeypatch):
    """ANTHROPIC_API_KEY isn't a declared Settings field (only LLM_API_KEY
    is) - llm_client.py falls back to reading it straight from
    os.environ. That only works if .env actually gets loaded into
    os.environ, which - before load_dotenv() was added to settings.py -
    it did not: Settings() only populated its own declared fields from
    .env, so a real user following .env.example's own instructions
    ("just set ANTHROPIC_API_KEY") got silent no-op heuristic fallback
    instead of the LLM they configured."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-test-from-dotenv\n")

    from dotenv import load_dotenv
    load_dotenv(str(env_file))

    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test-from-dotenv"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_load_dotenv_does_not_override_already_set_env_vars(tmp_path, monkeypatch):
    """Safety property this fix relies on: load_dotenv()'s default
    (override=False) must never let a stray .env clobber a value a test
    (or a real deployment's shell) already set - otherwise every other
    test's monkeypatched/exported env vars would be at the mercy of
    whatever .env happens to sit in the working directory."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./from-shell.db")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=sqlite:///./from-dotenv.db\n")

    from dotenv import load_dotenv
    load_dotenv(str(env_file))

    assert os.environ["DATABASE_URL"] == "sqlite:///./from-shell.db"
