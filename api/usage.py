"""Usage metric helpers for WebUI display payloads.

Prompt-cache hit percentage is cached prompt reads over the full prompt total
(input + cache reads + cache writes). Keep this calculation in the backend so
browser display code cannot drift across context indicator and per-turn labels.
"""


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def prompt_cache_hit_percent(cache_read_tokens, prompt_tokens):
    """Return cached reads as a percent of full prompt-token total.

    ``prompt_tokens`` must include ordinary input, cache reads, and cache writes
    (matching Agent's ``session_prompt_tokens`` value).
    """
    cache_read = _to_int(cache_read_tokens)
    prompt = _to_int(prompt_tokens)
    if cache_read <= 0 or prompt <= 0:
        return None
    return min(100, round((cache_read / prompt) * 100))


def normalize_stream_usage(usage: dict | None) -> dict:
    """Return a WebUI usage payload with Anthropic cache aliases.

    Providers disagree on token field names. Preserve Anthropic's native cache
    counters while also exposing the shorter names consumed by the existing UI.
    """
    source = usage if isinstance(usage, dict) else {}
    cache_read = _to_int(source.get("cache_read_input_tokens", source.get("cache_read_tokens", 0)))
    cache_write = _to_int(source.get("cache_creation_input_tokens", source.get("cache_write_tokens", 0)))
    normalized = dict(source)
    normalized["input_tokens"] = _to_int(
        source.get("input_tokens", source.get("prompt_tokens", 0))
    )
    normalized["output_tokens"] = _to_int(
        source.get("output_tokens", source.get("completion_tokens", 0))
    )
    normalized["cache_read_input_tokens"] = cache_read
    normalized["cache_creation_input_tokens"] = cache_write
    normalized["cache_read_tokens"] = cache_read
    normalized["cache_write_tokens"] = cache_write
    return normalized
