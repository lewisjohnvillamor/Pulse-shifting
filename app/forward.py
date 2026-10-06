"""Forward (live paper) test of the validated signals.

Backtests can be overfit by accident; a forward test cannot. Once per day
this records, from *closed* daily candles only:

* the Trend portfolio's target weights (app/portfolio.py rule) and closes;
* the AI model's daily-candle signal for a fixed set of coins.

Evaluation replays those records: weights recorded at close d are held to
close d+1, charged `cost_bps` per unit of turnover — the same accounting as
the backtest — and compared with what the out-of-sample backtest predicted.
A stop rule, fixed in advance, flags when the edge looks gone.

    python -m app.forward record    # record today (idempotent)
    python -m app.forward report    # print the live-vs-expected summary

Records live in data/forward/ (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .alphalab import stats
from .portfolio import TRADING_UNIVERSE, VALIDATION

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "forward"
TREND_FILE = "trend_portfolio.jsonl"
AI_FILE = "ai_signals.jsonl"
# Trend records are tagged with the universe they were computed on; only
# records of the current universe are evaluated (changing the coin set
# changes the strategy, so its forward clock restarts).
UNIVERSE_VERSION = f"{len(TRADING_UNIVERSE)}-coin-2026-10"
# AI signals: "core" = the 6 coins the model was trained on (daily OOS IC
# +0.093); "extended" = the other trading coins (daily OOS IC +0.044 on the
# same unseen period, positive for 21/32 coins). Scored separately.
AI_CORE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
AI_SYMBOLS = list(dict.fromkeys([*AI_CORE, *TRADING_UNIVERSE]))
COST_BPS = 12.0

# Expectations from the out-of-sample backtests, and the pre-committed rule
# for deciding the edge is gone. Not to be loosened after seeing live data.
EXPECTED = {
    "trend_sharpe": VALIDATION["sharpe"],
    "trend_max_dd_pct": VALIDATION["max_dd_pct"],
    "trend_cagr_pct": VALIDATION["cagr_pct"],
    "ai_1d_ic": {"core": 0.093, "extended": 0.044},
}
STOP_RULE = {"min_days": 180, "min_sharpe": 0.3, "max_dd_pct": -35.0}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def closed(candles: list[dict], now_ms: int | None = None) -> list[dict]:
    """Drop the still-forming candle, if any."""
    now_ms = now_ms or int(time.time() * 1000)
    return [c for c in candles if int(c.get("close_time", 0)) < now_ms]


class ForwardTracker:
    def __init__(self, directory: Path = DATA_DIR) -> None:
        self.dir = directory

    # -- storage ---------------------------------------------------------

    def _read(self, name: str) -> list[dict]:
        path = self.dir / name
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _append(self, name: str, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / name).open("a") as fh:
            fh.write(json.dumps(record) + "\n")

    # -- recording -------------------------------------------------------

    def record_trend(self, candles_by_symbol: dict[str, list[dict]]) -> dict | None:
        """Record target weights from closed daily candles. One record per
        candle date; returns None if that date is already recorded."""
        from . import portfolio

        data = {s: closed(c) for s, c in candles_by_symbol.items()}
        data = {s: c for s, c in data.items() if len(c) >= 140}
        if not data:
            return None
        last_open = max(int(c[-1]["open_time"]) for c in data.values())
        date = _day(last_open)
        if any(r["date"] == date for r in self._read(TREND_FILE)):
            return None
        built = portfolio.build(data, COST_BPS)
        record = {
            "date": date,
            "universe": UNIVERSE_VERSION,
            "recorded_at": int(time.time() * 1000),
            "weights": {
                h["symbol"]: round(h["weight_pct"] / 100, 6) for h in built["holdings"]
            },
            "prices": {
                s: float(c[-1]["close"])
                for s, c in data.items()
                if int(c[-1]["open_time"]) == last_open
            },
        }
        self._append(TREND_FILE, record)
        return record

    def record_ai(
        self, strategy, candles_by_symbol: dict[str, list[dict]]
    ) -> list[dict]:
        done = {(r["date"], r["symbol"]) for r in self._read(AI_FILE)}
        out = []
        for sym, candles in candles_by_symbol.items():
            c = closed(candles)
            if len(c) < getattr(strategy, "min_candles", 30):
                continue
            date = _day(int(c[-1]["open_time"]))
            if (date, sym) in done:
                continue
            d = strategy.decide(c, 1.0).as_dict()
            levels = d.get("levels") or {}
            record = {
                "date": date,
                "symbol": sym,
                "group": "core" if sym in AI_CORE else "extended",
                "action": d["action"],
                "price": float(c[-1]["close"]),
                "expected_move_bps": levels.get("expected_move_bps"),
                "horizon": levels.get("horizon_candles", 12),
                "reliability_ic": levels.get("reliability_ic"),
            }
            self._append(AI_FILE, record)
            out.append(record)
        return out

    # -- evaluation ------------------------------------------------------

    def evaluate_trend(self) -> dict[str, Any]:
        all_recs = self._read(TREND_FILE)
        recs = sorted(
            (r for r in all_recs if r.get("universe") == UNIVERSE_VERSION),
            key=lambda r: r["date"],
        )
        pnl, bench, curve = [], [], []
        prev_w: dict[str, float] = {}
        for a, b in zip(recs, recs[1:]):
            common = [s for s in a["prices"] if s in b["prices"]]
            if not common:
                continue
            rets = {s: b["prices"][s] / a["prices"][s] - 1 for s in common}
            w = a["weights"]
            turnover = sum(
                abs(w.get(s, 0.0) - prev_w.get(s, 0.0)) for s in set(w) | set(prev_w)
            )
            day = (
                sum(w.get(s, 0.0) * rets[s] for s in common) - turnover * COST_BPS / 1e4
            )
            pnl.append(day)
            bench.append(float(np.mean(list(rets.values()))))
            prev_w = w
            curve.append(
                {"date": b["date"], "strategy": day, "equal_weight": bench[-1]}
            )
        days = len(pnl)
        s = stats(np.array(pnl), 365) if days else None
        bm = stats(np.array(bench), 365) if days else None
        eq = np.cumprod(1 + np.array(pnl)) if days else np.array([])
        eb = np.cumprod(1 + np.array(bench)) if days else np.array([])
        status = "collecting"
        if days >= STOP_RULE["min_days"] and s:
            failed = (
                s["sharpe"] < STOP_RULE["min_sharpe"]
                or s["max_dd"] < STOP_RULE["max_dd_pct"]
            )
            status = "edge_gone" if failed else "on_track"
        elif s and s["max_dd"] < STOP_RULE["max_dd_pct"]:
            status = "edge_gone"  # drawdown breach counts at any time
        return {
            "universe": UNIVERSE_VERSION,
            "coins": len(TRADING_UNIVERSE),
            "earlier_universe_records": len(all_recs) - len(recs),
            "records": len(recs),
            "first_date": recs[0]["date"] if recs else None,
            "last_date": recs[-1]["date"] if recs else None,
            "days_evaluated": days,
            "live": s,
            "equal_weight": bm,
            "expected": {
                "sharpe": EXPECTED["trend_sharpe"],
                "max_dd_pct": EXPECTED["trend_max_dd_pct"],
                "cagr_pct": EXPECTED["trend_cagr_pct"],
            },
            "equity": [
                {
                    "date": c["date"],
                    "strategy": round(float(x), 5),
                    "equal_weight": round(float(y), 5),
                }
                for c, x, y in zip(curve, eq, eb)
            ],
            "stop_rule": STOP_RULE,
            "status": status,
        }

    def evaluate_ai(self, closes: dict[str, dict[str, float]]) -> dict[str, Any]:
        """Score recorded daily AI signals whose horizon has elapsed, per
        group. `closes` maps symbol -> {date: close} of closed daily candles."""
        recs = self._read(AI_FILE)
        out = {"signals": len(recs), "groups": {}}
        for group in ("core", "extended"):
            rows = [
                r
                for r in recs
                if r.get("group", "core" if r["symbol"] in AI_CORE else "extended")
                == group
            ]
            out["groups"][group] = self._score_ai(
                rows, closes, EXPECTED["ai_1d_ic"][group]
            )
        return out

    @staticmethod
    def _score_ai(recs: list[dict], closes: dict, expected_ic: float) -> dict[str, Any]:
        exp, real, long_rets = [], [], []
        pending = 0
        for r in recs:
            series = closes.get(r["symbol"], {})
            dates = sorted(series)
            if r["date"] not in series:
                pending += 1
                continue
            i = dates.index(r["date"]) + int(r.get("horizon") or 12)
            if i >= len(dates):
                pending += 1
                continue
            ret = series[dates[i]] / r["price"] - 1
            if r.get("expected_move_bps") is not None:
                exp.append(r["expected_move_bps"])
                real.append(ret * 1e4)
            if r["action"] == "LONG":
                long_rets.append(ret * 1e4)
        ic = (
            float(np.corrcoef(exp, real)[0, 1])
            if len(exp) >= 10 and np.std(exp) > 0 and np.std(real) > 0
            else None
        )
        return {
            "signals": len(recs),
            "resolved": len(real),
            "pending": pending,
            "ic": None if ic is None or math.isnan(ic) else round(ic, 4),
            "expected_ic": expected_ic,
            "long_calls": len(long_rets),
            "long_avg_bps": round(float(np.mean(long_rets)), 1) if long_rets else None,
            "long_hit_rate": round(float(np.mean(np.array(long_rets) > 0)), 3)
            if long_rets
            else None,
            "round_trip_cost_bps": 2 * 10 + 1,
        }


# ----------------------------------------------------------------------------
# Wiring used by the API and the CLI


async def record_now(market, registry, extra_symbols: list[str] = ()) -> dict:
    # `extra_symbols` (e.g. pinned coins) is ignored on purpose: the forward
    # test must track a fixed universe, or pinning a coin would silently
    # change the strategy being tested.
    """Fetch daily candles and record today's trend weights + AI signals."""
    symbols = list(dict.fromkeys([*TRADING_UNIVERSE, *AI_SYMBOLS]))

    async def daily(sym: str):
        try:
            return sym, await market.klines(sym, interval="1d", limit=1000)
        except Exception:
            return sym, []

    fetched = dict(await asyncio.gather(*(daily(s) for s in symbols)))
    tracker = ForwardTracker()
    trend = await asyncio.to_thread(tracker.record_trend, fetched)
    ai = []
    strategy = registry.get("ai_regime_fusion")
    if strategy is not None:
        ai = await asyncio.to_thread(
            tracker.record_ai, strategy, {s: fetched.get(s, []) for s in AI_SYMBOLS}
        )
    return {"trend_recorded": trend is not None, "ai_recorded": len(ai)}


