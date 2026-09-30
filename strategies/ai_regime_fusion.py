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
    name = "AI Regime Fusion v1"
    description = (
        "Adaptive ensemble of trend, momentum, VWAP reversion, breakout, "
        "volume and volatility signals with regime-aware weighting and abstention."
    )
    min_candles = 80
    styles = ["scalping", "day", "swing"]
    specs = [
        ParamSpec("entry_threshold", "float", 0.38, 0.15, 0.8, 0.01, "Entry score"),
        ParamSpec("min_consensus", "float", 0.56, 0.5, 0.9, 0.01, "Minimum consensus"),
        ParamSpec("trend_fast", "int", 9, 5, 30, 1, "Fast EMA"),
        ParamSpec("trend_slow", "int", 34, 20, 120, 1, "Slow EMA"),
        ParamSpec("vwap_period", "int", 48, 20, 160, 1, "VWAP window"),
        ParamSpec("breakout_period", "int", 24, 10, 80, 1, "Breakout window"),
    ]

    @staticmethod
    def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    def decide(self, candles: list[dict], spread_bps: float = 0.0) -> Decision:
        if len(candles) < self.min_candles:
            return Decision(
                "FLAT", 0.0, "UNKNOWN", 0.0, 0.0,
                [f"Need at least {self.min_candles} candles"],
            )

        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        volumes = [float(c["volume"]) for c in candles]
        price = closes[-1]

        fast_n = int(self.params["trend_fast"])
        slow_n = int(self.params["trend_slow"])
        fast = ema(closes[-max(slow_n, fast_n):], fast_n)
        slow = ema(closes[-slow_n:], slow_n)

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

        if trend_strength >= 0.35:
            regime = "TRENDING"
            weights = {
                "trend": 0.38,
                "momentum": 0.27,
                "reversion": 0.08,
                "breakout": 0.27,
            }
        elif realized_vol >= 0.006:
            regime = "HIGH_VOL"
            weights = {
                "trend": 0.22,
                "momentum": 0.23,
                "reversion": 0.15,
                "breakout": 0.40,
            }
        else:
            regime = "RANGING"
            weights = {
                "trend": 0.12,
                "momentum": 0.13,
                "reversion": 0.55,
                "breakout": 0.20,
            }

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

        threshold = self.params["entry_threshold"]
        min_consensus = self.params["min_consensus"]

        if score >= threshold and consensus >= min_consensus:
            action: Action = "LONG"
        elif score <= -threshold and consensus >= min_consensus:
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
            0.55 + 0.25 * trend_strength + 0.15 * min(realized_vol / 0.01, 1.0),
        )

        reasons = [
            f"Fusion score {score:+.3f} (entry ±{threshold:.2f})",
            f"Consensus {consensus * 100:.0f}%",
            f"Trend {trend_score:+.2f} · momentum {momentum_score:+.2f}",
            f"Reversion {reversion_score:+.2f} · breakout {breakout_score:+.2f}",
            f"Volume {volume_ratio:.2f}x · RSI {rsi_value:.1f}",
            f"Regime {regime} · spread {spread_bps:.2f} bps",
        ]

        return Decision(
            action,
            confidence,
            regime,
            regime_confidence,
            execution_confidence,
            reasons,
        )


STRATEGY = AiRegimeFusionStrategy()
