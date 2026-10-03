from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Trade:
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    pnl: float
    ts: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Position:
    qty: float = 0.0
    entry_price: float = 0.0

    def as_dict(self, price: float) -> dict:
        market_value = self.qty * price
        unrealized = (price - self.entry_price) * self.qty if self.qty else 0.0
        return {
            "qty": self.qty,
            "entry_price": round(self.entry_price, 8),
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized, 2),
        }


@dataclass
class PaperBroker:
    starting_cash: float = 10_000.0
    fee_bps: float = 10.0
    cash: float = 10_000.0
    realized_pnl: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    # Optional on-disk state (data/paper_account.json) so the paper account
    # survives restarts; None keeps the broker purely in memory.
    path: Path | None = None

    def reset(self) -> None:
        self.cash = self.starting_cash
        self.realized_pnl = 0.0
        self.positions.clear()
        self.trades.clear()
        self.save()

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        if self.path is None:
            return
        state = {
            "version": 1,
            "starting_cash": self.starting_cash,
            "fee_bps": self.fee_bps,
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "positions": {
                s: {"qty": p.qty, "entry_price": p.entry_price}
                for s, p in self.positions.items()
                if p.qty > 0
            },
            "trades": [t.as_dict() for t in self.trades[-5000:]],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        tmp.replace(self.path)  # atomic on POSIX and Windows

    def load(self) -> bool:
        """Restore state from `path`. Returns False (and keeps defaults) if
        there is nothing to load or the file is unreadable."""
        if self.path is None or not self.path.exists():
            return False
        try:
            state = json.loads(self.path.read_text())
            self.starting_cash = float(state.get("starting_cash", self.starting_cash))
            self.cash = float(state["cash"])
            self.realized_pnl = float(state.get("realized_pnl", 0.0))
            self.positions = {
                s: Position(float(p["qty"]), float(p["entry_price"]))
                for s, p in state.get("positions", {}).items()
            }
            self.trades = [Trade(**t) for t in state.get("trades", [])]
            return True
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def position(self, symbol: str) -> Position:
        return self.positions.setdefault(symbol, Position())

    def buy(self, symbol: str, usd: float, price: float) -> Trade:
        usd = max(0.0, min(float(usd), self.cash))
        fee = usd * self.fee_bps / 10_000
        spend = max(0.0, usd - fee)
        added_qty = spend / price if price > 0 else 0.0

        pos = self.position(symbol)
        previous_cost = pos.qty * pos.entry_price
        new_qty = pos.qty + added_qty
        pos.entry_price = (previous_cost + spend) / new_qty if new_qty else 0.0
        pos.qty = new_qty
        self.cash -= usd

        trade = Trade(symbol, "BUY", added_qty, price, fee, 0.0, self._now())
        self.trades.append(trade)
        self.save()
        return trade

    def sell(self, symbol: str, qty: float, price: float) -> Trade:
        pos = self.position(symbol)
        qty = max(0.0, min(float(qty), pos.qty))
        gross = qty * price
        fee = gross * self.fee_bps / 10_000
        pnl = (price - pos.entry_price) * qty - fee

        self.cash += gross - fee
        pos.qty -= qty
        self.realized_pnl += pnl

        if pos.qty <= 1e-12:
            pos.qty = 0.0
            pos.entry_price = 0.0

        trade = Trade(symbol, "SELL", qty, price, fee, pnl, self._now())
        self.trades.append(trade)
        self.save()
        return trade

    def position_qty(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        return pos.qty if pos else 0.0

    def snapshot(
        self, prices: dict[str, float], focus_symbol: str | None = None
    ) -> dict:
        positions_out: dict[str, dict] = {}
        market_value_total = 0.0
        unrealized_total = 0.0
        for symbol, pos in self.positions.items():
            if pos.qty <= 0:
                continue
            price = prices.get(symbol, pos.entry_price)
            view = pos.as_dict(price)
            positions_out[symbol] = view
            market_value_total += view["market_value"]
            unrealized_total += view["unrealized_pnl"]

        equity = self.cash + market_value_total
        focus = positions_out.get(focus_symbol) if focus_symbol else None

        return {
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 2),
            "market_value": round(market_value_total, 2),
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized_total, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "total_return_pct": round((equity / self.starting_cash - 1) * 100, 4),
            "trade_count": len(self.trades),
            "position": focus,
            "positions": positions_out,
            "trades": [trade.as_dict() for trade in self.trades[-30:]][::-1],
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
