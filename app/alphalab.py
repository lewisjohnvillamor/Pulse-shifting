"""Alpha lab: out-of-sample tests of documented crypto effects.

    python -m app.alphalab fetch          # download/cache research data
    python -m app.alphalab trend          # H1 time-series trend + vol target
    python -m app.alphalab xsmom          # H2 cross-sectional momentum
    python -m app.alphalab seasonality    # H3 hour-of-day / weekday
    python -m app.alphalab leadlag        # H4 BTC -> altcoin lead-lag
    python -m app.alphalab funding        # H5 perp funding rate signals

Every test fixes its parameters up front (from published research) or
chooses them on the first half of the data only, and reports the second,
unseen half, net of trading costs. Data comes only from Binance public
sources: the spot REST API and the public data archive
(data.binance.vision) for perpetual funding history.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE = BASE_DIR / "data" / "research" / "alphalab"
SPOT_API = "https://data-api.binance.vision"
ARCHIVE = "https://data.binance.vision"

# Liquid coins listed on Binance spot since at least 2020 that still trade.
# Survivorship bias: coins delisted since then are missing, which flatters
# long-only and momentum results; treat absolute returns as optimistic.
UNIVERSE = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "TRXUSDT",
    "ATOMUSDT",
    "NEARUSDT",
    "UNIUSDT",
    "ETCUSDT",
    "XLMUSDT",
    "BCHUSDT",
    "FILUSDT",
    "AAVEUSDT",
]
START = "2020-09-01"  # every UNIVERSE coin trades on Binance spot by then

# Expanded universe (2026-10): top-70 USDT pairs by volume, excluding
# stablecoins and tokenized stocks, listed on Binance by 2023-03-31 (so
# there is history before the 2023-09 out-of-sample split), plus the
# original 20. Chosen by rule, not by performance; selecting by today's
# volume adds survivorship bias on top of the original universe's.
EXPANDED_UNIVERSE = list(
    dict.fromkeys(
        UNIVERSE
        + [
            "ZECUSDT",
            "QNTUSDT",
            "MOVRUSDT",
            "VTHOUSDT",
            "FETUSDT",
            "HBARUSDT",
            "ALICEUSDT",
            "GTCUSDT",
            "ARBUSDT",
            "SYNUSDT",
            "JASMYUSDT",
            "DASHUSDT",
            "INJUSDT",
            "OPUSDT",
            "STXUSDT",
            "APTUSDT",
            "ICPUSDT",
            "ARUSDT",
        ]
    )
)

DAY_MS = 86_400_000
INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "1d": DAY_MS,
}


def _ms(date: str) -> int:
    return int(
        datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        * 1000
    )


# --------------------------------------------------------------------------
# Data


def fetch_klines(
    symbol: str, interval: str, start: str, end_ms: int | None = None
) -> dict:
    """Paginated spot klines as column arrays, cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{interval}_{start}.npz"
    if path.exists():
        return dict(np.load(path))
    step = INTERVAL_MS[interval]
    t = _ms(start)
    stop = end_ms or int(time.time() * 1000) - step
    rows: list[list] = []
    with httpx.Client(base_url=SPOT_API, timeout=20) as client:
        while t < stop:
            for attempt in range(4):
                try:
                    r = client.get(
                        "/api/v3/klines",
                        params={
                            "symbol": symbol,
                            "interval": interval,
                            "startTime": t,
                            "limit": 1000,
                        },
                    )
                    r.raise_for_status()
                    page = r.json()
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2**attempt)
            if not page:
                break
            rows.extend(page)
            t = int(page[-1][0]) + step
            if len(page) < 1000:
                break
    rows = [r for r in rows if int(r[0]) <= stop]
    arr = {
        "t": np.array([int(r[0]) for r in rows], dtype=np.int64),
        "open": np.array([float(r[1]) for r in rows]),
        "high": np.array([float(r[2]) for r in rows]),
        "low": np.array([float(r[3]) for r in rows]),
        "close": np.array([float(r[4]) for r in rows]),
        "volume": np.array([float(r[5]) for r in rows]),
        "quote_volume": np.array([float(r[7]) for r in rows]),
        "taker_buy": np.array([float(r[9]) for r in rows]),
    }
    np.savez_compressed(path, **arr)
    return arr


