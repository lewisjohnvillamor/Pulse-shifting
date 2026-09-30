from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AppConfig:
    """Local-first settings store (data/config.json — gitignored).
    Holds user-provided AI provider endpoints and API keys (BYO key)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {"providers": {}}
        self.load()

    def load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text())
            self._data.setdefault("providers", {})
        except Exception:
            self._data = {"providers": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2))

    def provider(self, name: str) -> dict[str, Any]:
        return self._data.get("providers", {}).get(name, {})

    def set_provider(self, name: str, cfg: dict[str, Any]) -> dict[str, Any]:
        existing = self.provider(name)
        merged = {**existing, **{k: v for k, v in cfg.items() if v is not None}}
        # Empty string clears an api_key; omitted key keeps the stored one.
        self._data.setdefault("providers", {})[name] = merged
        self.save()
        return merged

    def as_public_dict(self) -> dict[str, Any]:
        """Config safe to send to the UI: api keys are masked."""
        providers = {}
        for name, cfg in self._data.get("providers", {}).items():
            safe = dict(cfg)
            key = safe.get("api_key")
            if key:
                safe["api_key"] = key[:12] + "…" if len(key) > 12 else "***"
                safe["api_key_set"] = True
            providers[name] = safe
        return {"providers": providers}
