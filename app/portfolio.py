"""Trend + volatility-target portfolio (the validated H1 rule).

Out-of-sample result (`python -m app.alphalab trend`, 20 coins, second half
2023-09 onward, 12 bps per unit turnover): Sharpe 1.17 vs 0.74 for
equal-weight buy&hold and 1.04 for BTC; max drawdown -24% vs -70% / -53%.
This module applies exactly that rule to the latest daily candles and
returns today's target weights.
"""

from __future__ import annotations

import math

import numpy as np

from .alphalab import (
    TARGET_VOL,
    TREND_PAIRS,
    VOL_LOOKBACK,
    equal_weight,
    realized_vol,
    run_weights,
    stats,
    trend_signal,
    trend_weights,
)

VALIDATION = {
    "test": "python -m app.alphalab trend",
    "period_out_of_sample": "2023-09-16 onward",
    "sharpe": 1.17,
    "sharpe_equal_weight": 0.74,
    "sharpe_btc": 1.04,
    "max_dd_pct": -23.7,
    "max_dd_equal_weight_pct": -70.4,
    "max_dd_btc_pct": -53.0,
}


def build(candles_by_symbol: dict[str, list[dict]], cost_bps: float = 12.0) -> dict:
    symbols = [s for s, c in candles_by_symbol.items() if len(c) >= 140]
    days = sorted({int(c["open_time"]) for s in symbols for c in candles_by_symbol[s]})
    idx = {t: i for i, t in enumerate(days)}
    close = np.full((len(days), len(symbols)), np.nan)
    for j, s in enumerate(symbols):
        for c in candles_by_symbol[s]:
            close[idx[int(c["open_time"])], j] = float(c["close"])

    weights = trend_weights(close)
    signal = trend_signal(close)
    vol = realized_vol(close, VOL_LOOKBACK, 365)
    last = len(days) - 1
    rows = []
    for j, s in enumerate(symbols):
        v = float(vol[last, j]) if np.isfinite(vol[last, j]) else None
        sig = float(signal[last, j]) if np.isfinite(signal[last, j]) else 0.0
        rows.append(
            {
                "symbol": s,
                "price": float(close[last, j]),
                "trend": round(sig, 3),
                "vol_pct": round(v * 100, 1) if v else None,
                "weight_pct": round(float(weights[last, j]) * 100, 2),
                "action": "HOLD" if weights[last, j] > 0 else "OUT",
            }
        )
    rows.sort(key=lambda r: -r["weight_pct"])

    port = run_weights(close, weights, cost_bps)
    bench = run_weights(close, equal_weight(close), cost_bps)
    years = len(days) / 365
    return {
        "as_of": days[-1],
        "rule": {
            "trend_pairs_days": TREND_PAIRS,
            "target_vol": TARGET_VOL,
            "vol_lookback_days": VOL_LOOKBACK,
            "rebalance": "daily",
            "cost_bps": cost_bps,
        },
        "holdings": rows,
        "exposure_pct": round(float(np.nansum(weights[last])) * 100, 1),
        "window": {
            "days": len(days),
            "years": round(years, 2),
            "strategy": stats(port["pnl"], 365),
            "equal_weight": stats(bench["pnl"], 365),
            "note": "In-window replay (includes data used for validation).",
        },
        "validation": VALIDATION,
        "annualised_turnover": round(
            float(port["turnover"].sum()) / max(years, 1e-9), 1
        )
        if not math.isnan(float(port["turnover"].sum()))
        else None,
    }
