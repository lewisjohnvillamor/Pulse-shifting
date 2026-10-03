from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from .config import AppConfig

log = logging.getLogger("pulseshift.ai")

# After a failed call, don't retry the endpoint for this long. These calls
# are synchronous and run inside the live event loop (every market tick)
# and inside every backtest candle, so an unreachable endpoint would
# otherwise block the API for its full timeout, thousands of times.
OFFLINE_COOLDOWN_S = 60.0
_offline_until: dict[str, float] = {}


def offline(name: str) -> bool:
    return time.monotonic() < _offline_until.get(name, 0.0)


def mark_offline(name: str, seconds: float = OFFLINE_COOLDOWN_S) -> None:
    _offline_until[name] = time.monotonic() + seconds


def mark_online(name: str) -> None:
    _offline_until.pop(name, None)


class AiProviderClient:
    """Calls a user-configured AI endpoint for trade decisions.

    The provider entry in data/config.json looks like:
        {"url": "https://…/decide", "api_key": "…", "timeout_ms": 3000}

    Two response shapes are understood:
      - direct decide: {"action","confidence","regime","reasons"}
      - OpenAI-compatible chat: {"choices":[{"message":{"content":"{…json…}"}}]}
    """

    def __init__(self, config: AppConfig, name: str) -> None:
        self.config = config
        self.name = name

    def settings(self) -> dict[str, Any]:
        return self.config.provider(self.name)

    def configured(self) -> bool:
        return bool(self.settings().get("url"))

    def describe_status(self) -> str:
        s = self.settings()
        return s.get("url") or "not configured"

    def decide(self, payload: dict[str, Any], timeout_ms: int = 3000) -> dict | None:
        s = self.settings()
        url = s.get("url")
        if not url or offline(self.name):
            return None
        headers = {"Content-Type": "application/json"}
        key = s.get("api_key")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=(s.get("timeout_ms") or timeout_ms) / 1000
            ) as response:
                raw = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.info("AI provider %s unreachable: %s", self.name, exc)
            mark_offline(self.name)
            return None
        mark_online(self.name)

        if "choices" in raw:
            try:
                raw = json.loads(raw["choices"][0]["message"]["content"])
            except (KeyError, IndexError, ValueError):
                return None
        return raw if isinstance(raw, dict) else None
