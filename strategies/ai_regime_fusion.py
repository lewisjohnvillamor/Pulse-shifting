from __future__ import annotations

from statistics import mean

from app.strategy import Action, Decision, ParamSpec, Strategy, atr_pct, ema, rsi


class AiRegimeFusionStrategy(Strategy):
    """AI-designed deterministic ensemble.

    This is deliberately not presented as a trained model. It is a transparent
    meta-policy that combines multiple independent signals, changes weights by
    detected market regime, penalizes disagreement, and abstains when the edge
    is weak. The same feature vector can later be used to train/optimize the
    weights out-of-sample.
    """

    id = "ai_regime_fusion"
    name = "AI Regime Fusion v2"
    description = (
        "Adaptive ensemble of trend, momentum, VWAP reversion, breakout, "
        "volume and volatility signals with regime-aware weighting and abstention."
    )
    min_candles = 80
    styles = ["scalping", "day", "swing"]
    specs = [
        ParamSpec("entry_threshold", "float", 0.3, 0.15, 0.8, 0.01, "Entry score"),
        ParamSpec("min_consensus", "float", 0.64, 0.5, 0.9, 0.01, "Minimum consensus"),
        ParamSpec("trend_fast", "int", 12, 5, 30, 1, "Fast EMA"),
        ParamSpec("trend_slow", "int", 77, 20, 120, 1, "Slow EMA"),
        ParamSpec("vwap_period", "int", 67, 20, 160, 1, "VWAP window"),
        ParamSpec("breakout_period", "int", 46, 10, 80, 1, "Breakout window"),
        ParamSpec("trend_regime_threshold", "float", 0.37, 0.15, 0.75, 0.01, "Trend regime threshold"),
        ParamSpec("high_vol_threshold", "float", 0.002, 0.002, 0.02, 0.001, "High-vol threshold"),
        ParamSpec("tr_trend", "float", 0.39, 0.02, 0.8, 0.01, "Trending · trend weight"),
        ParamSpec("tr_momentum", "float", 0.29, 0.02, 0.8, 0.01, "Trending · momentum weight"),
        ParamSpec("tr_reversion", "float", 0.04, 0.0, 0.6, 0.01, "Trending · reversion weight"),
        ParamSpec("tr_breakout", "float", 0.38, 0.02, 0.8, 0.01, "Trending · breakout weight"),
        ParamSpec("rg_trend", "float", 0.19, 0.0, 0.6, 0.01, "Ranging · trend weight"),
        ParamSpec("rg_momentum", "float", 0.26, 0.0, 0.6, 0.01, "Ranging · momentum weight"),
        ParamSpec("rg_reversion", "float", 0.77, 0.05, 0.9, 0.01, "Ranging · reversion weight"),
        ParamSpec("rg_breakout", "float", 0.31, 0.0, 0.7, 0.01, "Ranging · breakout weight"),
        ParamSpec("hv_trend", "float", 0.16, 0.0, 0.7, 0.01, "High vol · trend weight"),
        ParamSpec("hv_momentum", "float", 0.35, 0.0, 0.7, 0.01, "High vol · momentum weight"),
        ParamSpec("hv_reversion", "float", 0.1, 0.0, 0.7, 0.01, "High vol · reversion weight"),
        ParamSpec("hv_breakout", "float", 0.55, 0.05, 0.9, 0.01, "High vol · breakout weight"),
        # Hysteresis: once long, hold until the score falls below this level
        # instead of exiting the moment it dips under the entry threshold.
        ParamSpec("exit_threshold", "float", -0.35, -0.6, 0.4, 0.01, "Exit score (hold above)"),
        # Cost filter: only enter when ATR over the expected holding horizon
        # covers `min_edge_multiple` round trips of fees + spread.
        ParamSpec("fee_bps", "float", 10.0, 0.0, 50.0, 0.5, "Taker fee per side (bps)", tunable=False),
        ParamSpec("edge_horizon", "int", 15, 1, 60, 1, "Expected hold (candles)"),
        ParamSpec("min_edge_multiple", "float", 0.1, 0.0, 6.0, 0.1, "Min move / round-trip cost"),
        # Backtests showed SHORT calls were anti-predictive (dips tended to
        # bounce), so shorts are opt-in.
        ParamSpec("allow_short", "int", 0, 0, 1, 1, "Allow SHORT signals (0/1)"),
    ]

    @staticmethod
    def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    def _evaluate(self, candles: list[dict], spread_bps: float) -> dict:
        """Compute the fusion score and its ingredients for the last candle."""

        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        volumes = [float(c["volume"]) for c in candles]
        price = closes[-1]

        fast_n = int(self.params["trend_fast"])
        slow_n = int(self.params["trend_slow"])
        # Seed both EMAs over the same, longer history so the slow one is
        # warmed up rather than anchored to its first sample.
        ema_window = closes[-min(len(closes), 3 * max(slow_n, fast_n)):]
        fast = ema(ema_window, fast_n)
        slow = ema(ema_window, slow_n)

        # 1) Trend: normalized EMA separation.
        trend_score = self._clamp((fast / slow - 1) * 240 if slow else 0.0)

        # 2) Momentum: short-horizon return, capped so one candle cannot dominate.
        ret5 = price / closes[-6] - 1
        momentum_score = self._clamp(ret5 * 120)

        # 3) VWAP reversion: negative above VWAP, positive below VWAP.
        vwap_n = int(self.params["vwap_period"])
        vwap_window = candles[-vwap_n:]
        vol_sum = sum(float(c["volume"]) for c in vwap_window)
        vwap = (
            sum(float(c["close"]) * float(c["volume"]) for c in vwap_window) / vol_sum
            if vol_sum else price
        )
        atr_abs = atr_pct(candles) * price
        vwap_dev_atr = (price - vwap) / atr_abs if atr_abs else 0.0
        reversion_score = self._clamp(-vwap_dev_atr / 2.0)

        # RSI contributes to reversion confirmation.
        rsi_value = rsi(closes, 14)
        rsi_score = self._clamp((50.0 - rsi_value) / 30.0)
        reversion_score = self._clamp(0.65 * reversion_score + 0.35 * rsi_score)

        # 4) Breakout: position relative to the prior rolling high/low.
        breakout_n = int(self.params["breakout_period"])
        prior_high = max(highs[-breakout_n - 1:-1])
        prior_low = min(lows[-breakout_n - 1:-1])
        if price > prior_high:
            breakout_score = self._clamp((price / prior_high - 1) * 400 + 0.35)
        elif price < prior_low:
            breakout_score = self._clamp(-((prior_low / price - 1) * 400 + 0.35))
        else:
            midpoint = (prior_high + prior_low) / 2
            half_range = max((prior_high - prior_low) / 2, 1e-12)
            breakout_score = self._clamp((price - midpoint) / half_range * 0.35)

        # 5) Volume confirms directional moves, but is not directional alone.
        avg_volume = mean(volumes[-21:-1]) or 1.0
        volume_ratio = volumes[-1] / avg_volume
        volume_quality = max(0.0, min(1.0, (volume_ratio - 0.7) / 1.3))

        # 6) Regime classification from trend separation and realized volatility.
        recent_returns = [
            closes[i] / closes[i - 1] - 1
            for i in range(max(1, len(closes) - 30), len(closes))
            if closes[i - 1]
        ]
        realized_vol = (
            (sum(r * r for r in recent_returns) / len(recent_returns)) ** 0.5
            if recent_returns else 0.0
        )
        trend_strength = abs(trend_score)

        def regime_weights(prefix: str) -> dict[str, float]:
            raw = {
                "trend": self.params[f"{prefix}_trend"],
                "momentum": self.params[f"{prefix}_momentum"],
                "reversion": self.params[f"{prefix}_reversion"],
                "breakout": self.params[f"{prefix}_breakout"],
            }
            total = sum(raw.values()) or 1.0
            return {key: value / total for key, value in raw.items()}

        if trend_strength >= self.params["trend_regime_threshold"]:
            regime = "TRENDING"
            weights = regime_weights("tr")
        elif realized_vol >= self.params["high_vol_threshold"]:
            regime = "HIGH_VOL"
            weights = regime_weights("hv")
        else:
            regime = "RANGING"
            weights = regime_weights("rg")

        components = {
            "trend": trend_score,
            "momentum": momentum_score,
            "reversion": reversion_score,
            "breakout": breakout_score,
        }
        raw_score = sum(weights[k] * components[k] for k in components)

        # Consensus: fraction of weighted components agreeing with the composite.
        direction = 1 if raw_score > 0 else -1 if raw_score < 0 else 0
        agreeing_weight = sum(
            weights[k]
            for k, value in components.items()
            if direction and value * direction > 0
        )
        consensus = agreeing_weight / sum(weights.values())

        # Penalize weak liquidity / expensive execution.
        execution_confidence = max(0.0, min(1.0, 1 - spread_bps / 12))
        score = raw_score * (0.75 + 0.25 * volume_quality)
        score *= 0.65 + 0.35 * execution_confidence

        # Cost filter: expected move over the holding horizon vs round trip.
        atrp = atr_abs / price if price else 0.0
        expected_move_bps = atrp * self.params["edge_horizon"] ** 0.5 * 10_000
        round_trip_bps = 2 * self.params["fee_bps"] + spread_bps
        edge_multiple = (
            expected_move_bps / round_trip_bps if round_trip_bps else float("inf")
        )

        return {
            "score": score,
            "consensus": consensus,
            "regime": regime,
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "reversion_score": reversion_score,
            "breakout_score": breakout_score,
            "volume_ratio": volume_ratio,
            "rsi": rsi_value,
            "trend_strength": trend_strength,
            "realized_vol": realized_vol,
            "execution_confidence": execution_confidence,
            "edge_multiple": edge_multiple,
        }

    def _warming_up(self, candles: list[dict]) -> bool:
        return len(candles) < self.min_candles

    def should_exit(
        self, candles: list[dict], spread_bps: float = 0.0, side: Action = "LONG"
    ) -> bool:
        if self._warming_up(candles):
            return False
        score = self._evaluate(candles, spread_bps)["score"]
        exit_level = self.params["exit_threshold"]
        if side == "LONG":
            return score < exit_level
        return score > -exit_level

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if self._warming_up(candles):
            return Decision(
                "FLAT", 0.0, "UNKNOWN", 0.0, 0.0,
                [f"Need at least {self.min_candles} candles"],
            )

        f = self._evaluate(candles, spread_bps)
        score = f["score"]
        consensus = f["consensus"]
        execution_confidence = f["execution_confidence"]
        threshold = self.params["entry_threshold"]
        min_consensus = self.params["min_consensus"]
        exit_level = self.params["exit_threshold"]
        edge_ok = f["edge_multiple"] >= self.params["min_edge_multiple"]
        shorts_on = self.params["allow_short"] >= 0.5

        if score >= threshold and consensus >= min_consensus and edge_ok:
            action: Action = "LONG"
        elif (
            shorts_on
            and score <= -threshold
            and consensus >= min_consensus
            and edge_ok
        ):
            action = "SHORT"
        else:
            action = "FLAT"

        # Confidence is deliberately tied to edge magnitude + agreement, not
        # called a calibrated probability until backtests prove calibration.
        edge_strength = min(abs(score) / max(threshold, 1e-6), 1.5) / 1.5
        confidence = min(
            0.99,
            0.35
            + 0.30 * edge_strength
            + 0.22 * consensus
            + 0.13 * execution_confidence,
        )
        regime_confidence = min(
            0.95,
            0.55
            + 0.25 * f["trend_strength"]
            + 0.15 * min(f["realized_vol"] / 0.01, 1.0),
        )

        reasons = [
            f"Fusion score {score:+.3f} (entry ±{threshold:.2f}, hold long above {exit_level:+.2f})",
            f"Consensus {consensus * 100:.0f}%",
            f"Trend {f['trend_score']:+.2f} · momentum {f['momentum_score']:+.2f}",
            f"Reversion {f['reversion_score']:+.2f} · breakout {f['breakout_score']:+.2f}",
            f"Volume {f['volume_ratio']:.2f}x · RSI {f['rsi']:.1f}",
            (
                f"Expected move {f['edge_multiple']:.1f}x round-trip cost"
                f" (need {self.params['min_edge_multiple']:.1f}x)"
            ),
            f"Regime {f['regime']} · spread {spread_bps:.2f} bps",
        ]
        if action == "FLAT" and not edge_ok and abs(score) >= threshold:
            reasons.append("Signal present but too small to beat fees — abstaining")
        if action == "FLAT" and not shorts_on and score <= -threshold:
            reasons.append("Bearish score, but shorts are disabled (allow_short=0)")

        return Decision(
            action,
            confidence,
            f["regime"],
            regime_confidence,
            execution_confidence,
            reasons,
        )

STRATEGY = AiRegimeFusionStrategy()
