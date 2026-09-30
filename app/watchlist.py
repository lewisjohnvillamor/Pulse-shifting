from __future__ import annotations

import json
from pathlib import Path

from .market import normalize_symbol

DEFAULT_SYMBOLS = ["BTCUSDT"]


class Watchlist:
    """Pinned symbols persisted to a JSON file under data/."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.symbols: list[str] = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
            self.symbols = [normalize_symbol(s) for s in raw if isinstance(s, str)]
            return
        except Exception:
            pass
        self.symbols = list(DEFAULT_SYMBOLS)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.symbols, indent=2))

    def add(self, symbol: str) -> str:
        symbol = normalize_symbol(symbol)
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            self.save()
        return symbol

    def remove(self, symbol: str) -> bool:
        symbol = normalize_symbol(symbol)
        if symbol in self.symbols:
            self.symbols.remove(symbol)
            self.save()
            return True
        return False
