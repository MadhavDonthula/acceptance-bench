"""Load provider config (config/providers.toml) and .env secrets.

No third-party dotenv dependency: .env is parsed with a tiny KEY=VALUE reader.
Keys are read only from the environment / .env and are never logged.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from urllib.parse import urlsplit

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PROVIDERS_TOML = os.path.join(ROOT, "config", "providers.toml")
ENV_PATH = os.path.join(ROOT, ".env")


def load_dotenv(path: str = ENV_PATH) -> None:
    """Populate os.environ from .env without overwriting existing values."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key:  # skip malformed lines (e.g. a stray '=') rather than crash
                continue
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


@dataclass
class ProviderCfg:
    name: str
    origin: str          # scheme://host, e.g. https://inference.coralbricks.ai
    base_url: str
    model: str
    api_key_env: str
    price_input: float
    price_cached_read: float
    price_output: float

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def cost(self, uncached: int, cached: int, output: int) -> float:
        # prices are per 1,000,000 tokens
        return (
            uncached * self.price_input
            + cached * self.price_cached_read
            + output * self.price_output
        ) / 1_000_000.0


@dataclass
class Settings:
    temperature: float
    max_tokens: int
    reasoning_effort: str


def _raw() -> dict:
    with open(PROVIDERS_TOML, "rb") as f:
        return tomllib.load(f)


def load_settings() -> Settings:
    s = _raw()["model_settings"]
    return Settings(
        temperature=float(s["temperature"]),
        max_tokens=int(s["max_tokens"]),
        reasoning_effort=str(s["reasoning_effort"]),
    )


def price_date() -> str:
    return _raw()["price_date"]


def load_providers() -> dict[str, ProviderCfg]:
    data = _raw()["providers"]
    out: dict[str, ProviderCfg] = {}
    for name, p in data.items():
        base_url = p["base_url"]
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
        # Offline-test hook: OPROXY_{NAME}_ORIGIN overrides the upstream origin
        # (used by the offline smoke test to point a provider at the mock).
        origin = os.environ.get(f"OPROXY_{name.upper()}_ORIGIN", origin)
        if name == "baseten":
            model = os.environ.get(p.get("model_env", "BASETEN_MODEL")) or p["model_default"]
        else:
            model = p["model"]
        out[name] = ProviderCfg(
            name=name,
            origin=origin,
            base_url=base_url,
            model=model,
            api_key_env=p["api_key_env"],
            price_input=float(p["price_input"]),
            price_cached_read=float(p["price_cached_read"]),
            price_output=float(p["price_output"]),
        )
    return out
