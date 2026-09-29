from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Trade:
    side: str
    qty: float
    price: float
    fee: float
    pnl: float
    ts: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class PaperBroker:
    starting_cash: float = 10_000.0
    fee_bps: float = 10.0
    cash: float = 10_000.0
    qty: float = 0.0
    entry_price: float = 0.0
    realized_pnl: float = 0.0
    trades: list[Trade] = field(default_factory=list)

    def reset(self) -> None:
        self.cash = self.starting_cash
        self.qty = 0.0
        self.entry_price = 0.0
        self.realized_pnl = 0.0
        self.trades.clear()

    def buy(self, usd: float, price: float) -> Trade:
        usd = max(0.0, min(float(usd), self.cash))
        fee = usd * self.fee_bps / 10_000
        spend = max(0.0, usd - fee)
        added_qty = spend / price if price > 0 else 0.0

        previous_cost = self.qty * self.entry_price
        new_qty = self.qty + added_qty
        self.entry_price = (previous_cost + spend) / new_qty if new_qty else 0.0
        self.qty = new_qty
        self.cash -= usd

        trade = Trade("BUY", added_qty, price, fee, 0.0, self._now())
        self.trades.append(trade)
        return trade

    def sell(self, qty: float, price: float) -> Trade:
        qty = max(0.0, min(float(qty), self.qty))
        gross = qty * price
        fee = gross * self.fee_bps / 10_000
        pnl = (price - self.entry_price) * qty - fee

        self.cash += gross - fee
        self.qty -= qty
        self.realized_pnl += pnl

        if self.qty <= 1e-12:
            self.qty = 0.0
            self.entry_price = 0.0

        trade = Trade("SELL", qty, price, fee, pnl, self._now())
        self.trades.append(trade)
        return trade

    def snapshot(self, price: float) -> dict:
        market_value = self.qty * price
        equity = self.cash + market_value
        unrealized = (price - self.entry_price) * self.qty if self.qty else 0.0

        return {
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 2),
            "btc_qty": self.qty,
            "entry_price": round(self.entry_price, 2),
            "market_value": round(market_value, 2),
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "total_return_pct": round((equity / self.starting_cash - 1) * 100, 4),
            "trade_count": len(self.trades),
            "trades": [trade.as_dict() for trade in self.trades[-30:]][::-1],
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
