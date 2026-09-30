"""Train and evaluate the shared predictive model behind AI Regime Fusion.

    python -m app.modeling eval             # walk-forward predictive power
    python -m app.modeling train            # fit on all data, write model

The model predicts the forward return over `horizon` candles in units of
recent volatility. It is pooled across symbols and timeframes (features are
volatility-normalised), fitted on the oldest part of every market and scored
on the newest part it never saw. "Predictive power" is reported as:

* IC — correlation between prediction and realised forward return;
* hit rate — how often the predicted direction was right, overall and for
  the most confident 20% of predictions;
* top/bottom quintile forward return in bps, to compare against fees.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from . import features as F
from .research import load_candles

MODEL_PATH = Path(__file__).resolve().parent / "models" / "fusion_model.json"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]
# Faster timeframes need more candles to cover a comparable span of regimes.
DEFAULT_BARS = {
    "1m": 20000,
    "5m": 15000,
    "15m": 10000,
    "1h": 8000,
    "4h": 5000,
    "1d": 3000,
}
INTERVAL_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}


# --------------------------------------------------------------------------
# Linear model (ridge) — small, transparent, and exact to reproduce in JSON.


class LinearModel:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.mean = np.array(spec["mean"])
        self.std = np.array(spec["std"])
        self.coef = np.array(spec["coef"])
        self.intercept = float(spec["intercept"])
        self.horizon = int(spec["horizon"])
        self.clip = float(spec.get("clip", 5.0))

    def predict(self, X: np.ndarray) -> np.ndarray:
        Z = np.clip((X - self.mean) / self.std, -self.clip, self.clip)
        Z = np.nan_to_num(Z)
        return Z @ self.coef + self.intercept

    def contributions(self, x: np.ndarray) -> dict[str, float]:
        z = np.nan_to_num(np.clip((x - self.mean) / self.std, -self.clip, self.clip))
        return dict(zip(self.spec["features"], (z * self.coef).tolist()))


class TimeframeModels:
    """One LinearModel per trained timeframe. Short timeframes mean-revert and
    long ones trend in this data, so a single pooled model cancels itself
    out; each candle interval uses the model trained nearest to it."""

    def __init__(self, bundle: dict[str, Any]) -> None:
        self.bundle = bundle
        self.models = {tf: LinearModel(spec) for tf, spec in bundle["models"].items()}

    def for_minutes(self, minutes: float) -> tuple[str, LinearModel]:
        tf = min(
            self.models,
            key=lambda k: abs(
                math.log(INTERVAL_MINUTES[k]) - math.log(max(minutes, 1))
            ),
        )
        return tf, self.models[tf]

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> TimeframeModels:
        return cls(json.loads(path.read_text()))


def fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float, horizon: int) -> dict:
    mean = np.nanmean(X, axis=0)
    std = np.nanstd(X, axis=0)
    std[std == 0] = 1.0
    Z = np.nan_to_num(np.clip((X - mean) / std, -5, 5))
    ym = y.mean()
    A = Z.T @ Z + alpha * len(Z) * np.eye(Z.shape[1])
    coef = np.linalg.solve(A, Z.T @ (y - ym))
    return {
        "kind": "ridge",
        "features": F.FEATURE_NAMES,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "coef": coef.tolist(),
        "intercept": float(ym),
        "horizon": horizon,
        "clip": 5.0,
        "alpha": alpha,
    }


# Composite factors: each is the mean z-score of its member features.
FACTORS: dict[str, tuple[str, ...]] = {
    "stretch": ("rsi_14", "sma_dist_20", "range_pos_24", "vwap_dev_48"),
    "trend": ("mom_12", "mom_24", "mom_48", "mom_96", "sma_dist_100", "sma_dist_200"),
    "flow": ("taker_imb_6", "taker_imb_24"),
}


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def fit_factor_model(
    parts: list[tuple[np.ndarray, np.ndarray]],
    horizon: int,
    min_agree: int = 5,
    min_ic: float = 0.01,
) -> dict:
    """Sign-stable factor model. `parts` holds one (X, y) per symbol.

    A factor is kept only if its correlation with the forward return has the
    same sign as the pooled correlation for at least `min_agree` symbols and
    the pooled |IC| >= `min_ic` — all measured on training data only. Kept
    factors are weighted by their pooled IC, which makes the prediction a
    rough regression estimate in target (sigma) units.
    """
    X = np.vstack([p[0] for p in parts])
    y = np.concatenate([p[1] for p in parts])
    mean = np.nanmean(X, axis=0)
    std = np.nanstd(X, axis=0)
    std[std == 0] = 1.0
    idx = {n: i for i, n in enumerate(F.FEATURE_NAMES)}

    def factor(Xp: np.ndarray, members: tuple[str, ...]) -> np.ndarray:
        cols = [idx[m] for m in members]
        Z = np.nan_to_num(np.clip((Xp[:, cols] - mean[cols]) / std[cols], -5, 5))
        return Z.mean(axis=1)

    coef = np.zeros(len(F.FEATURE_NAMES))
    kept: dict[str, dict] = {}
    need = min(min_agree, len(parts))
    for name, members in FACTORS.items():
        f = factor(X, members)
        f_std = float(f.std()) or 1.0
        ic = _corr(f, y)
        per_symbol = [_corr(factor(Xp, members), yp) for Xp, yp in parts]
        agree = sum(1 for v in per_symbol if np.sign(v) == np.sign(ic))
        # Temporal stability: same sign in the older and newer half of every
        # symbol's training rows (pooled), not just on average.
        halves = [
            _corr(
                np.concatenate([factor(Xp[sl(len(yp))], members) for Xp, yp in parts]),
                np.concatenate([yp[sl(len(yp))] for _, yp in parts]),
            )
            for sl in (lambda n: slice(0, n // 2), lambda n: slice(n // 2, n))
        ]
        same_in_time = all(np.sign(h) == np.sign(ic) for h in halves)
        stable = agree >= need and abs(ic) >= min_ic and same_in_time
        kept[name] = {
            "ic": round(ic, 4),
            "agree": agree,
            "of": len(parts),
            "halves": [round(h, 4) for h in halves],
            "used": stable,
        }
        if stable:
            for m in members:
                coef[idx[m]] += ic / (len(members) * f_std)
    return {
        "kind": "factor",
        "features": F.FEATURE_NAMES,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "coef": coef.tolist(),
        "intercept": 0.0,
        "horizon": horizon,
        "clip": 5.0,
        "factors": kept,
    }


# --------------------------------------------------------------------------
# Data


def market_frames(
    symbols: list[str], intervals: list[str], bars: int | None = None
) -> dict[tuple[str, str], dict]:
    frames = {}
    for s in symbols:
        for i in intervals:
            candles = load_candles(s, i, bars or DEFAULT_BARS.get(i, 5000))
            if len(candles) < F.WARMUP + 500:
                continue
            frames[(s, i)] = F.compute(candles)
    return frames


def rows(frame: dict, horizon: int, lo: int, hi: int):
    """Usable (X, y, sigma) rows with candle index in [lo, hi)."""
    y = F.forward_target(frame["logc"], frame["sigma"], horizon)
    X = frame["X"]
    idx = np.arange(len(y))
    ok = (
        (idx >= max(lo, F.WARMUP))
        & (idx < hi)
        & np.isfinite(y)
        & np.isfinite(frame["sigma"])
        & (np.isnan(X).sum(axis=1) == 0)
    )
    return X[ok], y[ok], frame["sigma"][ok]


def split_point(frame: dict, train_frac: float) -> int:
    return int(len(frame["logc"]) * train_frac)


# --------------------------------------------------------------------------
# Evaluation


def score(pred: np.ndarray, y: np.ndarray, sigma: np.ndarray, horizon: int) -> dict:
    fwd_bps = y * sigma * math.sqrt(horizon) * 1e4
    ic = _corr(pred, y)
    # Hit rate over rows where the model takes a side at all.
    side = pred != 0
    hit = float(np.mean(np.sign(pred[side]) == np.sign(y[side]))) if side.any() else 0.0
    q = max(len(pred) // 5, 1)
    order = np.argsort(pred)
    top, bottom = order[-q:], order[:q]
    conf = np.argsort(-np.abs(pred))[:q]
    return {
        "n": int(len(y)),
        "ic": round(ic, 4),
        "hit_rate": round(hit, 4),
        "hit_rate_top20": round(
            float(np.mean(np.sign(pred[conf]) == np.sign(y[conf]))), 4
        )
        if side.any()
        else 0.0,
        "top_q_bps": round(float(fwd_bps[top].mean()), 2),
        "bottom_q_bps": round(float(fwd_bps[bottom].mean()), 2),
    }


def _train_parts(frames: dict, interval: str, horizon: int, hi_frac: float):
    parts = []
    for (_, i), fr in frames.items():
        if i != interval:
            continue
        hi = split_point(fr, hi_frac) - horizon if hi_frac < 1 else len(fr["logc"])
        X, y, _ = rows(fr, horizon, 0, hi)
        if len(y) >= 100:
            parts.append((X, y))
    return parts


def _fit(
    frames: dict, interval: str, horizon: int, alpha: float, kind: str, hi_frac: float
):
    parts = _train_parts(frames, interval, horizon, hi_frac)
    if sum(len(y) for _, y in parts) < 200:
        return None
    if kind == "factor":
        return fit_factor_model(parts, horizon)
    X = np.vstack([p[0] for p in parts])
    y = np.concatenate([p[1] for p in parts])
    return fit_ridge(X, y, alpha, horizon)


def _train_rows(frames: dict, interval: str, horizon: int, hi_frac: float):
    Xs, ys = [], []
    for (_, i), fr in frames.items():
        if i != interval:
            continue
        hi = split_point(fr, hi_frac) - horizon if hi_frac < 1 else len(fr["logc"])
        X, y, _ = rows(fr, horizon, 0, hi)
        Xs.append(X)
        ys.append(y)
    if not Xs:
        return None, None
    return np.vstack(Xs), np.concatenate(ys)


def evaluate(
    frames: dict,
    horizon: int,
    alpha: float,
    train_frac: float = 0.6,
    kind: str = "factor",
) -> dict[str, Any]:
    report, specs = {}, {}
    for interval in sorted({i for _, i in frames}, key=INTERVAL_MINUTES.get):
        spec = _fit(frames, interval, horizon, alpha, kind, train_frac)
        if spec is None:
            continue
        specs[interval] = spec
        model = LinearModel(spec)
        parts = []
        for (_, i), fr in frames.items():
            if i != interval:
                continue
            Xt, yt, st = rows(fr, horizon, split_point(fr, train_frac), len(fr["logc"]))
            if len(yt) >= 50:
                parts.append((model.predict(Xt), yt, st))
        p = np.concatenate([a for a, _, _ in parts])
        yy = np.concatenate([b for _, b, _ in parts])
        ss = np.concatenate([c for _, _, c in parts])
        report[interval] = score(p, yy, ss, horizon)
        if "factors" in spec:
            report[interval]["factors"] = {
                k: v["ic"] for k, v in spec["factors"].items() if v["used"]
            }
    return {"horizon": horizon, "alpha": alpha, "test": report, "specs": specs}


def evaluate_trading(
    frames_candles: dict[tuple[str, str], list[dict]],
    frames: dict,
    horizon: int,
    alpha: float,
    kind: str,
    train_frac: float,
    strategy_id: str = "ai_regime_fusion",
    baseline_path: str | None = None,
    fee_bps: float = 10.0,
    params: dict[str, float] | None = None,
) -> dict[str, dict]:
    """Backtest the strategy on the unseen part of every market with a model
    fitted only on the older part. Returns per-interval averages."""
    from .backtest import run_backtest
    from .research import _load_registry

    template = _load_registry().get(strategy_id)
    oos = evaluate(frames, horizon, alpha, train_frac, kind)
    bundle = {"kind": kind, "horizon": horizon, "models": {}}
    for tf, spec in oos["specs"].items():
        spec = dict(spec)
        spec["oos"] = {k: v for k, v in oos["test"][tf].items() if k != "factors"}
        bundle["models"][tf] = spec
    baseline = None
    if baseline_path:
        from .registry import _load_python_plugin

        baseline = _load_python_plugin(Path(baseline_path))

    out: dict[str, dict] = {}
    for (sym, tf), candles in frames_candles.items():
        split = int(len(candles) * train_frac)
        history = F.WARMUP + 10
        test = candles[split - history :]
        strat = template.__class__({**(params or {}), "fee_bps": fee_bps})
        strat.models = TimeframeModels(bundle)
        r = run_backtest(test, fee_bps, strat, start_index=history)
        bh = (float(test[-1]["close"]) / float(test[history]["close"]) - 1) * 100
        row = out.setdefault(
            tf,
            {
                "n": 0,
                "ret": 0.0,
                "dd": 0.0,
                "trips": 0,
                "wins": 0,
                "bh": 0.0,
                "base": 0.0,
            },
        )
        row["n"] += 1
        row["ret"] += r["return_pct"]
        row["dd"] += r["max_drawdown_pct"]
        row["trips"] += r["round_trips"]
        row["wins"] += r["return_pct"] > 0
        row["bh"] += bh
        if baseline is not None:
            rb = run_backtest(test, fee_bps, baseline.__class__(), start_index=history)
            row["base"] += rb["return_pct"]
    for row in out.values():
        for k in ("ret", "dd", "bh", "base"):
            row[k] = round(row[k] / row["n"], 2)
    return out


def _print(result: dict) -> None:
    print(f"\nhorizon {result['horizon']} candles, alpha {result['alpha']}")
    print(
        f"{'tf':>4} {'n':>7} {'IC':>7} {'hit':>6} {'hit@20%':>8} {'top q bps':>10} {'bot q bps':>10}"
    )
    for tf in INTERVALS:
        r = result["test"].get(tf)
        if r:
            print(
                f"{tf:>4} {r['n']:7} {r['ic']:+7.3f} {r['hit_rate']:6.3f} "
                f"{r['hit_rate_top20']:8.3f} {r['top_q_bps']:+10.1f} {r['bottom_q_bps']:+10.1f}"
                f"  {r.get('factors', '')}"
            )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Fusion model research")
    ap.add_argument("command", choices=["eval", "eval-trade", "train"])
    ap.add_argument("--baseline", help="plugin file to compare against")
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--set", nargs="*", default=[], help="strategy params k=v")
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS)
    ap.add_argument("--intervals", nargs="+", default=INTERVALS)
    ap.add_argument("--bars", type=int, default=None, help="default: per interval")
    ap.add_argument("--horizons", nargs="+", type=int, default=[12])
    ap.add_argument("--alphas", nargs="+", type=float, default=[0.1])
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--kind", choices=["factor", "ridge"], default="factor")
    ap.add_argument("--out", default=str(MODEL_PATH))
    args = ap.parse_args(argv)

    frames = market_frames(args.symbols, args.intervals, args.bars)
    print(f"{len(frames)} markets loaded")

    if args.command == "eval-trade":
        candles = {
            (s, i): load_candles(s, i, args.bars or DEFAULT_BARS.get(i, 5000))
            for (s, i) in frames
        }
        res = evaluate_trading(
            candles,
            frames,
            args.horizons[0],
            args.alphas[0],
            args.kind,
            args.train_frac,
            baseline_path=args.baseline,
            fee_bps=args.fee_bps,
            params=dict(kv.split("=") for kv in args.set) if args.set else None,
        )
        print(
            f"\nunseen {1 - args.train_frac:.0%} of each market, "
            f"net of {args.fee_bps:g} bps/side, horizon {args.horizons[0]}"
        )
        print(
            f"{'tf':>4} {'avg ret%':>9} {'profitable':>10} {'avg DD%':>8} "
            f"{'trips':>6} {'buy&hold%':>10} {'baseline%':>10}"
        )
        for tf in sorted(res, key=INTERVAL_MINUTES.get):
            r = res[tf]
            print(
                f"{tf:>4} {r['ret']:+9.2f} {r['wins']:>5}/{r['n']:<4} {r['dd']:+8.2f} "
                f"{r['trips']:6} {r['bh']:+10.2f} {r['base']:+10.2f}"
            )
        return

    if args.command == "eval":
        for h in args.horizons:
            for a in args.alphas:
                _print(evaluate(frames, h, a, args.train_frac, args.kind))
        return

    # train: one model per timeframe on all data (choose horizon/alpha
    # with `eval` first).
    h, a = args.horizons[0], args.alphas[0]
    # Record out-of-sample reliability per timeframe first (fit on the oldest
    # `train_frac`, score the rest); the strategy abstains where it is <= 0.
    oos = evaluate(frames, h, a, args.train_frac, args.kind)["test"]
    bundle: dict[str, Any] = {
        "kind": args.kind,
        "horizon": h,
        "train_frac": args.train_frac,
        "models": {},
    }
    for interval in args.intervals:
        spec = _fit(frames, interval, h, a, args.kind, 1.0)
        if spec is None:
            continue
        spec["oos"] = {k: v for k, v in oos.get(interval, {}).items() if k != "factors"}
        bundle["models"][interval] = spec
        used = {k: v["ic"] for k, v in spec.get("factors", {}).items() if v["used"]}
        print(
            f"{interval:>4}: factors {used}  out-of-sample IC {spec['oos'].get('ic')}"
        )
    bundle["trained_on"] = {"symbols": args.symbols, "intervals": args.intervals}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
