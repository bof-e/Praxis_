"""
Configuration settings for Praxis v0.3 MVP

Decisions made per §16:
- Autonomy level default: 1 (supervised execution)
- KnowledgeBase: empty at start, manually populated
- Traceability: present but non-blocking in MVP
- Job queue: no Celery/Redis (would be overkill for a single-user personal
  tool) - v0.4 added a lightweight in-process background-thread runner
  instead (src/services/job_tracker.py + /tasks/{id}/execute-async), which
  covers the actual need (don't block the browser on a slow Excel file)
  without a new service to run and keep alive.
"""

from pydantic_settings import BaseSettings
from typing import Optional
from dotenv import load_dotenv
import os

# Load .env into os.environ explicitly (not just into the Settings object -
# see Config below) so that the raw-env-var fallbacks used throughout this
# codebase (ANTHROPIC_API_KEY, E2B_API_KEY - see llm_client.py/sandbox.py)
# work whether the person exports them in their shell or just puts them in
# .env, which is how everyone actually does it. Without this, .env.example's
# own "just set ANTHROPIC_API_KEY" instructions silently did nothing.
load_dotenv()


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Praxis"
    APP_VERSION: str = "0.3.0-mvp"
    DEBUG: bool = True
    
    # Database - SQLite for MVP, PostgreSQL for production
    DATABASE_URL: str = "sqlite:///./praxis.db"
    # For production: DATABASE_URL = "postgresql://user:pass@localhost:5432/praxis"
    
    # File storage
    STORAGE_PATH: str = "./storage/artifacts"
    UPLOAD_PATH: str = "./storage/uploads"
    MAX_FILE_SIZE_MB: int = 50
    
    # AI/LLM configuration (Phase 3 - optional: everything that uses this
    # falls back to the Phase 2 deterministic heuristics when no key is set)
    #
    # LLM_PROVIDER: "anthropic" (default) or "openai_compatible".
    # "openai_compatible" talks to any server implementing the OpenAI
    # /chat/completions schema - this covers Qwen (either Alibaba Cloud's
    # DashScope hosted endpoint, or a self-hosted Qwen served through
    # Ollama/vLLM/text-generation-inference), DeepSeek, OpenRouter, or
    # OpenAI itself. Set LLM_BASE_URL to point at it. See .env.example for
    # copy-pasteable examples of each.
    LLM_PROVIDER: str = "anthropic"
    LLM_MODEL: str = "claude-sonnet-5"
    LLM_API_KEY: Optional[str] = None  # falls back to $ANTHROPIC_API_KEY (anthropic) or $LLM_API_KEY (openai_compatible) if unset
    LLM_BASE_URL: Optional[str] = None  # required when LLM_PROVIDER=openai_compatible; ignored for anthropic
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 2048
    LLM_TIMEOUT_SECONDS: float = 30.0

    # Sandboxed execution (§9, Phase 3) - optional: DataAnalysisAgent runs
    # in-process (as it always has) when this is unset, and only tries the
    # E2B sandbox when a key is configured. Falls back to $E2B_API_KEY.
    E2B_API_KEY: Optional[str] = None
    E2B_TIMEOUT_SECONDS: int = 120

    # Domain routing (Praxis v1.0, Phase 4 - docs/PRAXIS_V1_ARCHITECTURE.md §4)
    # Below this confidence, domain_router.py asks for clarification
    # instead of assembling a plan from a guessed set of poles.
    DOMAIN_ROUTER_CONFIDENCE_THRESHOLD: float = 0.6
    
    # Autonomy settings (§4)
    DEFAULT_AUTONOMY_LEVEL: int = 1  # Level 1: supervised execution
    
    # Readiness thresholds (§3.3)
    DEFAULT_READINESS_THRESHOLD: float = 0.75
    CRITICAL_DIMENSION_THRESHOLD: float = 0.50
    
    # Learning settings (§3.7)
    LEARNING_ENABLED: bool = True
    AUTO_ADAPT_ENABLED: bool = False  # Disabled for MVP, requires explicit validation
    
    # Error recovery (§3.16)
    MAX_RETRY_ATTEMPTS: int = 3
    ERROR_ESCALATION_ENABLED: bool = True
    
    # Traceability (§3.13)
    TRACEABILITY_REQUIRED: bool = False  # Non-blocking for MVP
    TRACEABILITY_WARNING_ENABLED: bool = True
    
    # Metrics (§14)
    METRICS_ENABLED: bool = True
    
    # Security
    API_KEY_HEADER: str = "X-API-Key"
    ENCRYPTION_ENABLED: bool = False  # Can be enabled for sensitive data
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        # A single shared .env commonly holds vars this Settings model
        # doesn't declare - NEXT_PUBLIC_API_URL (frontend-only) sits in the
        # very .env.example this project ships. Pydantic's default is to
        # hard-crash the whole backend on any such "extra" key - so simply
        # uncommenting a documented frontend variable, or a future addition
        # to .env.example, silently took the API down. Ignoring extras is
        # the only sane default for a shared file.
        extra = "ignore"


settings = Settings()

# Ensure storage directories exist
os.makedirs(settings.STORAGE_PATH, exist_ok=True)
os.makedirs(settings.UPLOAD_PATH, exist_ok=True)
