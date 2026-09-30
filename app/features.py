"""Volatility-normalised market features for the predictive model.

Every feature is dimensionless (returns in units of recent volatility,
distances in ATRs, ratios, bounded oscillators) so one model can be shared
across symbols and timeframes. Every feature uses only fixed-length rolling
windows — no recursive EMAs — so the value for a candle is identical whether
it is computed from a 400-candle live window or a 5000-candle backtest.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

MOMENTUM_HORIZONS = (1, 3, 6, 12, 24, 48, 96, 192)
SMA_PERIODS = (20, 50, 100, 200)
VOL_WINDOW = 48
LONG_VOL_WINDOW = 192
# Candles of history needed before the first fully-defined feature row.
WARMUP = 260

FEATURE_NAMES: list[str] = (
    [f"mom_{k}" for k in MOMENTUM_HORIZONS]
    + [f"sma_dist_{n}" for n in SMA_PERIODS]
    + [
        "rsi_14",
        "vwap_dev_48",
        "range_pos_24",
        "range_pos_96",
        "vol_regime",
        "atr_level",
        "volume_z",
        "taker_imb_1",
        "taker_imb_6",
        "taker_imb_24",
        "close_loc_3",
        "log_bar_minutes",
    ]
)


def _roll_mean(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    if len(x) < n:
        return out
    c = np.cumsum(np.insert(np.nan_to_num(x), 0, 0.0))
    out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def _roll_max(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    if len(x) >= n:
        out[n - 1 :] = sliding_window_view(x, n).max(axis=1)
    return out


def _roll_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    if len(x) >= n:
        out[n - 1 :] = sliding_window_view(x, n).min(axis=1)
    return out


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def bar_minutes(candles: list[dict]) -> float:
    if len(candles) < 2:
        return 1.0
    step = int(candles[-1]["open_time"]) - int(candles[-2]["open_time"])
    return max(step / 60_000, 1.0)


def arrays(candles: list[dict]) -> dict[str, np.ndarray]:
    close = np.array([float(c["close"]) for c in candles])
    volume = np.array([float(c["volume"]) for c in candles])
    taker = np.array(
        [float(c.get("taker_buy_volume", float(c["volume"]) / 2)) for c in candles]
    )
    return {
        "open_time": np.array([int(c["open_time"]) for c in candles]),
        "high": np.array([float(c["high"]) for c in candles]),
        "low": np.array([float(c["low"]) for c in candles]),
        "close": close,
        "volume": volume,
        "taker": taker,
    }


def compute(candles: list[dict]) -> dict[str, np.ndarray]:
    """Feature matrix (n x F, NaN during warm-up) plus helper series."""
    a = arrays(candles)
    close, high, low, vol, taker = (
        a["close"],
        a["high"],
        a["low"],
        a["volume"],
        a["taker"],
    )
    n = len(close)
    logc = np.log(close)
    ret1 = np.full(n, np.nan)
    ret1[1:] = np.diff(logc)

    sigma = np.sqrt(_roll_mean(ret1**2, VOL_WINDOW))
    sigma_long = np.sqrt(_roll_mean(ret1**2, LONG_VOL_WINDOW))
    prev_close = _lag(close, 1)
    tr = np.fmax(
        high - low,
        np.fmax(np.abs(high - prev_close), np.abs(low - prev_close)),
    )
    atr = _roll_mean(tr, 14)
    safe_sigma = np.where(sigma > 0, sigma, np.nan)
    safe_atr = np.where(atr > 0, atr, np.nan)

    cols: list[np.ndarray] = []
    for k in MOMENTUM_HORIZONS:
        cols.append((logc - _lag(logc, k)) / (safe_sigma * math.sqrt(k)))
    for p in SMA_PERIODS:
        cols.append((close - _roll_mean(close, p)) / safe_atr)

    gain = np.where(ret1 > 0, ret1, 0.0)
    loss = np.where(ret1 < 0, -ret1, 0.0)
    avg_gain, avg_loss = _roll_mean(gain, 14), _roll_mean(loss, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = np.where(avg_loss > 0, 100 - 100 / (1 + avg_gain / avg_loss), 100.0)
    cols.append((rsi - 50) / 50)

    vwap = _roll_mean(close * vol, 48) / np.where(
        _roll_mean(vol, 48) > 0, _roll_mean(vol, 48), np.nan
    )
    cols.append((close - vwap) / safe_atr)

    for w in (24, 96):
        hh, ll = _roll_max(high, w), _roll_min(low, w)
        half = np.where(hh > ll, (hh - ll) / 2, np.nan)
        cols.append((close - (hh + ll) / 2) / half)

    cols.append(np.log(safe_sigma / np.where(sigma_long > 0, sigma_long, np.nan)))
    cols.append(np.log(safe_atr / close))
    avg_vol = _roll_mean(vol, 48)
    cols.append(np.log((vol + 1e-12) / np.where(avg_vol > 0, avg_vol, np.nan)))

    imbalance = np.where(vol > 0, 2 * taker / np.where(vol > 0, vol, 1) - 1, 0.0)
    for w in (1, 6, 24):
        cols.append(_roll_mean(imbalance, w))

    rng = high - low
    clv = np.where(
        rng > 0, ((close - low) - (high - close)) / np.where(rng > 0, rng, 1), 0
    )
    cols.append(_roll_mean(clv, 3))
    cols.append(np.full(n, math.log(bar_minutes(candles))))

    X = np.column_stack(cols)
    X[~np.isfinite(X)] = np.nan
    return {
        "X": X,
        "sigma": sigma,
        "atr": atr,
        "close": close,
        "logc": logc,
        "open_time": a["open_time"],
    }


def forward_target(logc: np.ndarray, sigma: np.ndarray, horizon: int) -> np.ndarray:
    """Forward log return over `horizon` candles in units of sigma*sqrt(h),
    clipped so single crashes do not dominate the fit."""
    y = np.full(logc.shape, np.nan)
    if len(logc) > horizon:
        y[:-horizon] = (logc[horizon:] - logc[:-horizon]) / (
            sigma[:-horizon] * math.sqrt(horizon)
        )
    return np.clip(y, -4, 4)
