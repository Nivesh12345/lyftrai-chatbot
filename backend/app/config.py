"""All configuration comes from environment variables (read on demand)."""
import os
from dataclasses import dataclass, field

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, concise assistant. "
    "Format responses in valid Markdown. Use proper headings, lists, tables, and math. "
    "Use fenced code blocks only for code, include a language name when known, "
    "and always close every code fence."
)


def _bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    llm_api_keys: list[str] = field(repr=False)  # never print keys
    llm_model: str
    llm_models: list[str]
    database_url: str
    allowed_origins: list[str]
    enable_failure_simulation: bool
    default_system_prompt: str
    cache_type: str  # "local" (in-memory) or "disk"
    cache_dir: str
    request_timeout: float


def get_settings() -> Settings:
    llm_model = os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite").strip()
    llm_models = _csv(os.getenv("LLM_MODELS"))
    if not llm_models:
        llm_models = [llm_model]

    return Settings(
        llm_api_keys=_csv(os.getenv("LLM_API_KEYS")),
        llm_model=llm_model,
        llm_models=llm_models,
        database_url=os.getenv("DATABASE_URL", "sqlite:////data/app.db"),
        allowed_origins=_csv(os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")),
        enable_failure_simulation=_bool(os.getenv("ENABLE_FAILURE_SIMULATION")),
        default_system_prompt=os.getenv("DEFAULT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        cache_type=os.getenv("CACHE_TYPE", "local").strip().lower(),
        cache_dir=os.getenv("CACHE_DIR", "/data/llm_cache"),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "30")),
    )
