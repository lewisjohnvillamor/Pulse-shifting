from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .market import normalize_symbol

# Record a signal at most this often per symbol unless the verdict changes.
RECORD_EVERY_S = 30.0


class SignalMonitor:
    """Per-symbol opt-in monitor: while enabled, every live decision+edge
    evaluation is appended to data/monitor/<symbol>.jsonl."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.enabled: set[str] = set()
        self._last: dict[str, tuple[float, str | None]] = {}

    def set_enabled(self, symbol: str, on: bool) -> bool:
        symbol = normalize_symbol(symbol)
        if on:
            self.enabled.add(symbol)
        else:
            self.enabled.discard(symbol)
            self._last.pop(symbol, None)
        return on

    def is_enabled(self, symbol: str) -> bool:
        return normalize_symbol(symbol) in self.enabled

    def symbols(self) -> list[str]:
        return sorted(self.enabled)

    def record(self, symbol: str, payload: dict[str, Any]) -> None:
        symbol = normalize_symbol(symbol)
        if symbol not in self.enabled:
            return
        verdict = (payload.get("edge") or {}).get("verdict")
        last_ts, last_verdict = self._last.get(symbol, (0.0, None))
        now = time.time()
        if verdict == last_verdict and now - last_ts < RECORD_EVERY_S:
            return
        self._last[symbol] = (now, verdict)
        entry = {
            "ts": int(now * 1000),
            "price": (payload.get("market") or {}).get("price"),
            "decisions": {
                sid: d.get("action")
                for sid, d in (payload.get("decisions") or {}).items()
            },
            "edge": payload.get("edge"),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / f"{symbol}.jsonl").open("a") as handle:
            handle.write(json.dumps(entry) + "\n")

    def recent(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        symbol = normalize_symbol(symbol)
        path = self.directory / f"{symbol}.jsonl"
        if not path.is_file():
            return []
        lines = path.read_text().strip().splitlines()
        return [json.loads(line) for line in lines[-limit:]]
