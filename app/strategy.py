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


@dataclass
class ParamSpec:
    name: str
    kind: str  # "float" | "int"
    default: float
    min: float
    max: float
    step: float = 0.0
    label: str = ""

    def as_dict(self, value: float) -> dict:
        return {**self.__dict__, "value": value}


class Strategy:
    """Base protocol for strategies. Subclasses define `specs` (editable
    params), `min_candles`, and implement `decide`."""

    id = "base"
    name = "Base strategy"
    description = ""
    min_candles = 30
    specs: list[ParamSpec] = []

    def __init__(self, params: dict[str, float] | None = None) -> None:
        self.params = {spec.name: spec.default for spec in self.specs}
        for key, value in (params or {}).items():
            spec = next((s for s in self.specs if s.name == key), None)
            if spec is not None:
                try:
                    self.params[key] = min(max(float(value), spec.min), spec.max)
                except (TypeError, ValueError):
                    pass

    def configure(self, params: dict[str, float]) -> list[dict]:
        for key, value in params.items():
            spec = next((s for s in self.specs if s.name == key), None)
            if spec is not None:
                try:
                    self.params[key] = min(max(float(value), spec.min), spec.max)
                except (TypeError, ValueError):
                    continue
        return self.param_values()

    def param_values(self) -> list[dict]:
        return [spec.as_dict(self.params[spec.name]) for spec in self.specs]

    def decide(
        self, candles: list[dict], spread_bps: float = 0.0
    ) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * k + result * (1 - k)
    return result


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(-period, 0):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def atr_pct(candles: list[dict], period: int = 14) -> float:
    """Average true range as a fraction of price — a cheap volatility gauge."""
    if len(candles) < period + 1:
        return 0.0
    ranges = []
    for i in range(-period, 0):
        high, low = float(candles[i]["high"]), float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    price = float(candles[-1]["close"])
    return mean(ranges) / price if price else 0.0


class EmaMomentumStrategy(Strategy):
    id = "ema_momentum"
    name = "EMA Momentum v1"
    description = "Long when EMA9 leads EMA21 with positive 5-candle momentum and supportive volume; flat otherwise."
    specs = [
        ParamSpec("fast", "int", 9, 3, 30, 1, "Fast EMA"),
        ParamSpec("slow", "int", 21, 8, 60, 1, "Slow EMA"),
        ParamSpec("lookback", "int", 5, 2, 20, 1, "Momentum candles"),
        ParamSpec("threshold", "float", 0.62, 0.5, 0.9, 0.01, "Signal threshold"),
    ]

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision(
                "FLAT", 0.0, "UNKNOWN", 0.0, 0.0, ["Need at least 30 candles"]
            )

        closes = [float(c["close"]) for c in candles]
        volumes = [float(c["volume"]) for c in candles]
        fast_p = int(self.params["fast"])
        slow_p = int(self.params["slow"])
        lookback = int(self.params["lookback"])
        threshold = self.params["threshold"]

        window = closes[-max(30, slow_p + lookback) :]
        fast = ema(window, fast_p)
        slow = ema(window, slow_p)
        latest = closes[-1]
        ref = closes[-(lookback + 1)] if len(closes) > lookback else closes[0]
        ret = (latest / ref - 1) if ref else 0.0
        avg_volume = mean(volumes[-21:-1]) or 1.0
        volume_ratio = volumes[-1] / avg_volume
        trend_gap = (fast / slow - 1) if slow else 0.0

        trend_strength = min(abs(trend_gap) * 3500, 1.0)
        momentum_strength = min(abs(ret) * 500, 1.0)
        volume_strength = min(max((volume_ratio - 0.7) / 1.3, 0.0), 1.0)
        execution_confidence = max(0.0, min(1.0, 1 - spread_bps / 12))

        regime = "TRENDING" if trend_strength > 0.18 else "RANGING"
        regime_confidence = 0.55 + min(trend_strength * 0.4, 0.4)

        bullish = fast > slow and ret > 0
        bearish = fast < slow and ret < 0

        raw_score = (
            0.35 * trend_strength
            + 0.30 * momentum_strength
            + 0.20 * volume_strength
            + 0.15 * execution_confidence
        )
        confidence = max(0.0, min(0.99, 0.45 + raw_score * 0.5))

        reasons = [
            f"EMA{fast_p} {'above' if fast > slow else 'below'} EMA{slow_p}",
            f"{lookback}-candle momentum {ret * 100:+.2f}%",
            f"Volume {volume_ratio:.2f}x recent average",
            f"Spread {spread_bps:.2f} bps",
        ]

        if bullish and confidence >= threshold:
            action: Action = "LONG"
        elif bearish and confidence >= threshold:
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


