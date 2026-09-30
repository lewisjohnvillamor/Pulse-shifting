"""Multi-market, walk-forward strategy research from the command line.

Single-window backtests are easy to fool: one lucky trend flatters any
long-biased rule, and parameters tuned on the same data they are scored on
look great and then fail live. This module scores a strategy on many
symbol/interval combinations at once and, with ``--tune``, fits parameters
on one period and reports only the *next*, unseen period (walk-forward).

    python -m app.research --strategy ai_regime_fusion
    python -m app.research --strategy ai_regime_fusion --tune --folds 2
    python -m app.research --strategy ai_regime_fusion --set trail_atr=3

Candles are fetched from Binance public endpoints and cached under
``data/research/`` so repeated runs are offline and deterministic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from .backtest import run_backtest
from .evolution import _metric_score, mutate_params
from .market import BinancePublicClient
from .registry import StrategyRegistry
from .strategy import Strategy

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "data" / "research"
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_INTERVALS = ["5m", "15m", "1h", "4h"]

Market = tuple[str, str]


# --------------------------------------------------------------------------
# Data


async def _fetch(symbol: str, interval: str, bars: int) -> list[dict]:
    client = BinancePublicClient()
    out: list[dict] = []
    end: int | None = None
    try:
        while len(out) < bars:
            page = await client.klines(
                symbol, interval=interval, limit=1000, end_time=end
            )
            if not page:
                break
            out = page + out
            end = int(page[0]["open_time"]) - 1
            if len(page) < 1000:
                break
    finally:
        await client.close()
    return out[-bars:]


def load_candles(symbol: str, interval: str, bars: int) -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}_{interval}_{bars}_v2.json"
    if path.exists():
        return json.loads(path.read_text())
    candles = asyncio.run(_fetch(symbol, interval, bars))
    path.write_text(json.dumps(candles))
    return candles


# --------------------------------------------------------------------------
# Scoring


def _make(template: Strategy, params: dict[str, float]) -> Strategy:
    return template.__class__(params)


def score_markets(
    template: Strategy,
    params: dict[str, float],
    data: dict[Market, list[dict]],
    fee_bps: float = 10.0,
    start_index: int = 80,
) -> dict[str, Any]:
    """Backtest one parameter set on every market and aggregate. Candles
    before ``start_index`` are history only; no trades happen there."""
    per_market: dict[str, dict[str, Any]] = {}
    for (symbol, interval), candles in data.items():
        result = run_backtest(
            candles, fee_bps, _make(template, params), start_index=start_index
        )
        start = float(candles[min(start_index, len(candles) - 1)]["close"])
        per_market[f"{symbol} {interval}"] = {
            "return_pct": result["return_pct"],
            "max_drawdown_pct": result["max_drawdown_pct"],
            "profit_factor": result["profit_factor"],
            "round_trips": result["round_trips"],
            "buy_hold_pct": round((float(candles[-1]["close"]) / start - 1) * 100, 3),
            "_score": _metric_score(result),
        }
    rows = list(per_market.values())
    n = max(len(rows), 1)
    losers = sum(1 for r in rows if r["return_pct"] < 0)
    return {
        "avg_return_pct": round(sum(r["return_pct"] for r in rows) / n, 3),
        "avg_drawdown_pct": round(sum(r["max_drawdown_pct"] for r in rows) / n, 3),
        "avg_buy_hold_pct": round(sum(r["buy_hold_pct"] for r in rows) / n, 3),
        "profitable": n - losers,
        "markets": n,
        # Fitness: mean risk-adjusted score, minus a penalty per losing
        # market so one big winner cannot hide many losers.
        "fitness": sum(r["_score"] for r in rows) / n - 5.0 * losers / n,
        "per_market": per_market,
    }


def _load_registry() -> StrategyRegistry:
    # Read-only: tuned params are reported, never written to the live
    # registry's params file.
    return StrategyRegistry(
        BASE_DIR / "strategies", CACHE_DIR / "strategy_params.readonly.json"
    )


# Worker processes rebuild the strategy from its id: plugin classes are
# loaded dynamically and cannot be pickled across processes.
_WORKER: dict[str, Any] = {}


def _init_worker(strategy_id: str, data: dict[Market, list[dict]]) -> None:
    _WORKER["template"] = _load_registry().get(strategy_id)
    _WORKER["data"] = data


def _score_job(params: dict[str, float]) -> float:
    return score_markets(_WORKER["template"], params, _WORKER["data"])["fitness"]


def tune(
    template: Strategy,
    base: dict[str, float],
    data: dict[Market, list[dict]],
    *,
    generations: int,
    population: int,
    seed: int,
    workers: int,
    frozen: frozenset[str] = frozenset(),
    log=print,
) -> dict[str, float]:
    rng = random.Random(seed)

    def child(parent: dict[str, float], rate: float, scale: float) -> dict:
        params = mutate_params(template, parent, rng, rate, scale)
        return {**params, **{k: base[k] for k in frozen if k in base}}

    elite_n = max(2, population // 5)
    pop = [dict(base)] + [child(base, 0.4, 0.15) for _ in range(population - 1)]
    ranked: list[tuple[dict[str, float], float]] = []
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(template.id, data),
    ) as pool:
        for gen in range(generations):
            fits = list(pool.map(_score_job, pop))
            ranked = sorted(zip(pop, fits), key=lambda x: -x[1])
            log(f"  gen {gen}: best {ranked[0][1]:+.2f}")
            elites = [p for p, _ in ranked[:elite_n]]
            pop = elites + [
                child(rng.choice(elites), 0.35, 0.10)
                for _ in range(population - elite_n)
            ]
    return ranked[0][0]


def walk_forward(
    template: Strategy,
    base: dict[str, float],
    data: dict[Market, list[dict]],
    *,
    folds: int,
    tune_params: bool,
    generations: int = 6,
    population: int = 20,
    seed: int = 42,
    workers: int = 4,
    frozen: frozenset[str] = frozenset(),
    warmup: int = 300,
    log=print,
) -> dict[str, Any]:
    """Split each market into ``folds + 1`` chronological blocks. For fold k,
    fit on everything before block k+1 (if tuning) and score block k+1.
    ``warmup`` candles before each test block are included for indicators
    only; the backtest's first trades need ``min_candles`` of history."""
    length = min(len(c) for c in data.values())
    block = length // (folds + 1)
    results = []
    for k in range(folds):
        train_end = block * (k + 1)
        test_end = train_end + block
        train = {m: c[:train_end] for m, c in data.items()}
        history = min(warmup, train_end)
        test = {m: c[train_end - history : test_end] for m, c in data.items()}
        params = dict(base)
        if tune_params:
            log(f"fold {k + 1}/{folds}: tuning on {train_end} candles/market")
            params = tune(
                template,
                base,
                train,
                generations=generations,
                population=population,
                seed=seed + k,
                workers=workers,
                frozen=frozen,
                log=log,
            )
        scored = score_markets(template, params, test, start_index=history)
        results.append({"fold": k + 1, "params": params, "test": scored})
        log(
            f"fold {k + 1}: test avg {scored['avg_return_pct']:+.2f}% "
            f"(buy&hold {scored['avg_buy_hold_pct']:+.2f}%), "
            f"profitable {scored['profitable']}/{scored['markets']}, "
            f"avg DD {scored['avg_drawdown_pct']:+.2f}%"
        )
    n = len(results)
    return {
        "folds": results,
        "avg_test_return_pct": round(
            sum(r["test"]["avg_return_pct"] for r in results) / n, 3
        ),
        "avg_test_buy_hold_pct": round(
            sum(r["test"]["avg_buy_hold_pct"] for r in results) / n, 3
        ),
        "avg_test_drawdown_pct": round(
            sum(r["test"]["avg_drawdown_pct"] for r in results) / n, 3
        ),
    }


