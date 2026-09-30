from __future__ import annotations

import asyncio
import time
from typing import Any

import websockets

from .market import BinancePublicClient, MarketSnapshot

SPOT_STREAM = (
    "wss://stream.binance.com:9443/stream"
    "?streams=btcusdt@bookTicker/btcusdt@ticker/btcusdt@kline_1m"
)


class LiveMarketFeed:
    def __init__(self, rest: BinancePublicClient) -> None:
        self.rest = rest
        self.market: MarketSnapshot | None = None
        self.candles: list[dict[str, float | int]] = []
        self.connected = False
        self.last_event_ms = 0
        self._stop = asyncio.Event()

    async def bootstrap(self) -> None:
        self.market = await self.rest.snapshot()
        self.candles = await self.rest.klines(limit=120)

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if self.market is None or not self.candles:
            try:
                await self.bootstrap()
            except Exception:
                pass

        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    SPOT_STREAM,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=2048,
                ) as socket:
                    self.connected = True
                    backoff = 1
                    async for raw in socket:
                        import json
                        packet = json.loads(raw)
                        stream = packet.get("stream", "")
                        data = packet.get("data", {})
                        self.last_event_ms = int(data.get("E") or time.time() * 1000)

                        if stream.endswith("@bookTicker"):
                            self._apply_book(data)
                        elif stream.endswith("@ticker"):
                            self._apply_ticker(data)
                        elif "@kline_" in stream:
                            self._apply_kline(data)

                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)

        self.connected = False

    def _ensure_market(self) -> MarketSnapshot:
        if self.market is None:
            self.market = MarketSnapshot(
                symbol="BTCUSDT",
                price=0.0,
                bid=0.0,
                ask=0.0,
                spread_bps=0.0,
                change_24h_pct=0.0,
                volume_24h=0.0,
                ts_ms=int(time.time() * 1000),
            )
        return self.market

    def _apply_book(self, data: dict[str, Any]) -> None:
        market = self._ensure_market()
        bid = float(data.get("b", market.bid))
        ask = float(data.get("a", market.ask))
        mid = (bid + ask) / 2 if bid and ask else market.price
        market.bid = bid
        market.ask = ask
        market.spread_bps = ((ask - bid) / mid) * 10_000 if mid else 0.0
        market.ts_ms = int(time.time() * 1000)

    def _apply_ticker(self, data: dict[str, Any]) -> None:
        market = self._ensure_market()
        market.price = float(data.get("c", market.price))
        market.change_24h_pct = float(data.get("P", market.change_24h_pct))
        market.volume_24h = float(data.get("q", market.volume_24h))
        market.ts_ms = int(data.get("E") or time.time() * 1000)

    def _apply_kline(self, data: dict[str, Any]) -> None:
        kline = data.get("k", {})
        if not kline:
            return

        candle = {
            "open_time": int(kline["t"]),
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "close_time": int(kline["T"]),
        }

        market = self._ensure_market()
        market.price = candle["close"]
        market.ts_ms = int(data.get("E") or time.time() * 1000)

        if self.candles and self.candles[-1]["open_time"] == candle["open_time"]:
            self.candles[-1] = candle
        else:
            self.candles.append(candle)
            self.candles = self.candles[-120:]

    def current_candle(self) -> dict[str, float | int] | None:
        return self.candles[-1] if self.candles else None
