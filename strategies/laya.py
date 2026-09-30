"""Laya adapter strategy.

Calls a locally running Laya model to help with decision making. Point
`LAYA_URL` (default http://127.0.0.1:8791/decide) at a small HTTP endpoint
that accepts:

    POST {"symbol": "...", "spread_bps": 1.2,
          "closes": [...], "volumes": [...]}

and returns e.g. {"action": "LONG"|"SHORT"|"FLAT", "confidence": 0.72,
"regime": "TRENDING", "reasons": [...]}.

When Laya is offline the strategy stays FLAT so it simply abstains on the
board — no noise, no crashes. Backtesting Laya replays it against the same
local endpoint over historical candles.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app.strategy import Decision, ParamSpec, Strategy

LAYA_URL = os.environ.get("LAYA_URL", "http://127.0.0.1:8791/decide")


class LayaStrategy(Strategy):
    id = "laya"
    name = "Laya"
    description = "Delegates the call to a local Laya model endpoint; abstains when Laya is offline."
    styles = ["scalping", "day"]
    specs = [
        ParamSpec("min_confidence", "float", 0.55, 0.1, 0.99, 0.01, "Min confidence"),
        ParamSpec("timeout_ms", "int", 1500, 200, 5000, 100, "Timeout (ms)"),
    ]

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision("FLAT", 0.0, "UNKNOWN", 0.0, 0.0, ["Warming up"])

        window = candles[-60:]
        body = json.dumps(
            {
                "spread_bps": spread_bps,
                "closes": [float(c["close"]) for c in window],
                "volumes": [float(c["volume"]) for c in window],
            }
        ).encode()
        request = urllib.request.Request(
            LAYA_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.params["timeout_ms"] / 1000
            ) as response:
                result = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return Decision(
                "FLAT", 0.0, "OFFLINE", 0.0, 0.0, [f"Laya offline at {LAYA_URL}"]
            )

        action = str(result.get("action", "FLAT")).upper()
        if action not in ("LONG", "SHORT", "FLAT"):
            action = "FLAT"
        confidence = float(result.get("confidence", 0.5))
        if confidence < self.params["min_confidence"]:
            action = "FLAT"
        reasons = result.get("reasons") or [f"Laya confidence {confidence:.0%}"]
        return Decision(
            action,
            min(max(confidence, 0.0), 0.99),
            str(result.get("regime", "UNKNOWN")),
            float(result.get("regime_confidence", 0.5)),
            float(result.get("execution_confidence", 0.5)),
            reasons,
        )


STRATEGY = LayaStrategy