class MeanReversionStrategy(Strategy):
    id = "mean_reversion"
    name = "RSI Mean Reversion"
    description = "Fades stretched moves: long near the lower Bollinger band when RSI is oversold, shorts/flat at overbought."
    min_candles = 40
    specs = [
        ParamSpec("rsi_period", "int", 14, 5, 30, 1, "RSI period"),
        ParamSpec("band_period", "int", 20, 10, 60, 1, "Band period"),
        ParamSpec("band_width", "float", 2.0, 1.0, 3.5, 0.1, "Band width (σ)"),
        ParamSpec("oversold", "float", 30.0, 10.0, 45.0, 1.0, "Oversold RSI"),
        ParamSpec("overbought", "float", 70.0, 55.0, 90.0, 1.0, "Overbought RSI"),
    ]

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision(
                "FLAT", 0.0, "UNKNOWN", 0.0, 0.0, ["Need at least 40 candles"]
            )

        closes = [float(c["close"]) for c in candles]
        band_p = int(self.params["band_period"])
        width = self.params["band_width"]
        level = rsi(closes, int(self.params["rsi_period"]))
        basis = mean(closes[-band_p:])
        dev = stdev(closes[-band_p:])
        upper, lower = basis + width * dev, basis - width * dev
        latest = closes[-1]
        position_in_band = (latest - lower) / (upper - lower) if upper > lower else 0.5

        oversold = latest <= lower or level <= self.params["oversold"]
        overbought = latest >= upper or level >= self.params["overbought"]
        stretch = min(abs(position_in_band - 0.5) * 2, 1.0)
        execution_confidence = max(0.0, min(1.0, 1 - spread_bps / 12))
        confidence = max(
            0.0, min(0.99, 0.45 + 0.4 * stretch + 0.15 * execution_confidence)
        )

        regime = "RANGING" if dev / basis < 0.02 else "VOLATILE"
        reasons = [
            f"RSI({int(self.params['rsi_period'])}) {level:.0f}",
            f"Price {position_in_band * 100:.0f}% of band",
            f"Spread {spread_bps:.2f} bps",
        ]

        if oversold:
            action: Action = "LONG"
        elif overbought:
            action = "SHORT"
        else:
            action = "FLAT"

        return Decision(action, confidence, regime, 0.7, execution_confidence, reasons)


class BreakoutStrategy(Strategy):
    id = "breakout"
    name = "Channel Breakout"
    description = "Long on a close above the N-candle high with volume confirmation; flat inside the channel."
    min_candles = 30
    specs = [
        ParamSpec("channel", "int", 20, 10, 60, 1, "Channel length"),
        ParamSpec("volume_mult", "float", 1.2, 0.8, 3.0, 0.1, "Min volume × avg"),
    ]

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision(
                "FLAT", 0.0, "UNKNOWN", 0.0, 0.0, ["Need at least 30 candles"]
            )

        channel = int(self.params["channel"])
        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        volumes = [float(c["volume"]) for c in candles]

        latest = closes[-1]
        channel_high = max(highs[-channel - 1 : -1])
        channel_low = min(lows[-channel - 1 : -1])
        avg_volume = mean(volumes[-channel:-1]) or 1.0
        volume_ratio = volumes[-1] / avg_volume
        vol_ok = volume_ratio >= self.params["volume_mult"]

        broke_up = latest > channel_high
        broke_down = latest < channel_low
        strength = min(
            abs(latest - channel_high) / channel_high * 800
            if broke_up
            else abs(latest - channel_low) / channel_low * 800
            if broke_down
            else 0.0,
            1.0,
        )
        execution_confidence = max(0.0, min(1.0, 1 - spread_bps / 12))
        confidence = max(
            0.0, min(0.99, 0.5 + 0.3 * strength + 0.2 * execution_confidence)
        )

        reasons = [
            f"Channel {channel_low:.4g}–{channel_high:.4g}",
            f"Volume {volume_ratio:.2f}x (need {self.params['volume_mult']:.1f}x)",
            f"Spread {spread_bps:.2f} bps",
        ]

        if broke_up and vol_ok:
            action: Action = "LONG"
        elif broke_down and vol_ok:
            action = "SHORT"
        else:
            action = "FLAT"

        return Decision(
            action,
            confidence,
            "BREAKOUT" if (broke_up or broke_down) else "RANGING",
            0.6,
            execution_confidence,
            reasons,
        )
