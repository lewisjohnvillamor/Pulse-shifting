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

    lookback = getattr(strategy, "max_lookback", None)
    for index in range(max(start_index, 0), len(candles)):
        start = max(0, index + 1 - lookback) if lookback else 0
        window = candles[start : index + 1]
        price = float(candles[index]["close"])

        if qty == 0:
            decision = strategy.decide(window, spread_bps=1.0)
            if decision.action == "LONG":
                fee = cash * fee_bps / 10_000
                qty = (cash - fee) / price
                entry_price = price
                cash = 0.0
                trades.append(
                    {"side": "BUY", "price": price, "confidence": decision.confidence}
                )

        elif _should_exit(strategy, window):
            gross = qty * price
            fee = gross * fee_bps / 10_000
            cash = gross - fee
            pnl = (price - entry_price) * qty - fee
            trades.append(
                {
                    "side": "SELL",
                    "price": price,
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
        cash = gross - fee
        pnl = (price - entry_price) * qty - fee
        trades.append({"side": "SELL", "price": price, "pnl": pnl, "confidence": 1.0})

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
    }