# --------------------------------------------------------------------------
# CLI


def _parse_set(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        key, _, value = item.partition("=")
        out[key.strip()] = float(value)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--strategy", default="ai_regime_fusion")
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--intervals", nargs="+", default=DEFAULT_INTERVALS)
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--folds", type=int, default=2)
    ap.add_argument("--tune", action="store_true", help="fit params per fold")
    ap.add_argument("--generations", type=int, default=6)
    ap.add_argument("--population", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--set", nargs="*", default=[], help="param overrides k=v")
    ap.add_argument(
        "--freeze", nargs="*", default=[], help="params the tuner must not change"
    )
    ap.add_argument("--json", help="write the full report to this path")
    args = ap.parse_args(argv)

    template = _load_registry().get(args.strategy)
    if template is None:
        raise SystemExit(f"Unknown strategy: {args.strategy}")
    base = {**template.params, **_parse_set(args.set)}

    data = {
        (s, i): load_candles(s, i, args.bars)
        for s in args.symbols
        for i in args.intervals
    }
    report = walk_forward(
        template,
        base,
        data,
        folds=args.folds,
        tune_params=args.tune,
        generations=args.generations,
        population=args.population,
        seed=args.seed,
        workers=args.workers,
        frozen=frozenset(args.freeze),
    )
    print(
        f"\nwalk-forward test: avg {report['avg_test_return_pct']:+.2f}% "
        f"vs buy&hold {report['avg_test_buy_hold_pct']:+.2f}%, "
        f"avg DD {report['avg_test_drawdown_pct']:+.2f}%"
    )
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
