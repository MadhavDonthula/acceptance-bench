"""Normalize token usage across the API shapes Coral can return.

Coral speaks three wire formats (Chat Completions, Responses, Anthropic
Messages) and we don't know ahead of time which cache fields it fills in.
So we look for every field name we know about and keep the raw dict too.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Usage:
    input_tokens: int = 0          # total prompt tokens the server counted
    cached_tokens: int = 0         # prompt tokens served from the prefix cache
    cache_write_tokens: int = -1   # -1 means "server didn't say"
    output_tokens: int = 0         # completion tokens, reasoning included
    reasoning_tokens: int = 0
    cache_field: str = ""          # which field the cached count came from

    @property
    def uncached_tokens(self) -> int:
        return max(self.input_tokens - self.cached_tokens, 0)

    @property
    def cache_hit_ratio(self) -> float:
        if self.input_tokens <= 0:
            return 0.0
        return self.cached_tokens / self.input_tokens

    def to_dict(self) -> dict:
        data = asdict(self)
        data["uncached_tokens"] = self.uncached_tokens
        data["cache_hit_ratio"] = round(self.cache_hit_ratio, 4)
        return data


def _get_int(source: dict, *path) -> int | None:
    """Walk a nested dict and return an int, or None if any key is missing."""
    current = source
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if isinstance(current, (int, float)):
        return int(current)
    return None


# Places a cached-token count can show up, in the order we trust them.
CACHED_FIELD_CANDIDATES = [
    ("prompt_tokens_details", "cached_tokens"),   # OpenAI chat completions
    ("input_tokens_details", "cached_tokens"),    # OpenAI responses
    ("cache_read_input_tokens",),                 # Anthropic messages
    ("prompt_cache_hit_tokens",),                 # DeepSeek native API
    ("cached_tokens",),                           # generic
]

CACHE_WRITE_FIELD_CANDIDATES = [
    ("cache_creation_input_tokens",),             # Anthropic messages
    ("prompt_tokens_details", "cache_write_tokens"),
    ("input_tokens_details", "cache_write_tokens"),
    ("prompt_cache_miss_tokens",),                # DeepSeek native API
]


def parse_usage(raw: dict | None) -> Usage:
    if not raw:
        return Usage()

    usage = Usage()

    input_tokens = _get_int(raw, "prompt_tokens")
    if input_tokens is None:
        input_tokens = _get_int(raw, "input_tokens")
    usage.input_tokens = input_tokens or 0

    output_tokens = _get_int(raw, "completion_tokens")
    if output_tokens is None:
        output_tokens = _get_int(raw, "output_tokens")
    usage.output_tokens = output_tokens or 0

    for path in CACHED_FIELD_CANDIDATES:
        value = _get_int(raw, *path)
        if value is not None:
            usage.cached_tokens = value
            usage.cache_field = ".".join(path)
            break

    for path in CACHE_WRITE_FIELD_CANDIDATES:
        value = _get_int(raw, *path)
        if value is not None:
            usage.cache_write_tokens = value
            break

    reasoning = _get_int(raw, "completion_tokens_details", "reasoning_tokens")
    if reasoning is None:
        reasoning = _get_int(raw, "output_tokens_details", "reasoning_tokens")
    usage.reasoning_tokens = reasoning or 0

    # Anthropic reports input_tokens as the *uncached* part only, with cache
    # reads and writes listed separately. Fold them back into one total so
    # every format means the same thing by input_tokens.
    if "cache_read_input_tokens" in raw and "prompt_tokens" not in raw:
        cache_write = max(usage.cache_write_tokens, 0)
        usage.input_tokens = usage.input_tokens + usage.cached_tokens + cache_write

    return usage