async def report_now(market) -> dict:
    tracker = ForwardTracker()

    async def daily(sym: str) -> tuple[str, list[dict]]:
        try:
            return sym, closed(await market.klines(sym, interval="1d", limit=1000))
        except Exception:
            return sym, []

    # Fetch all coins concurrently: sequentially this took ~11 s cold.
    fetched = await asyncio.gather(*(daily(s) for s in AI_SYMBOLS))
    closes = {
        sym: {_day(int(c["open_time"])): float(c["close"]) for c in candles}
        for sym, candles in fetched
    }
    return {"trend": tracker.evaluate_trend(), "ai": tracker.evaluate_ai(closes)}


def main(argv: list[str] | None = None) -> None:
    from .market import BinancePublicClient
    from .registry import StrategyRegistry

    ap = argparse.ArgumentParser(description="Forward test of validated signals")
    ap.add_argument("command", choices=["record", "report"])
    args = ap.parse_args(argv)

    async def run() -> dict:
        market = BinancePublicClient()
        try:
            if args.command == "record":
                base = Path(__file__).resolve().parent.parent
                registry = StrategyRegistry(
                    base / "strategies", base / "data" / "strategy_params.json"
                )
                return await record_now(market, registry)
            return await report_now(market)
        finally:
            await market.close()

    print(json.dumps(asyncio.run(run()), indent=2))


if __name__ == "__main__":
    main()
