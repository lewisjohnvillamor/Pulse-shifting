from __future__ import annotations

from statistics import mean
from typing import Any

from .strategy import atr_pct


def _slope(p1: tuple[int, float], p2: tuple[int, float]) -> float:
    di = p2[0] - p1[0]
    return (p2[1] - p1[1]) / di if di else 0.0


def swing_points(candles: list[dict], span: int = 3) -> list[tuple[int, float, str]]:
    """Local extrema: (index, price, 'high'|'low')."""
    out: list[tuple[int, float, str]] = []
    for i in range(span, len(candles) - span):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        if all(
            high >= float(candles[i + j]["high"]) for j in range(-span, span + 1) if j
        ):
            out.append((i, high, "high"))
        if all(
            low <= float(candles[i + j]["low"]) for j in range(-span, span + 1) if j
        ):
            out.append((i, low, "low"))
    return out


def _ts(candle: dict) -> int:
    return int(candle["open_time"]) // 1000


def _pattern(
    name: str,
    bias: str,
    candles: list[dict],
    points: list[tuple[int, float]],
    entry: float,
    stop: float,
    target: float,
    confidence: float,
    note: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "bias": bias,
        "points": [[_ts(candles[i]), price] for i, price in points],
        "entry": round(entry, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "confidence": round(confidence, 3),
        "note": note,
    }


def detect_patterns(candles: list[dict], lookback: int = 120) -> dict[str, Any]:
    """Heuristic classic-pattern recognition over the candle window.

    Returns overlay geometry (polylines in chart time), support/resistance
    levels, and a projected entry/stop/target per pattern — geometry is
    approximate; treat calls as candidates, not signals.
    """
    candles = candles[-lookback:]
    if len(candles) < 40:
        return {"patterns": [], "levels": []}

    price = float(candles[-1]["close"])
    atr = atr_pct(candles) * price or price * 0.005
    swings = swing_points(candles, 3)
    highs = [(i, p) for i, p, k in swings if k == "high"]
    lows = [(i, p) for i, p, k in swings if k == "low"]

    patterns: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []

    # Support / resistance: clusters of recent swings within 0.4% of each other.
    for kind, pts in (("resistance", highs), ("support", lows)):
        for i, p in pts[-8:]:
            if abs(p - price) / price < 0.002:
                continue
            touches = sum(1 for _, q in pts if abs(q - p) / p < 0.006)
            if touches >= 2 and all(abs(l["price"] - p) / p > 0.001 for l in levels):
                levels.append(
                    {
                        "kind": kind,
                        "price": round(p, 8),
                        "touches": touches,
                        "t0": _ts(candles[0]),
                        "t1": _ts(candles[-1]),
                    }
                )
        if len(levels) >= 4:
            break

    # Double top / bottom.
    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        valley = min(
            (p for i, p, k in swings if k == "low" and i1 < i < i2), default=None
        )
        if (
            i2 - i1 >= 5
            and abs(p1 - p2) / p1 < 0.015
            and valley is not None
            and (p1 - valley) / p1 > 0.002
            and price < p2
        ):
            height = p1 - valley
            patterns.append(
                _pattern(
                    "Double top",
                    "bearish",
                    candles,
                    [(i1, p1), (i2, p2)],
                    entry=valley,
                    stop=max(p1, p2) + atr * 0.3,
                    target=valley - height,
                    confidence=0.6,
                    note="Sell below the neckline between the twin peaks.",
                )
            )
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        peak = max(
            (p for i, p, k in swings if k == "high" and i1 < i < i2), default=None
        )
        if (
            i2 - i1 >= 5
            and abs(p1 - p2) / p1 < 0.015
            and peak is not None
            and (peak - p1) / p1 > 0.002
            and price > p2
        ):
            height = peak - p1
            patterns.append(
                _pattern(
                    "Double bottom",
                    "bullish",
                    candles,
                    [(i1, p1), (i2, p2)],
                    entry=peak,
                    stop=min(p1, p2) - atr * 0.3,
                    target=peak + height,
                    confidence=0.6,
                    note="Buy the break of the intervening peak.",
                )
            )

    # Head & shoulders (top): three swing highs, middle tallest, shoulders similar.
    if len(highs) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = highs[-3:]
        head = max(p1, p2, p3)
        if (
            p2 == head
            and abs(p1 - p3) / p2 < 0.3
            and (p2 - p1) / p2 > 0.0015
            and (p2 - p3) / p2 > 0.0015
        ):
            nl = [
                p for i, p, k in swings if k == "low" and (i1 < i < i2 or i2 < i < i3)
            ]
            if nl:
                neckline = mean(nl)
                height = head - neckline
                if price < p3 or price < head:
                    patterns.append(
                        _pattern(
                            "Head & shoulders top",
                            "bearish",
                            candles,
                            [(i1, p1), (i2, p2), (i3, p3)],
                            entry=neckline,
                            stop=p3 + atr * 0.3,
                            target=neckline - height,
                            confidence=0.65,
                            note="Sell the neckline break; target = head height below it.",
                        )
                    )

    # Triangles: fit slopes over the last ~half of swings.
    recent_highs = highs[-4:]
    recent_lows = lows[-4:]
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        hs = _slope(recent_highs[0], recent_highs[-1])
        ls = _slope(recent_lows[0], recent_lows[-1])
        scale = atr or price * 0.01
        flat = scale * 0.4
        name = bias = None
        if abs(hs) < flat and ls > flat:
            name, bias = "Ascending triangle", "bullish"
        elif hs < -flat and abs(ls) < flat:
            name, bias = "Descending triangle", "bearish"
        elif hs < -flat and ls > flat:
            name, bias = "Symmetrical triangle", "neutral"
        if name:
            top = max(p for _, p in recent_highs)
            bottom = min(p for _, p in recent_lows)
            height = top - bottom
            bull = bias == "bullish"
            entry = top if bias != "bearish" else bottom
            target = entry + height if bull else entry - height
            patterns.append(
                _pattern(
                    name,
                    bias,
                    candles,
                    recent_highs[:2] + recent_lows[:2],
                    entry=entry,
                    stop=bottom if bull else top,
                    target=target,
                    confidence=0.55,
                    note=f"Trade the {bias if bias != 'neutral' else 'breakout'} side of the converging range.",
                )
            )

    # Range: both sides flat — price oscillating in a rectangle.
    if (
        not patterns
        and len(recent_highs) >= 2
        and len(recent_lows) >= 2
        and abs(hs) < flat
        and abs(ls) < flat
    ):
        top = max(p for _, p in recent_highs)
        bottom = min(p for _, p in recent_lows)
        if (top - bottom) / price > 0.001:
            patterns.append(
                _pattern(
                    "Range",
                    "neutral",
                    candles,
                    recent_highs[:2] + recent_lows[:2],
                    entry=top,
                    stop=bottom,
                    target=top + (top - bottom),
                    confidence=0.5,
                    note="Ranging market — trade range edges or wait for the break.",
                )
            )

    # Flag: strong impulse followed by a shallow counter-drift channel.
    if len(candles) >= 20:
        closes = [float(c["close"]) for c in candles]
        impulse = closes[-12] - closes[-24]
        drift = closes[-1] - closes[-12]
        if abs(impulse) > 2.5 * atr and abs(drift) < abs(impulse) * 0.5:
            bull = impulse > 0
            if (bull and drift < 0) or (not bull and drift > 0):
                flag_high = max(float(c["high"]) for c in candles[-12:])
                flag_low = min(float(c["low"]) for c in candles[-12:])
                height = abs(impulse)
                entry = flag_high if bull else flag_low
                patterns.append(
                    _pattern(
                        "Bull flag" if bull else "Bear flag",
                        "bullish" if bull else "bearish",
                        candles,
                        [(len(candles) - 12, flag_high), (len(candles) - 1, flag_high)]
                        + [(len(candles) - 12, flag_low), (len(candles) - 1, flag_low)],
                        entry=entry,
                        stop=flag_low if bull else flag_high,
                        target=entry + height if bull else entry - height,
                        confidence=0.55,
                        note="Continuation: enter on break of the flag channel.",
                    )
                )

    return {"patterns": patterns[:4], "levels": levels[:5]}
