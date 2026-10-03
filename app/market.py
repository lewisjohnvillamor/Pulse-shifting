from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

BINANCE_BASE = "https://api.binance.com"
# Public market-data mirror that is not geo-restricted; used as a fallback
# when api.binance.com answers 451/403.
BINANCE_FALLBACK_BASE = "https://data-api.binance.vision"


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


def normalize_symbol(symbol: str) -> str:
    cleaned = "".join(ch for ch in symbol.upper() if ch.isalnum())
    if not cleaned:
        raise ValueError("symbol is empty")
    return cleaned


class BinancePublicClient:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=BINANCE_BASE, timeout=8.0)
        self._symbols_cache: tuple[float, list[dict[str, Any]]] | None = None

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {"params": params}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = await self.client.get(path, **kwargs)
        if (
            response.status_code in (403, 451)
            and str(self.client.base_url) != BINANCE_FALLBACK_BASE
        ):
            self.client.base_url = BINANCE_FALLBACK_BASE
            response = await self.client.get(path, **kwargs)
        response.raise_for_status()
        return response

    async def list_symbols(self) -> list[dict[str, Any]]:
        """Tradable USDT-quoted spot symbols, cached for 10 minutes.

        The unfiltered exchangeInfo payload is ~18 MB and could exceed the
        default timeout on slower links (search then hung). Requesting only
        TRADING symbols without permission sets is ~2.5 MB; it also gets a
        longer timeout, and a stale list is served if a refresh fails.
        """
        now = time.time()
        if self._symbols_cache and now - self._symbols_cache[0] < 600:
            return self._symbols_cache[1]

        try:
            response = await self._get(
                "/api/v3/exchangeInfo",
                params={"symbolStatus": "TRADING", "showPermissionSets": "false"},
                timeout=30.0,
            )
        except Exception:
            if self._symbols_cache:
                return self._symbols_cache[1]
            raise
        symbols = [
            {
                "symbol": item["symbol"],
                "base": item["baseAsset"],
                "quote": item["quoteAsset"],
            }
            for item in response.json()["symbols"]
            if item["status"] == "TRADING"
            and item["quoteAsset"] == "USDT"
            and item.get("isSpotTradingAllowed", True)
        ]
        symbols.sort(key=lambda item: item["symbol"])
        self._symbols_cache = (now, symbols)
        return symbols

    async def search_symbols(
        self, query: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        symbols = await self.list_symbols()
        needle = query.upper().strip()
        if needle:
            # Exact base first (SOL -> SOLUSDT), then prefix, then substring.
            def rank(item: dict[str, Any]) -> tuple[int, int, str]:
                base = item["base"]
                if base == needle:
                    tier = 0
                elif base.startswith(needle):
                    tier = 1
                else:
                    tier = 2
                return (tier, len(base), item["symbol"])

            symbols = sorted(
                (
                    item
                    for item in symbols
                    if needle in item["symbol"] or needle in item["base"]
                ),
                key=rank,
            )
        return symbols[:limit]

    async def snapshot(self, symbol: str = "BTCUSDT") -> MarketSnapshot:
        ticker_response, book_response = await asyncio.gather(
            self._get("/api/v3/ticker/24hr", params={"symbol": symbol}),
            self._get("/api/v3/ticker/bookTicker", params={"symbol": symbol}),
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
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, float | int]]:
        limit = max(20, min(limit, 1000))
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        response = await self._get("/api/v3/klines", params=params)
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
                "trades": int(row[8]),
                # Base volume bought by aggressive (taker) buyers: order flow.
                "taker_buy_volume": float(row[9]),
            }
            for row in rows
        ]
