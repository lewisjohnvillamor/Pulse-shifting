from __future__ import annotations

from .strategy import EmaMomentumStrategy, Strategy


def _should_exit(strategy: Strategy, window: list[dict]) -> bool:
    # Plugins that don't subclass Strategy may lack the hook; fall back to
    # "exit once the long signal is gone".
    hook = getattr(strategy, "should_exit", None)
    if hook is not None:
        return bool(hook(window, spread_bps=1.0, side="LONG"))
    return strategy.decide(window, spread_bps=1.0).action != "LONG"


def run_backtest(
    candles: list[dict],
    fee_bps: float = 10.0,
    strategy: Strategy | None = None,
    start_index: int = 30,
) -> dict:
    """Long-only replay. Candles before `start_index` are indicator history
    only — no trades are opened there."""
    if strategy is None:
        strategy = EmaMomentumStrategy()
    cash = 10_000.0
    qty = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve = [cash]

    # Strategies may pre-compute per-candle features for the whole series.
    prepare = getattr(strategy, "prepare", None)
    if prepare is not None:
        prepare(candles)
    lookback = getattr(strategy, "max_lookback", None)
    for index in range(max(start_index, 0), len(candles)):
        start = max(0, index + 1 - lookback) if lookback else 0
        window = candles[start : index + 1]
        price = float(candles[index]["close"])

        if qty == 0:
            decision = strategy.decide(window, spread_bps=1.0)
            if decision.action == "LONG":
                # Optional position sizing: levels["size"] = fraction of
                # equity to deploy (e.g. volatility targeting); default all-in.
                size = (decision.levels or {}).get("size", 1.0)
                size = min(max(float(size), 0.0), 1.0) or 1.0
                spend = cash * size
                fee = spend * fee_bps / 10_000
                qty = (spend - fee) / price
                entry_price = price
                cash -= spend
                trades.append(
                    {
                        "side": "BUY",
                        "price": price,
                        "time": int(candles[index]["open_time"]),
                        "confidence": decision.confidence,
                    }
                )

        elif _should_exit(strategy, window):
            gross = qty * price
            fee = gross * fee_bps / 10_000
            cash += gross - fee
            pnl = (price - entry_price) * qty - fee
            trades.append(
                {
                    "side": "SELL",
                    "price": price,
                    "time": int(candles[index]["open_time"]),
                    "pnl": pnl,
                    "confidence": None,
                }
            )
            qty = 0.0
            entry_price = 0.0

        equity_curve.append(cash + qty * price)

    if qty:
        price = float(candles[-1]["close"])
        gross = qty * price
        fee = gross * fee_bps / 10_000
        cash += gross - fee
        pnl = (price - entry_price) * qty - fee
        trades.append(
            {
                "side": "SELL",
                "price": price,
                "time": int(candles[-1]["open_time"]),
                "pnl": pnl,
                "confidence": 1.0,
                "open": True,
            }
        )

    closed_trades = [trade for trade in trades if trade["side"] == "SELL"]
    wins = [trade for trade in closed_trades if trade.get("pnl", 0) > 0]
    losses = [trade for trade in closed_trades if trade.get("pnl", 0) <= 0]
    gross_profit = sum(trade.get("pnl", 0) for trade in wins)
    gross_loss = abs(sum(trade.get("pnl", 0) for trade in losses))

    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        drawdown = (equity / peak - 1) * 100 if peak else 0.0
        max_drawdown = min(max_drawdown, drawdown)

    return {
        "strategy": strategy.name,
        "start_equity": 10_000.0,
        "end_equity": round(cash, 2),
        "return_pct": round((cash / 10_000 - 1) * 100, 3),
        "round_trips": len(closed_trades),
        "win_rate_pct": round(
            (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0, 2
        ),
        "max_drawdown_pct": round(max_drawdown, 3),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "trades": trades[-50:],
        "all_trades": trades,
    }


def trade_markers(trades: list[dict]) -> list[dict]:
    """BUY/EXIT chart markers; exits are labelled with the trade's % result."""
    out: list[dict] = []
    entry = None
    for t in trades:
        if t["side"] == "BUY":
            entry = t["price"]
            text = "BUY"
        else:
            change = (t["price"] / entry - 1) * 100 if entry else 0.0
            text = f"{'OPEN' if t.get('open') else 'EXIT'} {change:+.1f}%"
            entry = None
        out.append(
            {
                "time": t["time"] // 1000,
                "side": t["side"],
                "price": t["price"],
                "text": text,
                "open": bool(t.get("open")),
            }
        )
    return out


def signal_overlay(
    candles: list[dict],
    strategy: Strategy,
    spread_bps: float = 1.0,
    fee_bps: float = 10.0,
) -> dict:
    """Chart overlay for a strategy: BUY/SELL markers from a replay over
    `candles`, plus the current decision and its entry/stop/target plan."""
    start = max(int(getattr(strategy, "min_candles", 30)), 30)
    replay = run_backtest(candles, fee_bps, strategy, start_index=start)
    markers = trade_markers(replay["all_trades"])
    markers += _setup_markers(candles, strategy)
    decision = strategy.decide(candles, spread_bps).as_dict()
    return {
        "markers": markers,
        "plan": decision.get("levels"),
        "decision": decision,
        "summary": {
            k: replay[k]
            for k in ("return_pct", "round_trips", "win_rate_pct", "max_drawdown_pct")
        },
    }


def _setup_markers(
    candles: list[dict], strategy: Strategy, top_frac: float = 0.1, cap: int = 40
) -> list[dict]:
    """Candles where the model's predicted move starts a new run in its
    strongest `top_frac` (per direction) for this window. These are leans,
    not trades: they need not beat fees."""
    series = getattr(strategy, "edge_series", None)
    if series is None:
        return []
    edges = series(candles)
    finite = sorted(abs(e) for e in edges if e == e)
    if len(finite) < 20:
        return []
    cut = finite[int(len(finite) * (1 - top_frac))]
    if cut <= 0:
        return []
    out: list[dict] = []
    prev = 0
    for c, e in zip(candles, edges):
        state = 0 if e != e or abs(e) < cut else (1 if e > 0 else -1)
        if state and state != prev:
            out.append(
                {
                    "time": int(c["open_time"]) // 1000,
                    "side": "SETUP_UP" if state > 0 else "SETUP_DOWN",
                    "price": float(c["close"]),
                    "text": "",
                    "edge_bps": round(e, 1),
                }
            )
        prev = state
    return out[-cap:]
