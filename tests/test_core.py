"""Core accounting and persistence tests. Run with `.venv/bin/pytest`."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from app import ai
from app.backtest import run_backtest
from app.paper import PaperBroker
from app.strategy import Decision, Strategy

# ---------------------------------------------------------------------------
# helpers


def candles(closes, start_ms=1_700_000_000_000, step_ms=60_000):
    out = []
    for i, c in enumerate(closes):
        c = float(c)
        out.append(
            {
                "open_time": start_ms + i * step_ms,
                "open": c,
                "high": c * 1.001,
                "low": c * 0.999,
                "close": c,
                "volume": 100.0,
                "close_time": start_ms + (i + 1) * step_ms - 1,
            }
        )
    return out


class AlwaysLong(Strategy):
    id = "always_long"
    name = "Always long"
    min_candles = 1

    def __init__(self, size=None):
        super().__init__()
        self.size = size

    def decide(self, candles, spread_bps=0.0):
        levels = {"size": self.size} if self.size is not None else None
        return Decision("LONG", 0.9, "TEST", 1.0, 1.0, [], levels)

    def should_exit(self, candles, spread_bps=0.0, side="LONG"):
        return False


# ---------------------------------------------------------------------------
# paper broker


def test_broker_round_trip_and_fees():
    b = PaperBroker()
    b.buy("BTCUSDT", 1000.0, 100.0)
    assert b.cash == pytest.approx(9000.0)
    pos = b.position("BTCUSDT")
    assert pos.qty == pytest.approx(999.0 / 100.0)  # 10 bps fee on the way in
    b.sell("BTCUSDT", pos.qty, 110.0)
    assert b.position_qty("BTCUSDT") == 0.0
    gross = 9.99 * 110.0
    assert b.cash == pytest.approx(9000.0 + gross - gross * 0.001)
    snap = b.snapshot({})
    assert snap["trade_count"] == 2
    assert snap["realized_pnl"] == pytest.approx(round(b.realized_pnl, 2))


def test_broker_persists_and_reloads(tmp_path):
    path = tmp_path / "paper_account.json"
    b = PaperBroker(path=path)
    b.buy("ETHUSDT", 500.0, 2000.0)
    b.buy("ETHUSDT", 500.0, 2500.0)
    saved = json.loads(path.read_text())
    assert saved["positions"]["ETHUSDT"]["qty"] == pytest.approx(
        b.position_qty("ETHUSDT")
    )

    b2 = PaperBroker(path=path)
    assert b2.load() is True
    assert b2.cash == pytest.approx(b.cash)
    assert b2.position("ETHUSDT").entry_price == pytest.approx(
        b.position("ETHUSDT").entry_price
    )
    assert len(b2.trades) == 2

    b2.reset()
    assert json.loads(path.read_text())["cash"] == pytest.approx(10_000.0)


def test_broker_ignores_corrupt_state(tmp_path):
    path = tmp_path / "paper_account.json"
    path.write_text("{not json")
    b = PaperBroker(path=path)
    assert b.load() is False
    assert b.cash == 10_000.0


# ---------------------------------------------------------------------------
# backtest accounting


def test_backtest_all_in_matches_price_change_net_of_fees():
    cs = candles([100.0] * 31 + [110.0])
    r = run_backtest(cs, fee_bps=10.0, strategy=AlwaysLong())
    # Buy at 100 (fee 10 bps), forced close at 110 (fee 10 bps).
    qty = 9990.0 / 100.0
    expected = qty * 110.0 * (1 - 0.001)
    assert r["end_equity"] == pytest.approx(round(expected, 2))
    assert r["round_trips"] == 1


def test_backtest_honours_position_size_and_keeps_cash():
    cs = candles([100.0] * 31 + [110.0])
    r = run_backtest(cs, fee_bps=0.0, strategy=AlwaysLong(size=0.25))
    # 25% invested gains 10%; the 75% in cash must not be lost on exit.
    assert r["end_equity"] == pytest.approx(10_000.0 * (0.75 + 0.25 * 1.1))


def test_backtest_start_index_never_trades_warmup():
    cs = candles([100.0] * 50 + [200.0] * 50)
    r = run_backtest(cs, fee_bps=0.0, strategy=AlwaysLong(), start_index=60)
    # Entry happens at index 60 (price 200), so no gain from the 100->200 jump.
    assert r["return_pct"] == pytest.approx(0.0)
    assert r["all_trades"][0]["price"] == 200.0


# ---------------------------------------------------------------------------
# AI provider offline cooldown


def test_offline_cooldown_blocks_retries(monkeypatch):
    ai._offline_until.clear()
    assert ai.offline("x") is False
    ai.mark_offline("x", seconds=100)
    assert ai.offline("x") is True
    ai.mark_online("x")
    assert ai.offline("x") is False


def test_ai_client_does_not_call_network_while_offline(tmp_path, monkeypatch):
    from app.config import AppConfig

    cfg = AppConfig(tmp_path / "config.json")
    cfg.set_provider("jev", {"url": "http://127.0.0.1:9/decide", "timeout_ms": 200})
    client = ai.AiProviderClient(cfg, "jev")
    ai._offline_until.clear()
    assert client.decide({"closes": [1.0]}) is None  # connection refused
    assert ai.offline("jev") is True

    calls = []
    monkeypatch.setattr(
        ai.urllib.request,
        "urlopen",
        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(OSError()),
    )
    assert client.decide({"closes": [1.0]}) is None
    assert calls == []  # cooldown short-circuited before any network call


# ---------------------------------------------------------------------------
# features: rolling window == full-series values


def test_features_are_window_invariant():
    from app import features as F

    rng = np.random.default_rng(0)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 900)))
    cs = candles(closes)
    for c in cs:
        c["taker_buy_volume"] = 55.0
    full = F.compute(cs)["X"][-1]
    window = F.compute(cs[-400:])["X"][-1]
    assert np.allclose(full, window, equal_nan=True)
    assert not np.isnan(full).any()


def test_forward_target_is_clipped_and_shifted():
    from app import features as F

    logc = np.log(np.linspace(100, 200, 50))
    sigma = np.full(50, 0.01)
    y = F.forward_target(logc, sigma, 12)
    assert np.isnan(y[-12:]).all()
    assert np.nanmax(np.abs(y)) <= 4.0
    assert math.isclose(y[0], min(4.0, (logc[12] - logc[0]) / (0.01 * math.sqrt(12))))
