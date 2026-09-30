from __future__ import annotations

from .strategy import EmaMomentumStrategy


def run_backtest(candles: list[dict], fee_bps: float = 10.0) -> dict:
    strategy = EmaMomentumStrategy()
    cash = 10_000.0
    qty = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve = [cash]

    for index in range(30, len(candles)):
        window = candles[: index + 1]
        price = float(candles[index]["close"])
        decision = strategy.decide(window, spread_bps=1.0)

        if decision.action == "LONG" and qty == 0:
            fee = cash * fee_bps / 10_000
            qty = (cash - fee) / price
            entry_price = price
            cash = 0.0
            trades.append(
                {"side": "BUY", "price": price, "confidence": decision.confidence}
            )

        elif decision.action in ("SHORT", "FLAT") and qty > 0:
            gross = qty * price
            fee = gross * fee_bps / 10_000
            cash = gross - fee
            pnl = (price - entry_price) * qty - fee
            trades.append(
                {
                    "side": "SELL",
                    "price": price,
                    "pnl": pnl,
                    "confidence": decision.confidence,
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
