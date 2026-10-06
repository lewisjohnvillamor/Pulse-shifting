"""Example strategy plugin — copy it into `strategies/` to try it.

    cp examples/strategies/rsi_dip.py strategies/
    # then restart the API, or: curl -X POST localhost:8000/api/strategies/reload

It appears on the Strategy board, in the chart's backtest selector, in the
param editor (its `specs`) and in `python -m app.research --strategy rsi_dip`.

Rules (deliberately simple, for learning):
* LONG when RSI(14) is oversold *and* price is above its 100-candle average
  (buy dips inside an uptrend);
* exit when RSI recovers above `exit_rsi`, or price closes below the stop.
"""

from __future__ import annotations

from statistics import mean

from app.strategy import Action, Decision, ParamSpec, Strategy, atr_pct, rsi


class RsiDipStrategy(Strategy):
    id = "rsi_dip"
    name = "RSI Dip (example)"
    description = "Buys RSI dips inside an uptrend; exits on RSI recovery."
    styles = ["day", "swing"]
    min_candles = 110  # needs 100 candles for the trend filter
    max_lookback = 200  # backtests only pass the window we actually read
    specs = [
        ParamSpec("entry_rsi", "float", 30, 10, 45, 1, "Enter below RSI"),
        ParamSpec("exit_rsi", "float", 55, 45, 80, 1, "Exit above RSI"),
        ParamSpec("trend_len", "int", 100, 20, 200, 1, "Trend average (candles)"),
        ParamSpec("stop_atr", "float", 2.5, 0.5, 6.0, 0.1, "Stop (x ATR)"),
    ]

    def _state(self, candles: list[dict]) -> dict:
        closes = [float(c["close"]) for c in candles]
        price = closes[-1]
        trend = mean(closes[-int(self.params["trend_len"]) :])
        atr = atr_pct(candles) * price
        return {"price": price, "rsi": rsi(closes, 14), "trend": trend, "atr": atr}

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision("FLAT", 0.0, "WARMUP", 0.0, 0.0, ["Warming up"])
        s = self._state(candles)
        uptrend = s["price"] > s["trend"]
        oversold = s["rsi"] < self.params["entry_rsi"]
        action: Action = "LONG" if uptrend and oversold else "FLAT"
        stop = s["price"] - self.params["stop_atr"] * s["atr"]
        return Decision(
            action,
            confidence=0.5 + 0.4 * max(0.0, (self.params["entry_rsi"] - s["rsi"]) / 30),
            regime="UPTREND" if uptrend else "DOWNTREND",
            regime_confidence=0.6,
            execution_confidence=max(0.0, 1 - spread_bps / 12),
            reasons=[
                f"RSI {s['rsi']:.1f} (enter < {self.params['entry_rsi']:.0f})",
                f"Price {'above' if uptrend else 'below'} "
                f"{self.params['trend_len']:.0f}-candle average",
            ],
            # `levels` are drawn on the chart (AI/plan lines). `size` (0-1)
            # would make backtests invest only part of the equity.
            levels={
                "side": "LONG",
                "active": action == "LONG",
                "entry": round(s["price"], 8),
                "stop": round(stop, 8),
                "target": round(s["price"] + 2 * (s["price"] - stop), 8),
            },
        )

    def should_exit(
        self, candles: list[dict], spread_bps: float = 0.0, side: Action = "LONG"
    ) -> bool:
        # Called by backtests while a position is open: hold until RSI has
        # recovered, instead of exiting the moment the entry signal fades.
        s = self._state(candles)
        return s["rsi"] > self.params["exit_rsi"] or s["price"] < s["trend"] * 0.97


STRATEGY = RsiDipStrategy()
