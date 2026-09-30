from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Literal

Action = Literal["LONG", "SHORT", "FLAT"]


@dataclass
class Decision:
    action: Action
    confidence: float
    regime: str
    regime_confidence: float
    execution_confidence: float
    reasons: list[str]

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 4),
            "execution_confidence": round(self.execution_confidence, 4),
            "reasons": self.reasons,
        }


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * k + result * (1 - k)
    return result


class EmaMomentumStrategy:
    name = "EMA Momentum v1"

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < 30:
            return Decision(
                "FLAT", 0.0, "UNKNOWN", 0.0, 0.0, ["Need at least 30 candles"]
            )

        closes = [float(c["close"]) for c in candles]
        volumes = [float(c["volume"]) for c in candles]

        fast = ema(closes[-30:], 9)
        slow = ema(closes[-30:], 21)
        latest = closes[-1]
        ret5 = (latest / closes[-6] - 1) if closes[-6] else 0.0
        avg_volume = mean(volumes[-21:-1]) or 1.0
        volume_ratio = volumes[-1] / avg_volume
        trend_gap = (fast / slow - 1) if slow else 0.0

        trend_strength = min(abs(trend_gap) * 3500, 1.0)
        momentum_strength = min(abs(ret5) * 500, 1.0)
        volume_strength = min(max((volume_ratio - 0.7) / 1.3, 0.0), 1.0)
        execution_confidence = max(0.0, min(1.0, 1 - spread_bps / 12))

        regime = "TRENDING" if trend_strength > 0.18 else "RANGING"
        regime_confidence = 0.55 + min(trend_strength * 0.4, 0.4)

        bullish = fast > slow and ret5 > 0
        bearish = fast < slow and ret5 < 0

        raw_score = (
            0.35 * trend_strength
            + 0.30 * momentum_strength
            + 0.20 * volume_strength
            + 0.15 * execution_confidence
        )
        confidence = max(0.0, min(0.99, 0.45 + raw_score * 0.5))

        reasons = [
            f"EMA9 {'above' if fast > slow else 'below'} EMA21",
            f"5-candle momentum {ret5 * 100:+.2f}%",
            f"Volume {volume_ratio:.2f}x recent average",
            f"Spread {spread_bps:.2f} bps",
        ]

        if bullish and confidence >= 0.62:
            action: Action = "LONG"
        elif bearish and confidence >= 0.62:
            action = "SHORT"
        else:
            action = "FLAT"

        return Decision(
            action=action,
            confidence=confidence,
            regime=regime,
            regime_confidence=regime_confidence,
            execution_confidence=execution_confidence,
            reasons=reasons,
        )
