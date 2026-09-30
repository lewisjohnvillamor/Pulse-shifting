from __future__ import annotations

import math

import numpy as np

from app import features as F
from app.strategy import Action, Decision, ParamSpec, Strategy


def _ema(values: np.ndarray, n: int) -> float:
    k = 2 / (n + 1)
    out = float(values[0])
    for v in values[1:]:
        out = float(v) * k + out * (1 - k)
    return out


class TrendVolTargetStrategy(Strategy):
    """Multi-horizon trend following with volatility-targeted sizing.

    Validated out of sample in `python -m app.alphalab trend` (20 coins,
    2020-09 → today, second half unseen, 12 bps cost per unit turnover):
    daily candles Sharpe 1.17 vs 0.74 equal-weight buy&hold and 1.04 BTC,
    max drawdown -24% vs -70% / -53%; stable across 9 parameter variants.
    On 4h candles the edge is smaller (Sharpe 0.45 vs 0.33); on 1h and below
    it did not beat buy&hold, so the strategy abstains there.

    Signal: average sign of EMA(fast) - EMA(slow) for (8,32), (16,64),
    (32,128) candles. Long when the average is positive; exit when it turns
    non-positive. Size: target_vol / realised volatility (capped at 100%),
    scaled by trend agreement, reported as `levels["size"]`.
    """

    id = "trend_vol_target"
    name = "Trend + Vol Target"
    description = (
        "Multi-horizon EMA trend with volatility-targeted position size. "
        "Validated on daily (strong) and 4h (modest) candles; abstains faster."
    )
    styles = ["day", "swing"]
    min_candles = 140
    max_lookback = 400
    PAIRS = ((8, 32), (16, 64), (32, 128))
    specs = [
        ParamSpec("target_vol", "float", 0.5, 0.1, 1.5, 0.05, "Target annual vol"),
        ParamSpec("vol_lookback", "int", 30, 10, 120, 1, "Vol look-back (candles)"),
        ParamSpec("stop_atr", "float", 3.0, 1.0, 8.0, 0.1, "Reference stop (x ATR)"),
        ParamSpec(
            "min_bar_minutes", "int", 240, 1, 1440, 1, "Shortest candle traded (min)"
        ),
    ]

    def _trend(self, closes: np.ndarray) -> float:
        votes = []
        for fast, slow in self.PAIRS:
            window = closes[-min(len(closes), 3 * slow) :]
            votes.append(np.sign(_ema(window, fast) - _ema(window, slow)))
        return float(np.mean(votes))

    def _state(self, candles: list[dict]) -> dict:
        closes = np.array([float(c["close"]) for c in candles])
        minutes = F.bar_minutes(candles)
        per_year = 365 * 1440 / minutes
        n = int(self.params["vol_lookback"])
        rets = np.diff(np.log(closes[-(n + 1) :]))
        vol = float(rets.std() * math.sqrt(per_year)) if len(rets) > 2 else 0.0
        trend = self._trend(closes)
        highs = np.array([float(c["high"]) for c in candles[-15:]])
        lows = np.array([float(c["low"]) for c in candles[-15:]])
        prev = closes[-16:-1]
        tr = np.maximum(highs - lows, np.maximum(abs(highs - prev), abs(lows - prev)))
        return {
            "price": float(closes[-1]),
            "trend": trend,
            "vol": vol,
            "atr": float(tr.mean()),
            "minutes": minutes,
            "tradable": minutes >= self.params["min_bar_minutes"],
        }

    def should_exit(
        self, candles: list[dict], spread_bps: float = 0.0, side: Action = "LONG"
    ) -> bool:
        if len(candles) < self.min_candles:
            return False
        trend = self._state(candles)["trend"]
        return trend <= 0 if side == "LONG" else trend >= 0

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision(
                "FLAT", 0.0, "WARMUP", 0.0, 0.0,
                [f"Need at least {self.min_candles} candles"],
            )
        s = self._state(candles)
        size = 0.0
        if s["vol"] > 0 and s["trend"] > 0:
            size = min(self.params["target_vol"] / s["vol"], 1.0) * s["trend"]
        action: Action = "LONG" if s["trend"] > 0 and s["tradable"] else "FLAT"
        regime = "UPTREND" if s["trend"] > 0 else "DOWNTREND" if s["trend"] < 0 else "MIXED"
        price, atr = s["price"], s["atr"]
        stop = price - self.params["stop_atr"] * atr
        levels = {
            "side": "LONG",
            "active": action == "LONG",
            "entry": round(price, 8),
            "stop": round(stop, 8),
            # Trend trades have no fixed target; 2R is shown as a reference.
            "target": round(price + 2 * (price - stop), 8),
            "size": round(size, 4),
            "timeframe_model": "trend",
        }
        reasons = [
            f"Trend agreement {s['trend']:+.2f} across EMA 8/32, 16/64, 32/128",
            f"Realised vol {s['vol'] * 100:.0f}%/yr → size {size * 100:.0f}% "
            f"of allocation (target {self.params['target_vol'] * 100:.0f}%)",
            "Exit when trend agreement turns ≤ 0; stop/target are references",
        ]
        if not s["tradable"]:
            reasons.append(
                f"{s['minutes']:.0f}m candles: no validated trend edge below "
                f"{self.params['min_bar_minutes']:.0f}m — abstaining"
            )
        return Decision(
            action,
            0.5 + 0.4 * max(s["trend"], 0),
            regime,
            abs(s["trend"]),
            max(0.0, min(1.0, 1 - spread_bps / 12)),
            reasons,
            levels,
        )


STRATEGY = TrendVolTargetStrategy()
