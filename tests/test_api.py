"""API-level tests that need no network: symbol search ranking, the forward
tracker's accounting, and the trend portfolio builder."""

from __future__ import annotations

import numpy as np
import pytest

from app.alphalab import run_weights, trend_weights
from app.forward import COST_BPS, ForwardTracker, UNIVERSE_VERSION
from app.market import BinancePublicClient


@pytest.mark.asyncio
async def test_symbol_search_ranks_exact_base_first():
    client = BinancePublicClient()
    client._symbols_cache = (
        1e12,
        [
            {"symbol": "BNSOLUSDT", "base": "BNSOL", "quote": "USDT"},
            {"symbol": "SOLVUSDT", "base": "SOLV", "quote": "USDT"},
            {"symbol": "SOLUSDT", "base": "SOL", "quote": "USDT"},
            {"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT"},
        ],
    )
    out = await client.search_symbols("sol")
    assert [o["symbol"] for o in out] == ["SOLUSDT", "SOLVUSDT", "BNSOLUSDT"]
    await client.close()


def _daily(symbols, n, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    t0 = 1_700_000_000_000
    for j, s in enumerate(symbols):
        closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n)))
        out[s] = [
            {
                "open_time": t0 + i * 86_400_000,
                "close_time": t0 + (i + 1) * 86_400_000 - 1,
                "open": c,
                "high": c,
                "low": c,
                "close": float(c),
                "volume": 1.0,
            }
            for i, c in enumerate(closes)
        ]
    return out


def test_forward_tracker_matches_backtest_accounting(tmp_path, monkeypatch):
    from app import forward, portfolio

    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    monkeypatch.setattr(portfolio, "TRADING_UNIVERSE", syms)
    hist = _daily(syms, 260)
    tracker = ForwardTracker(tmp_path)
    days = [c["open_time"] for c in hist[syms[0]]]
    # Pretend "now" is far in the future so every candle counts as closed.
    monkeypatch.setattr(forward.time, "time", lambda: 4e9)
    for d in days[-40:]:
        tracker.record_trend(
            {s: [c for c in v if c["open_time"] <= d] for s, v in hist.items()}
        )
    # Recording the same day again is a no-op.
    assert tracker.record_trend(hist) is None

    ev = tracker.evaluate_trend()
    assert ev["records"] == 40 and ev["days_evaluated"] == 39
    assert ev["universe"] == UNIVERSE_VERSION

    close = np.column_stack([[c["close"] for c in hist[s]] for s in syms])
    ref = run_weights(close, trend_weights(close), COST_BPS)["pnl"]
    eq = np.array([e["strategy"] for e in ev["equity"]])
    pnl = eq / np.concatenate([[1.0], eq[:-1]]) - 1
    # First day differs by the initial buy-in cost; the rest must match.
    assert np.allclose(pnl[1:], ref[-39:][1:], atol=1e-4)


def test_trend_portfolio_weights_are_bounded():
    from app import portfolio

    hist = _daily(["AAAUSDT", "BBBUSDT"], 300)
    built = portfolio.build(hist)
    for h in built["holdings"]:
        assert 0.0 <= h["weight_pct"] <= 50.0  # each coin gets at most 1/N
    assert 0.0 <= built["exposure_pct"] <= 100.0