def fetch_funding(symbol: str, start: str = START) -> dict:
    """USD-M perpetual funding history from the public data archive."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_funding.npz"
    if path.exists():
        return dict(np.load(path))
    y, m = map(int, start.split("-")[:2])
    now = datetime.now(timezone.utc)
    ts, rates = [], []
    with httpx.Client(base_url=ARCHIVE, timeout=30) as client:
        while (y, m) < (now.year, now.month):
            url = f"/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip"
            for attempt in range(4):
                try:
                    r = client.get(url)
                    break
                except httpx.HTTPError:
                    if attempt == 3:
                        raise
                    time.sleep(2**attempt)
            if r.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    text = z.read(z.namelist()[0]).decode()
                for row in csv.reader(io.StringIO(text)):
                    if not row or not row[0].isdigit():
                        continue
                    ts.append(int(row[0]))
                    rates.append(float(row[-1]))
            m += 1
            if m > 12:
                y, m = y + 1, 1
    order = np.argsort(ts)
    arr = {"t": np.array(ts, dtype=np.int64)[order], "rate": np.array(rates)[order]}
    np.savez_compressed(path, **arr)
    return arr


def daily_panel(symbols: list[str] = UNIVERSE, start: str = START) -> dict:
    """Aligned daily close/volume matrices (days x coins), NaN where missing."""
    data = {s: fetch_klines(s, "1d", start) for s in symbols}
    days = np.unique(np.concatenate([d["t"] for d in data.values()]))
    idx = {t: i for i, t in enumerate(days)}
    close = np.full((len(days), len(symbols)), np.nan)
    qvol = np.full_like(close, np.nan)
    for j, s in enumerate(symbols):
        rows = [idx[t] for t in data[s]["t"]]
        close[rows, j] = data[s]["close"]
        qvol[rows, j] = data[s]["quote_volume"]
    return {"t": days, "symbols": symbols, "close": close, "qvol": qvol}


# --------------------------------------------------------------------------
# Portfolio accounting shared by the tests


def run_weights(
    close: np.ndarray, weights: np.ndarray, cost_bps: float
) -> dict[str, np.ndarray | float]:
    """Daily (or per-bar) portfolio: `weights[t]` is held from close t to
    close t+1. Costs are charged on turnover |w_t - w_{t-1}|."""
    rets = np.nan_to_num(close[1:] / close[:-1] - 1)
    w = np.nan_to_num(weights[:-1])
    turnover = np.abs(np.diff(np.vstack([np.zeros(w.shape[1]), w]), axis=0)).sum(axis=1)
    pnl = (w * rets).sum(axis=1) - turnover * cost_bps / 10_000
    equity = np.cumprod(1 + pnl)
    return {"pnl": pnl, "equity": equity, "turnover": turnover}


def stats(pnl: np.ndarray, periods_per_year: float) -> dict[str, float]:
    if len(pnl) == 0:
        return {"cagr": 0.0, "vol": 0.0, "sharpe": 0.0, "max_dd": 0.0, "total": 0.0}
    equity = np.cumprod(1 + pnl)
    years = len(pnl) / periods_per_year
    peak = np.maximum.accumulate(equity)
    vol = pnl.std() * math.sqrt(periods_per_year)
    return {
        "total": round((equity[-1] - 1) * 100, 1),
        "cagr": round((equity[-1] ** (1 / years) - 1) * 100, 1)
        if years > 0 and equity[-1] > 0
        else -100.0,
        "vol": round(vol * 100, 1),
        "sharpe": round(pnl.mean() * periods_per_year / vol, 2) if vol else 0.0,
        "max_dd": round((equity / peak - 1).min() * 100, 1),
    }


def _fmt(name: str, s: dict) -> str:
    return (
        f"{name:<34} total {s['total']:+8.1f}%  CAGR {s['cagr']:+6.1f}%  "
        f"vol {s['vol']:5.1f}%  Sharpe {s['sharpe']:+5.2f}  maxDD {s['max_dd']:+6.1f}%"
    )


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 10 or a[m].std() == 0 or b[m].std() == 0:
        return float("nan")
    return float(np.corrcoef(a[m], b[m])[0, 1])


# --------------------------------------------------------------------------
# Signals


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """Column-wise EMA that skips leading NaNs (coins listed later)."""
    out = np.full_like(x, np.nan)
    k = 2 / (n + 1)
    for j in range(x.shape[1]):
        prev = np.nan
        for i, v in enumerate(x[:, j]):
            if np.isnan(v):
                continue
            prev = v if np.isnan(prev) else v * k + prev * (1 - k)
            out[i, j] = prev
    return out


def realized_vol(close: np.ndarray, n: int, per_year: float) -> np.ndarray:
    r = np.full_like(close, np.nan)
    r[1:] = np.log(close[1:] / close[:-1])
    out = np.full_like(close, np.nan)
    for i in range(n, len(close)):
        w = r[i - n + 1 : i + 1]
        out[i] = np.nanstd(w, axis=0) * math.sqrt(per_year)
    return out


# Pre-registered trend settings (multi-horizon EMA crossovers, as in the
# time-series momentum literature). Not tuned on this data.
TREND_PAIRS = ((8, 32), (16, 64), (32, 128))
VOL_LOOKBACK = 30
TARGET_VOL = 0.5  # annualised, per coin; no leverage (weight capped at 1)


def trend_signal(close: np.ndarray, pairs=TREND_PAIRS) -> np.ndarray:
    """Average sign of EMA(fast) - EMA(slow) over several horizons, in [-1, 1]."""
    parts = [np.sign(ema(close, f) - ema(close, s)) for f, s in pairs]
    sig = np.nanmean(np.stack(parts), axis=0)
    sig[np.isnan(close)] = np.nan
    return sig


def trend_weights(
    close: np.ndarray, target_vol: float = TARGET_VOL, pairs=TREND_PAIRS
) -> np.ndarray:
    """Long-only, vol-targeted trend weights, equal risk budget per coin."""
    sig = np.clip(trend_signal(close, pairs), 0, 1)
    vol = realized_vol(close, VOL_LOOKBACK, 365)
    scale = np.clip(target_vol / np.where(vol > 0, vol, np.nan), 0, 1)
    raw = np.nan_to_num(sig * scale)
    live = np.isfinite(close) & np.isfinite(vol)
    n_live = np.maximum(live.sum(axis=1, keepdims=True), 1)
    return raw / n_live


def equal_weight(close: np.ndarray) -> np.ndarray:
    live = np.isfinite(close).astype(float)
    return live / np.maximum(live.sum(axis=1, keepdims=True), 1)


def band(weights: np.ndarray, threshold: float) -> np.ndarray:
    """Only trade a coin when its target moves more than `threshold`
    (absolute weight) away from the current holding — cuts turnover."""
    out = weights.copy()
    for i in range(1, len(out)):
        keep = np.abs(weights[i] - out[i - 1]) < threshold
        out[i, keep] = out[i - 1, keep]
    return out


def xs_momentum_weights(
    close: np.ndarray,
    lookback: int = 21,
    top: int = 4,
    rebalance: int = 7,
    trend_filter: np.ndarray | None = None,
    long_short: bool = False,
) -> np.ndarray:
    """Weekly rotation into the `top` coins by past `lookback`-day return
    (3-week momentum, Liu-Tsyvinski-Wu). Optional absolute trend filter."""
    w = np.zeros_like(close)
    current = np.zeros(close.shape[1])
    for i in range(lookback, len(close)):
        if (i - lookback) % rebalance == 0:
            past = close[i] / close[i - lookback] - 1
            ok = np.isfinite(past)
            current = np.zeros(close.shape[1])
            if ok.sum() >= top * 2:
                order = np.argsort(np.where(ok, past, -np.inf))
                winners = [j for j in order[::-1][:top]]
                if trend_filter is not None:
                    winners = [j for j in winners if trend_filter[i, j] > 0]
                for j in winners:
                    current[j] = 1 / top
                if long_short:
                    losers = [j for j in order if ok[j]][:top]
                    for j in losers:
                        current[j] = -1 / top
        w[i] = current
    return w


# --------------------------------------------------------------------------
# Tests


def _halves(n: int) -> tuple[slice, slice]:
    mid = n // 2
    return slice(0, mid), slice(mid, n)


def _report(name: str, pnl: np.ndarray, t: np.ndarray, per_year: float) -> None:
    a, b = _halves(len(pnl))

    def day(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")

    print(_fmt(f"{name} [in {day(t[0])}..]", stats(pnl[a], per_year)))
    print(_fmt(f"{name} [OUT {day(t[len(t) // 2])}..]", stats(pnl[b], per_year)))


def cmd_trend(args: argparse.Namespace) -> None:
    p = daily_panel(EXPANDED_UNIVERSE if args.universe == "expanded" else UNIVERSE)
    close, t = p["close"], p["t"]
    cost = args.cost_bps
    print(
        f"H1 trend + vol target · {len(t)} days · {len(p['symbols'])} coins · cost {cost} bps/turnover\n"
    )
    ew = run_weights(close, equal_weight(close), cost)
    _report("Equal-weight buy&hold", ew["pnl"], t, 365)
    btc = run_weights(close[:, :1], np.ones((len(t), 1)), cost)
    _report("BTC buy&hold", btc["pnl"], t, 365)
    tw = trend_weights(close)
    for label, w in (
        ("Trend+vol target (daily)", tw),
        ("Trend+vol target (5% band)", band(tw, 0.05 / len(p["symbols"]))),
    ):
        r = run_weights(close, w, cost)
        _report(label, r["pnl"], t, 365)
        print(
            f"{'':34} avg exposure {np.nansum(w, axis=1).mean():.2f}, turnover/yr {r['turnover'].sum() / (len(t) / 365):.1f}x"
        )
    # BTC alone, same rules.
    bw = trend_weights(close[:, :1])
    _report("BTC trend+vol target", run_weights(close[:, :1], bw, cost)["pnl"], t, 365)
    # Robustness (reported, not used to pick): other horizons / targets.
    print("\nrobustness (full period Sharpe):")
    for pairs in (
        ((5, 20), (10, 40), (20, 80)),
        TREND_PAIRS,
        ((16, 64), (32, 128), (64, 256)),
    ):
        for tv in (0.3, 0.5, 0.8):
            r = run_weights(close, trend_weights(close, tv, pairs), cost)
            print(
                f"  pairs {pairs} target {tv:.1f}: Sharpe {stats(r['pnl'], 365)['sharpe']:+.2f}, maxDD {stats(r['pnl'], 365)['max_dd']:+.1f}%"
            )


def hourly_panel(symbols: list[str] = UNIVERSE, agg: int = 1) -> dict:
    data = {s: fetch_klines(s, "1h", "2022-01-01") for s in symbols}
    t0 = max(d["t"][0] for d in data.values())
    t1 = min(d["t"][-1] for d in data.values())
    grid = np.arange(t0, t1 + 1, 3_600_000)[::agg]
    close = np.full((len(grid), len(symbols)), np.nan)
    for j, s in enumerate(symbols):
        d = data[s]
        pos = np.searchsorted(d["t"], grid)
        pos = np.clip(pos, 0, len(d["t"]) - 1)
        hit = d["t"][pos] == grid
        close[hit, j] = d["close"][pos[hit]]
    # Forward-fill rare gaps.
    for j in range(close.shape[1]):
        for k in range(1, len(close)):
            if np.isnan(close[k, j]):
                close[k, j] = close[k - 1, j]
    return {"t": grid, "close": close}


def cmd_trend_tf(args: argparse.Namespace) -> None:
    """H1 on intraday candles: same EMA pairs counted in candles."""
    print(
        f"H1 on intraday candles (EMA pairs in candles) · cost {args.cost_bps} bps/turnover\n"
    )
    for label, agg in (("1h", 1), ("4h", 4)):
        p = hourly_panel(agg=agg)
        close, t = p["close"], p["t"]
        per_year = 365 * 24 / agg
        global VOL_LOOKBACK
        saved, VOL_LOOKBACK = VOL_LOOKBACK, 30 * 24 // agg
        try:
            vol = realized_vol(close, VOL_LOOKBACK, per_year)
            sig = np.clip(trend_signal(close), 0, 1)
            scale = np.clip(TARGET_VOL / np.where(vol > 0, vol, np.nan), 0, 1)
            w = np.nan_to_num(sig * scale) / close.shape[1]
        finally:
            VOL_LOOKBACK = saved
        _report(
            f"EW buy&hold {label}",
            run_weights(close, equal_weight(close), args.cost_bps)["pnl"],
            t,
            per_year,
        )
        r = run_weights(close, w, args.cost_bps)
        _report(f"Trend+vol {label}", r["pnl"], t, per_year)
        rb = run_weights(close, band(w, 0.05 / close.shape[1]), args.cost_bps)
        _report(f"Trend+vol {label} (band)", rb["pnl"], t, per_year)
        print(
            f"{'':34} turnover/yr {r['turnover'].sum() / (len(t) / per_year):.1f}x (banded {rb['turnover'].sum() / (len(t) / per_year):.1f}x)\n"
        )


def cmd_xsmom(args: argparse.Namespace) -> None:
    p = daily_panel()
    close, t = p["close"], p["t"]
    cost = args.cost_bps
    print(
        f"H2 cross-sectional momentum · weekly rebalance · cost {cost} bps/turnover\n"
    )
    _report(
        "Equal-weight buy&hold",
        run_weights(close, equal_weight(close), cost)["pnl"],
        t,
        365,
    )
    trend = trend_signal(close)
    for label, w in (
        ("Top-4 by 3w return", xs_momentum_weights(close)),
        (
            "Top-4 by 3w return + trend filter",
            xs_momentum_weights(close, trend_filter=trend),
        ),
        (
            "Long-short top/bottom 4 (research)",
            xs_momentum_weights(close, long_short=True),
        ),
        ("Trend+vol target (H1, reference)", trend_weights(close)),
    ):
        r = run_weights(close, w, cost)
        _report(label, r["pnl"], t, 365)
    # Does rank predict next-week return? Weekly cross-sectional IC.
    ics = []
    for i in range(21, len(close) - 7, 7):
        past = close[i] / close[i - 21] - 1
        fut = close[i + 7] / close[i] - 1
        ics.append(_corr(past, fut))
    ics = np.array(ics)
    a, b = _halves(len(ics))
    print(
        f"\nweekly cross-sectional rank IC: first half {np.nanmean(ics[a]):+.3f}, second half {np.nanmean(ics[b]):+.3f} "
        f"(share of weeks > 0: {np.nanmean(ics[b] > 0):.2f})"
    )


def cmd_seasonality(args: argparse.Namespace) -> None:
    rets, hours, wdays = [], [], []
    for s in UNIVERSE:
        h = fetch_klines(s, "1h", "2022-01-01")
        r = h["close"][1:] / h["close"][:-1] - 1
        ts = h["t"][1:]
        rets.append(r)
        hours.append((ts // 3_600_000) % 24)
        wdays.append(((ts // DAY_MS) + 3) % 7)  # 1970-01-01 was a Thursday -> 0=Mon
    # Time split on the timestamp axis (same cut for all coins).
    t_all = np.concatenate(
        [fetch_klines(s, "1h", "2022-01-01")["t"][1:] for s in UNIVERSE]
    )
    r_all = np.concatenate(rets)
    h_all = np.concatenate(hours)
    d_all = np.concatenate(wdays)
    cut = np.median(t_all)
    first, second = t_all < cut, t_all >= cut
    print("H3 seasonality (hourly returns, 20 coins, UTC)\n")
    hm = {
        k: np.array([r_all[m & (h_all == hh)].mean() * 1e4 for hh in range(24)])
        for k, m in (("A", first), ("B", second))
    }
    print("hour  first-half bps  second-half bps")
    for hh in range(24):
        print(f"  {hh:02d}   {hm['A'][hh]:+7.2f}        {hm['B'][hh]:+7.2f}")
    print(f"correlation of hourly means between halves: {_corr(hm['A'], hm['B']):+.2f}")
    dm = {
        k: np.array([r_all[m & (d_all == d)].mean() * 1e4 * 24 for d in range(7)])
        for k, m in (("A", first), ("B", second))
    }
    names = "Mon Tue Wed Thu Fri Sat Sun".split()
    print(
        "\nweekday (bps/day): "
        + " ".join(f"{n} {a:+.0f}/{b:+.0f}" for n, a, b in zip(names, dm["A"], dm["B"]))
    )
    print(
        f"correlation of weekday means between halves: {_corr(dm['A'], dm['B']):+.2f}"
    )
    # Pre-registered literature window: long 21:00-23:00 UTC only.
    for label, hrs in (
        ("21-23 UTC window (literature)", {21, 22}),
        ("hours positive in first half", set(np.where(hm["A"] > 0)[0])),
    ):
        m = second & np.isin(h_all, list(hrs))
        gross = r_all[m].mean() * 1e4 * len(hrs)
        print(
            f"\n{label}: second-half gross {gross:+.2f} bps/day per coin; one entry+exit/day costs {2 * args.cost_bps:.0f} bps"
        )


def cmd_leadlag(args: argparse.Namespace) -> None:
    alts = [
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "LINKUSDT",
    ]
    btc = fetch_klines("BTCUSDT", "1m", "2026-07-01")
    print("H4 BTC -> altcoin lead-lag (1m data from 2026-07-01)\n")
    print(
        f"{'bar':>4} {'IC first':>9} {'IC second':>10} {'alt own-lag IC':>15} {'|move| top decile bps':>22}"
    )
    for agg in (1, 5, 15, 60):
        rows = {"A": [], "B": [], "own": []}
        big = []
        for a in alts:
            alt = fetch_klines(a, "1m", "2026-07-01")
            common, ib, ia = np.intersect1d(btc["t"], alt["t"], return_indices=True)
            bc, ac = btc["close"][ib][::agg], alt["close"][ia][::agg]
            rb, ra = np.diff(np.log(bc)), np.diff(np.log(ac))
            x, y, own = rb[:-1], ra[1:], ra[:-1]
            mid = len(x) // 2
            rows["A"].append(_corr(x[:mid], y[:mid]))
            rows["B"].append(_corr(x[mid:], y[mid:]))
            rows["own"].append(_corr(own[mid:], y[mid:]))
            # Next-bar alt move after the biggest BTC moves (second half).
            xb, yb = x[mid:], y[mid:]
            cut = np.quantile(np.abs(xb), 0.9)
            sel = np.abs(xb) >= cut
            big.append(np.mean(np.sign(xb[sel]) * yb[sel]) * 1e4)
        print(
            f"{agg:>3}m {np.nanmean(rows['A']):+9.3f} {np.nanmean(rows['B']):+10.3f} {np.nanmean(rows['own']):+15.3f} {np.mean(big):+22.2f}"
        )
    print(f"\n(round-trip cost at taker fees: {2 * args.cost_bps:.0f} bps)")


def cmd_funding(args: argparse.Namespace) -> None:
    p = daily_panel()
    close, t, syms = p["close"], p["t"], p["symbols"]
    print("H5 perp funding -> spot returns (daily)\n")
    fund = np.full_like(close, np.nan)
    for j, s in enumerate(syms):
        f = fetch_funding(s)
        if not len(f["t"]):
            continue
        day = f["t"] // DAY_MS * DAY_MS
        for d in np.unique(day):
            i = np.searchsorted(t, d)
            if i < len(t) and t[i] == d:
                fund[i, j] = f["rate"][day == d].sum()  # daily funding paid by longs
    fut1 = np.full_like(close, np.nan)
    fut7 = np.full_like(close, np.nan)
    fut1[:-1] = close[1:] / close[:-1] - 1
    fut7[:-7] = close[7:] / close[:-7] - 1
    # Funding relative to its own 30-day history (crowding vs normal).
    z = np.full_like(fund, np.nan)
    for i in range(30, len(fund)):
        w = fund[i - 30 : i]
        z[i] = (fund[i] - np.nanmean(w, axis=0)) / np.where(
            np.nanstd(w, axis=0) > 0, np.nanstd(w, axis=0), np.nan
        )
    a, b = _halves(len(t))
    print(
        f"{'predictor':<28} {'horizon':>7} {'IC first':>9} {'IC second':>10} {'XS IC second':>13}"
    )
    for name, x in (("funding level", fund), ("funding z (30d)", z)):
        for hname, y in (("1d", fut1), ("7d", fut7)):
            ia = _corr(x[a].ravel(), y[a].ravel())
            ib = _corr(x[b].ravel(), y[b].ravel())
            xs = np.nanmean([_corr(x[i], y[i]) for i in range(b.start, b.stop)])
            print(f"{name:<28} {hname:>7} {ia:+9.3f} {ib:+10.3f} {xs:+13.3f}")
    # Pre-registered rule: skip (go flat on) coins whose funding z > 2 — the
    # crowded-long condition — inside the H1 trend portfolio.
    tw = trend_weights(close)
    crowded = np.nan_to_num(z) > 2
    tw2 = np.where(crowded, 0.0, tw)
    _report(
        "H1 trend (reference)", run_weights(close, tw, args.cost_bps)["pnl"], t, 365
    )
    _report(
        "H1 trend, flat when funding z>2",
        run_weights(close, tw2, args.cost_bps)["pnl"],
        t,
        365,
    )


def cmd_gbm(args: argparse.Namespace) -> None:
    """H6: gradient-boosted trees vs the shipped sign-stable factor model,
    same per-timeframe data and the same 60/40 chronological split."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    from . import modeling as M

    frames = M.market_frames(M.SYMBOLS, M.INTERVALS)
    horizon, frac = 12, 0.6
    fac = M.evaluate(frames, horizon, 0.0, frac, "factor")["test"]
    print(
        "H6 gradient boosting vs factor model (out-of-sample IC, 12-candle horizon)\n"
    )
    print(
        f"{'tf':>4} {'factor IC':>10} {'GBM IC':>8} {'GBM+hour IC':>12} {'blend IC':>9}"
    )
    for tf in M.INTERVALS:
        tr, te = [], []
        for (_, i), fr in frames.items():
            if i != tf:
                continue
            sp = M.split_point(fr, frac)
            hours = ((fr["open_time"] // 3_600_000) % 24).astype(float)
            ang = 2 * np.pi * hours / 24
            extra = np.column_stack([np.sin(ang), np.cos(ang)])
            fr_x = np.column_stack([fr["X"], extra])
            y = M.F.forward_target(fr["logc"], fr["sigma"], horizon)
            idx = np.arange(len(y))
            ok = np.isfinite(y) & (np.isnan(fr["X"]).sum(1) == 0) & (idx >= M.F.WARMUP)
            tr.append((fr_x[ok & (idx < sp - horizon)], y[ok & (idx < sp - horizon)]))
            te.append((fr_x[ok & (idx >= sp)], y[ok & (idx >= sp)]))
        Xtr = np.vstack([a for a, _ in tr])
        ytr = np.concatenate([b for _, b in tr])
        Xte = np.vstack([a for a, _ in te])
        yte = np.concatenate([b for _, b in te])
        base = dict(
            max_iter=200,
            learning_rate=0.05,
            max_leaf_nodes=15,
            min_samples_leaf=200,
            l2_regularization=1.0,
            random_state=0,
        )
        g = HistGradientBoostingRegressor(**base).fit(Xtr[:, :-2], ytr)
        gh = HistGradientBoostingRegressor(**base).fit(Xtr, ytr)
        p_g = g.predict(Xte[:, :-2])
        p_gh = gh.predict(Xte)
        spec = M.evaluate(
            {k: v for k, v in frames.items() if k[1] == tf},
            horizon,
            0.0,
            frac,
            "factor",
        )["specs"].get(tf)
        p_f = M.LinearModel(spec).predict(Xte[:, :-2]) if spec else np.zeros(len(yte))
        blend = (p_f / (p_f.std() or 1)) + (p_g / (p_g.std() or 1))
        print(
            f"{tf:>4} {fac.get(tf, {}).get('ic', float('nan')):+10.3f} {_corr(p_g, yte):+8.3f} "
            f"{_corr(p_gh, yte):+12.3f} {_corr(blend, yte):+9.3f}"
        )


# --------------------------------------------------------------------------
# CLI


def cmd_fetch(_: argparse.Namespace) -> None:
    for s in UNIVERSE:
        d = fetch_klines(s, "1d", START)
        h = fetch_klines(s, "1h", "2022-01-01")
        print(f"{s}: {len(d['t'])} days, {len(h['t'])} hours", flush=True)
    for s in [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "LINKUSDT",
    ]:
        m1 = fetch_klines(s, "1m", "2026-07-01")
        print(f"{s}: {len(m1['t'])} 1m candles", flush=True)
    for s in UNIVERSE:
        f = fetch_funding(s)
        print(f"{s}: {len(f['t'])} funding prints", flush=True)


COMMANDS = {
    "fetch": cmd_fetch,
    "trend": cmd_trend,
    "xsmom": cmd_xsmom,
    "seasonality": cmd_seasonality,
    "leadlag": cmd_leadlag,
    "funding": cmd_funding,
    "gbm": cmd_gbm,
    "trend_tf": cmd_trend_tf,
}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="PulseShift alpha lab")
    ap.add_argument("command", choices=sorted(COMMANDS))
    ap.add_argument(
        "--cost-bps", type=float, default=10.0, help="cost per unit turnover"
    )
    ap.add_argument("--universe", choices=["core", "expanded"], default="core")
    args = ap.parse_args(argv)
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
