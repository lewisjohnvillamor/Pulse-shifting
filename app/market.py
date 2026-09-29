from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

BINANCE_BASE = "https://api.binance.com"


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    bid: float
    ask: float
    spread_bps: float
    change_24h_pct: float
    volume_24h: float
    ts_ms: int

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class BinancePublicClient:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=BINANCE_BASE, timeout=8.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def snapshot(self, symbol: str = "BTCUSDT") -> MarketSnapshot:
        ticker_response, book_response = await asyncio.gather(
            self.client.get("/api/v3/ticker/24hr", params={"symbol": symbol}),
            self.client.get("/api/v3/ticker/bookTicker", params={"symbol": symbol}),
        )
        ticker_response.raise_for_status()
        book_response.raise_for_status()

        ticker = ticker_response.json()
        book = book_response.json()
        price = float(ticker["lastPrice"])
        bid = float(book["bidPrice"])
        ask = float(book["askPrice"])
        mid = (bid + ask) / 2 if bid and ask else price
        spread_bps = ((ask - bid) / mid) * 10_000 if mid else 0.0

        return MarketSnapshot(
            symbol=symbol,
            price=price,
            bid=bid,
            ask=ask,
            spread_bps=spread_bps,
            change_24h_pct=float(ticker["priceChangePercent"]),
            volume_24h=float(ticker["quoteVolume"]),
            ts_ms=int(time.time() * 1000),
        )

    async def klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        limit: int = 300,
    ) -> list[dict[str, float | int]]:
        limit = max(20, min(limit, 1000))
        response = await self.client.get(
            "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        response.raise_for_status()
        rows = response.json()

        return [
            {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": int(row[6]),
            }
            for row in rows
        ]
