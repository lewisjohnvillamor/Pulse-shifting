from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any

from .strategy import (
    BreakoutStrategy,
    Decision,
    EmaMomentumStrategy,
    MeanReversionStrategy,
    Strategy,
)

log = logging.getLogger("pulseshift.strategies")

BUILTINS: dict[str, type[Strategy]] = {
    cls.id: cls
    for cls in (EmaMomentumStrategy, MeanReversionStrategy, BreakoutStrategy)
}

# Declarative JSON strategy templates users can author without Python.
JSON_TEMPLATES: dict[str, type[Strategy]] = dict(BUILTINS)


class JsonStrategy(Strategy):
    """A builtin template instantiated from a JSON file in data/strategies."""

    def __init__(self, spec_file: Path) -> None:
        raw = json.loads(spec_file.read_text())
        template = JSON_TEMPLATES.get(raw.get("kind", ""))
        if template is None:
            raise ValueError(f"unknown strategy kind: {raw.get('kind')}")
        self.id = raw.get("id") or spec_file.stem
        self.name = raw.get("name") or self.id
        self.description = raw.get("description", "")
        self.min_candles = template.min_candles
        self.specs = template.specs
        super().__init__(raw.get("params"))
        self._template = template
        self._source = str(spec_file)

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        return self._template(self.params).decide(candles, spread_bps)


def _load_python_plugin(path: Path) -> Strategy | None:
    """Load a user plugin. A plugin file must define `STRATEGY` (a Strategy
    instance or subclass). Plugins run with full interpreter privileges —
    only drop in code you trust."""
    try:
        module_spec = importlib.util.spec_from_file_location(
            f"pulseshift_plugin_{path.stem}", path
        )
        if module_spec is None or module_spec.loader is None:
            return None
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        candidate = getattr(module, "STRATEGY", None)
        if candidate is None:
            return None
        instance = candidate() if isinstance(candidate, type) else candidate
        if not hasattr(instance, "decide"):
            return None
        if not getattr(instance, "id", None):
            instance.id = path.stem
        if not getattr(instance, "name", None) or instance.name == "Base strategy":
            instance.name = path.stem
        if not hasattr(instance, "param_values"):
            instance.param_values = lambda: []  # type: ignore[attr-defined]
        if not hasattr(instance, "configure"):
            instance.configure = lambda params: []  # type: ignore[attr-defined]
        return instance
    except Exception:
        log.exception("Failed to load strategy plugin %s", path)
        return None


class StrategyRegistry:
    def __init__(self, plugin_dir: Path, params_file: Path) -> None:
        # One drop-in directory: *.py plugins and *.json declarative strategies.
        self.plugin_dir = plugin_dir
        self.json_dir = plugin_dir
        self.params_file = params_file
        self.strategies: dict[str, Strategy] = {}
        self.reload()

    def _saved_params(self) -> dict[str, dict[str, float]]:
        try:
            return json.loads(self.params_file.read_text())
        except Exception:
            return {}

    def _save_params(self) -> None:
        self.params_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            sid: s.params
            for sid, s in self.strategies.items()
            if getattr(s, "params", None)
        }
        self.params_file.write_text(json.dumps(payload, indent=2))

    def reload(self) -> None:
        saved = self._saved_params()
        found: dict[str, Strategy] = {}
        for sid, cls in BUILTINS.items():
            found[sid] = cls(saved.get(sid))
        if self.json_dir.is_dir():
            for path in sorted(self.json_dir.glob("*.json")):
                try:
                    instance = JsonStrategy(path)
                    found[instance.id] = instance
                    if instance.id in saved:
                        instance.configure(saved[instance.id])
                except Exception:
                    log.exception("Bad JSON strategy %s", path)
        if self.plugin_dir.is_dir():
            for path in sorted(self.plugin_dir.glob("*.py")):
                plugin = _load_python_plugin(path)
                if plugin is not None:
                    found[plugin.id] = plugin
        self.strategies = found

    def get(self, strategy_id: str) -> Strategy | None:
        return self.strategies.get(strategy_id)

    def decide_all(self, candles: list[dict], spread_bps: float) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sid, strategy in self.strategies.items():
            try:
                out[sid] = strategy.decide(candles, spread_bps).as_dict()
            except Exception as exc:
                out[sid] = {"action": "ERROR", "reasons": [str(exc)]}
        return out

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "id": sid,
                "name": s.name,
                "description": s.description,
                "params": s.param_values() if hasattr(s, "param_values") else [],
                "builtin": sid in BUILTINS,
            }
            for sid, s in sorted(self.strategies.items())
        ]

    def configure(self, strategy_id: str, params: dict[str, float]) -> list[dict] | None:
        strategy = self.strategies.get(strategy_id)
        if strategy is None:
            return None
        result = strategy.configure(params)
        self._save_params()
        return result
