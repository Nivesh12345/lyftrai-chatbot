"""LiteLLM wrapper: built-in response cache + ordered API-key failover."""
import logging
from dataclasses import dataclass

import litellm

from .config import Settings

logger = logging.getLogger("chatbot.llm")
litellm.suppress_debug_info = True

SIMULATABLE_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed."""


class SimulatedProviderError(Exception):
    """Raised for keys listed in X-Simulate-Failure (that request only)."""

    def __init__(self, status_code: int):
        super().__init__(f"simulated provider status {status_code}")
        self.status_code = status_code


@dataclass
class LLMResult:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    key_id: str | None
    fallback_attempts: int


def setup_cache(settings: Settings) -> None:
    """Enable LiteLLM's built-in cache (in-memory by default, disk optional)."""
    from litellm import Cache

    if settings.cache_type == "disk":
        try:
            litellm.cache = Cache(type="disk", disk_cache_dir=settings.cache_dir)
            return
        except Exception as exc:  # fall back rather than crash on startup
            logger.warning("disk cache unavailable (%s); using in-memory cache", type(exc).__name__)
    litellm.cache = Cache(type="local")


def cache_enabled() -> bool:
    return litellm.cache is not None


def parse_simulation(header: str | None) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429'  ->  {'key_1': 429, ...}. Bad pairs are ignored."""
    result: dict[str, int] = {}
    for pair in (header or "").split(","):
        name, sep, status = pair.strip().partition("=")
        if not sep:
            continue
        try:
            code = int(status.strip())
        except ValueError:
            continue
        if code in SIMULATABLE_STATUSES and name.strip():
            result[name.strip()] = code
    return result


def _usage(resp, model: str, settings: Settings, messages: list[dict], reply: str) -> tuple[int, int]:
    usage = getattr(resp, "usage", None)
    inp = getattr(usage, "prompt_tokens", None)
    out = getattr(usage, "completion_tokens", None)
    if inp is None:
        inp = litellm.token_counter(model=model, messages=messages)
    if out is None:
        out = litellm.token_counter(model=model, text=reply)
    return int(inp), int(out)


def complete(messages: list[dict], settings: Settings, simulate: dict[str, int] | None = None) -> LLMResult:
    """Try every model/key combination in order. First success wins.

    The cache key is built by LiteLLM from model + messages (the API key is not part
    of it). Configured models are tried independently of configured keys.
    """
    simulate = simulate or {}
    failed_key_ids: set[str] = set()
    for model in settings.llm_models:
        for index, api_key in enumerate(settings.llm_api_keys, start=1):
            key_id = f"key_{index}"
            try:
                status = simulate.get(key_id, simulate.get("*"))
                if status:
                    raise SimulatedProviderError(status)
                resp = litellm.completion(
                    model=model,
                    messages=messages,
                    api_key=api_key,
                    num_retries=0,
                    timeout=settings.request_timeout,
                    # Simulated requests must be a real live answer, so skip the cache.
                    caching=not simulate,
                )
                reply = (resp.choices[0].message.content or "").strip()
                if not reply:
                    raise ValueError("empty completion")
                hidden = getattr(resp, "_hidden_params", None) or {}
                cache_hit = bool(hidden.get("cache_hit"))
                inp, out = _usage(resp, model, settings, messages, reply)
            except Exception as exc:
                logger.warning(
                    "model=%s %s failed (%s); trying next combination",
                    model,
                    key_id,
                    type(exc).__name__,
                )
                failed_key_ids.add(key_id)
                continue
            return LLMResult(
                reply=reply,
                model=model,
                input_tokens=inp,
                output_tokens=out,
                cache_hit=cache_hit,
                key_id=None if cache_hit else key_id,
                fallback_attempts=0 if cache_hit else len(failed_key_ids),
            )
    raise AllProvidersUnavailable()
