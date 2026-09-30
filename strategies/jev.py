"""Jev adapter strategy.

Delegates the call to a Jev AI endpoint. Configure the endpoint + your API
key via the settings gear in the UI (stored in data/config.json under
providers.jev as {"url", "api_key"}) — the strategy abstains (FLAT) until
it's configured or while Jev is unreachable. Works live and in backtests.
"""

from __future__ import annotations

from pathlib import Path

from app.ai import AiProviderClient
from app.config import AppConfig
from app.strategy import Decision, ParamSpec, Strategy

CONFIG = AppConfig(Path(__file__).resolve().parent.parent / "data" / "config.json")


class JevStrategy(Strategy):
    id = "jev"
    name = "Jev"
    description = "Delegates the call to a configured Jev AI endpoint (BYO key via settings); abstains when unset/offline."
    styles = ["scalping", "day"]
    specs = [
        ParamSpec("min_confidence", "float", 0.55, 0.1, 0.99, 0.01, "Min confidence"),
    ]

    def _client(self) -> AiProviderClient:
        CONFIG.load()
        return AiProviderClient(CONFIG, "jev")

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision("FLAT", 0.0, "UNKNOWN", 0.0, 0.0, ["Warming up"])

        client = self._client()
        if not client.configured():
            return Decision(
                "FLAT",
                0.0,
                "OFFLINE",
                0.0,
                0.0,
                ["Jev not configured — set its endpoint + API key in settings"],
            )

        window = candles[-60:]
        result = client.decide(
            {
                "spread_bps": spread_bps,
                "closes": [float(c["close"]) for c in window],
                "volumes": [float(c["volume"]) for c in window],
            }
        )
        if result is None:
            return Decision(
                "FLAT",
                0.0,
                "OFFLINE",
                0.0,
                0.0,
                [f"Jev unreachable at {client.describe_status()}"],
            )

        action = str(result.get("action", "FLAT")).upper()
        if action not in ("LONG", "SHORT", "FLAT"):
            action = "FLAT"
        confidence = float(result.get("confidence", 0.5))
        if confidence < self.params["min_confidence"]:
            action = "FLAT"
        reasons = result.get("reasons") or [f"Jev confidence {confidence:.0%}"]
        return Decision(
            action,
            min(max(confidence, 0.0), 0.99),
            str(result.get("regime", "UNKNOWN")),
            float(result.get("regime_confidence", 0.5)),
            float(result.get("execution_confidence", 0.5)),
            reasons,
        )


STRATEGY = JevStrategy
