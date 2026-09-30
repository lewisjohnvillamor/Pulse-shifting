from __future__ import annotations

import math
from collections import OrderedDict

import numpy as np

from app import features as F
from app.modeling import FACTORS, MODEL_PATH, TimeframeModels
from app.strategy import Action, Decision, ParamSpec, Strategy


class AiRegimeFusionStrategy(Strategy):
    """Timeframe-aware factor model with fee-aware entries.

    Research (`python -m app.modeling eval`) showed the same features point
    in opposite directions on different timeframes: short candles
    (1m/5m) mean-revert, daily candles trend, 15m follows order flow. A
    single rule set cancels itself out, so this strategy loads one small,
    sign-stable factor model per timeframe (`app/models/fusion_model.json`)
    and picks the one trained nearest to the candle interval it is given.

    The model predicts the move over the next `horizon` candles. The
    strategy only trades when that predicted move beats the round-trip cost
    by `entry_cost_mult`, and abstains on timeframes whose model did not
    show positive out-of-sample predictive power (IC) in walk-forward tests.
    Each decision carries entry/stop/target levels for the chart.
    """

    id = "ai_regime_fusion"
    name = "AI Regime Fusion v3"
    description = (
        "Per-timeframe factor model (mean reversion on fast candles, momentum "
        "on slow ones, order flow in between) that trades only when the "
        "predicted move beats fees; draws entry/stop/target levels."
    )
    min_candles = F.WARMUP + 2
    max_lookback = 400
    styles = ["scalping", "day", "swing"]
    specs = [
        # Enter when predicted move >= entry_cost_mult x round-trip cost.
        ParamSpec("entry_cost_mult", "float", 1.0, 0.2, 5.0, 0.1, "Entry: move / cost"),
        # Hold while the prediction stays above exit_cost_mult x cost
        # (negative = hold until the model leans the other way).
        ParamSpec("exit_cost_mult", "float", -0.2, -3.0, 2.0, 0.1, "Exit: move / cost"),
        ParamSpec("stop_atr", "float", 2.0, 0.5, 6.0, 0.1, "Stop distance (x ATR)"),
        ParamSpec(
            "fee_bps", "float", 10.0, 0.0, 50.0, 0.5, "Taker fee per side (bps)",
            tunable=False,
        ),
        # Minimum out-of-sample IC a timeframe's model needs to be traded.
        ParamSpec(
            "min_oos_ic", "float", 0.005, -0.1, 0.2, 0.005, "Min model reliability (IC)"
        ),
        ParamSpec("allow_short", "int", 0, 0, 1, 1, "Allow SHORT signals (0/1)"),
    ]

    def __init__(self, params: dict[str, float] | None = None) -> None:
        super().__init__(params)
        self.models = TimeframeModels.load(MODEL_PATH)
        self._cache: OrderedDict[tuple, dict] = OrderedDict()

    # ------------------------------------------------------------------
    # Feature rows, cached so backtests compute each candle only once.

    @staticmethod
    def _key(candle: dict) -> tuple:
        return (
            int(candle["open_time"]),
            float(candle["close"]),
            float(candle["volume"]),
        )

    def prepare(self, candles: list[dict]) -> None:
        """Pre-compute feature rows for a whole series (used by backtests).
        Rows are identical to the ones a rolling window would produce."""
        if len(candles) < self.min_candles:
            return
        f = F.compute(candles)
        for i, c in enumerate(candles):
            self._remember(
                self._key(c),
                {"x": f["X"][i], "sigma": f["sigma"][i], "atr": f["atr"][i]},
            )

    def _remember(self, key: tuple, row: dict) -> None:
        self._cache[key] = row
        if len(self._cache) > 200_000:
            self._cache.popitem(last=False)

    def _row(self, candles: list[dict]) -> dict:
        key = self._key(candles[-1])
        row = self._cache.get(key)
        if row is None:
            f = F.compute(candles[-self.max_lookback :])
            row = {"x": f["X"][-1], "sigma": f["sigma"][-1], "atr": f["atr"][-1]}
            self._remember(key, row)
        return row

    # ------------------------------------------------------------------

    def _evaluate(self, candles: list[dict], spread_bps: float) -> dict:
        row = self._row(candles)
        tf, model = self.models.for_minutes(F.bar_minutes(candles))
        x = row["x"]
        price = float(candles[-1]["close"])
        sigma = float(row["sigma"]) if np.isfinite(row["sigma"]) else 0.0
        atr = float(row["atr"]) if np.isfinite(row["atr"]) else 0.0
        valid = bool(np.isfinite(x).all()) and sigma > 0

        z = float(model.predict(x[None, :])[0]) if valid else 0.0
        expected_bps = z * sigma * math.sqrt(model.horizon) * 10_000
        cost_bps = 2 * self.params["fee_bps"] + spread_bps
        oos_ic = float(model.spec.get("oos", {}).get("ic") or 0.0)

        contrib = model.contributions(x) if valid else {}
        return {
            "tf": tf,
            "horizon": model.horizon,
            "price": price,
            "atr": atr,
            "valid": valid,
            "z": z,
            "expected_bps": expected_bps,
            "cost_bps": cost_bps,
            "edge_mult": expected_bps / cost_bps if cost_bps else 0.0,
            "oos_ic": oos_ic,
            "reliable": oos_ic >= self.params["min_oos_ic"],
            "factors": {
                name: sum(contrib.get(m, 0.0) for m in members)
                for name, members in FACTORS.items()
            },
            "used": [
                k for k, v in model.spec.get("factors", {}).items() if v.get("used")
            ],
        }

    def edge_series(self, candles: list[dict]) -> list[float]:
        """Predicted move (bps over `horizon` candles) for every candle, NaN
        during warm-up. Used to mark the strongest setups on the chart."""
        if len(candles) < self.min_candles:
            return [float("nan")] * len(candles)
        f = F.compute(candles)
        _, model = self.models.for_minutes(F.bar_minutes(candles))
        pred = model.predict(f["X"])
        edge = pred * f["sigma"] * math.sqrt(model.horizon) * 10_000
        edge[np.isnan(f["X"]).any(axis=1)] = np.nan
        oos_ic = float(model.spec.get("oos", {}).get("ic") or 0.0)
        if oos_ic < self.params["min_oos_ic"]:
            edge[:] = np.nan  # no reliable edge on this timeframe
        return edge.tolist()

    def should_exit(
        self, candles: list[dict], spread_bps: float = 0.0, side: Action = "LONG"
    ) -> bool:
        if len(candles) < self.min_candles:
            return False
        f = self._evaluate(candles, spread_bps)
        level = self.params["exit_cost_mult"]
        if side == "LONG":
            return f["edge_mult"] < level
        return -f["edge_mult"] < level

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision(
                "FLAT", 0.0, "WARMUP", 0.0, 0.0,
                [f"Need at least {self.min_candles} candles"],
            )
        f = self._evaluate(candles, spread_bps)
        entry_mult = self.params["entry_cost_mult"]
        shorts_on = self.params["allow_short"] >= 0.5

        action: Action = "FLAT"
        if f["valid"] and f["reliable"]:
            if f["edge_mult"] >= entry_mult:
                action = "LONG"
            elif shorts_on and -f["edge_mult"] >= entry_mult:
                action = "SHORT"

        # Regime label = the factor currently driving the prediction.
        fx = f["factors"]
        driver = max(fx, key=lambda k: abs(fx[k])) if any(fx.values()) else ""
        trained = self.models.models[f["tf"]].spec.get("factors", {})
        if not f["reliable"] or not driver:
            regime = "NO_EDGE"
        elif driver == "flow":
            regime = "ORDER_FLOW"
        elif trained.get(driver, {}).get("ic", 0) < 0:
            regime = "MEAN_REVERSION"
        else:
            regime = "MOMENTUM"

        strength = min(abs(f["edge_mult"]) / max(entry_mult, 1e-6), 2.0) / 2.0
        reliability = max(0.0, min(f["oos_ic"] / 0.1, 1.0))
        execution_confidence = max(0.0, min(1.0, 1 - spread_bps / 12))
        confidence = min(0.95, 0.4 + 0.3 * strength + 0.25 * reliability)

        price, atr = f["price"], f["atr"]
        levels = None
        if f["valid"] and atr > 0:
            side = "LONG" if f["z"] >= 0 else "SHORT"
            sign = 1 if side == "LONG" else -1
            move = abs(f["expected_bps"]) / 10_000 * price
            # Target is never inside the break-even distance.
            min_move = f["cost_bps"] / 10_000 * price * 1.5
            levels = {
                "side": side,
                "active": action != "FLAT",
                "entry": round(price, 8),
                "stop": round(price - sign * self.params["stop_atr"] * atr, 8),
                "target": round(price + sign * max(move, min_move), 8),
                "horizon_candles": f["horizon"],
                "expected_move_bps": round(f["expected_bps"], 2),
                "cost_bps": round(f["cost_bps"], 2),
                "reliability_ic": round(f["oos_ic"], 4),
                "timeframe_model": f["tf"],
            }

        reasons = [
            f"{f['tf']} model · predicts {f['expected_bps']:+.1f} bps over "
            f"{f['horizon']} candles vs {f['cost_bps']:.1f} bps cost "
            f"({f['edge_mult']:+.2f}x, need {entry_mult:.1f}x)",
            f"Model reliability (out-of-sample IC) {f['oos_ic']:+.3f}"
            + ("" if f["reliable"] else " — too low, abstaining on this timeframe"),
            "Factors · "
            + " · ".join(
                f"{k} {fx[k]:+.3f}" + ("" if k in f["used"] else " (off)")
                for k in FACTORS
            ),
        ]
        if levels:
            reasons.append(
                f"Plan {levels['side']}: entry {levels['entry']:.6g} · stop "
                f"{levels['stop']:.6g} · target {levels['target']:.6g}"
            )
        if (
            action == "FLAT"
            and f["reliable"]
            and -f["edge_mult"] >= entry_mult
            and not shorts_on
        ):
            reasons.append("Bearish edge, but shorts are disabled (allow_short=0)")

        return Decision(
            action,
            confidence,
            regime,
            reliability,
            execution_confidence,
            reasons,
            levels,
        )


STRATEGY = AiRegimeFusionStrategy()
