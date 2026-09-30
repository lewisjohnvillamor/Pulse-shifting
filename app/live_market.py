from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import websockets

from .market import BinancePublicClient, MarketSnapshot, normalize_symbol

STREAM_BASES = (
    "wss://stream.binance.com:9443/stream",
    # Public data-stream mirror, not geo-restricted.
    "wss://data-stream.binance.vision/stream",
)
STREAM_KINDS = ("bookTicker", "ticker", "kline_1m")
# Enough 1m history for the slowest model features (200-bar averages).
LIVE_CANDLES = 500


def stream_url(symbol: str, base: str) -> str:
    lowered = symbol.lower()
    streams = "/".join(f"{lowered}@{kind}" for kind in STREAM_KINDS)
    return f"{base}?streams={streams}"


class SymbolFeed:
    """Live market state for one Binance spot symbol."""

    def __init__(self, rest: BinancePublicClient, symbol: str) -> None:
        self.symbol = normalize_symbol(symbol)
        self.rest = rest
        self.market: MarketSnapshot | None = None
        self.candles: list[dict[str, float | int]] = []
        self.connected = False
        self.last_event_ms = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._base_index = 0

    async def bootstrap(self) -> None:
        self.market = await self.rest.snapshot(self.symbol)
        self.candles = await self.rest.klines(self.symbol, limit=LIVE_CANDLES)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

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
                    stream_url(self.symbol, STREAM_BASES[self._base_index]),
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=2048,
                ) as socket:
                    self.connected = True
                    backoff = 1
                    async for raw in socket:
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
                self._base_index = (self._base_index + 1) % len(STREAM_BASES)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)

        self.connected = False

    def _ensure_market(self) -> MarketSnapshot:
        if self.market is None:
            self.market = MarketSnapshot(
                symbol=self.symbol,
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
            "trades": int(kline.get("n", 0)),
            "taker_buy_volume": float(kline.get("V", 0.0)),
        }

        market = self._ensure_market()
        market.price = candle["close"]
        market.ts_ms = int(data.get("E") or time.time() * 1000)

        if self.candles and self.candles[-1]["open_time"] == candle["open_time"]:
            self.candles[-1] = candle
        else:
            self.candles.append(candle)
            self.candles = self.candles[-LIVE_CANDLES:]

    def current_candle(self) -> dict[str, float | int] | None:
        return self.candles[-1] if self.candles else None


class MarketHub:
    """Owns one SymbolFeed per subscribed symbol."""

    def __init__(self, rest: BinancePublicClient) -> None:
        self.rest = rest
        self.feeds: dict[str, SymbolFeed] = {}

    def symbols(self) -> list[str]:
        return list(self.feeds)

    def feed(self, symbol: str) -> SymbolFeed | None:
        return self.feeds.get(normalize_symbol(symbol))

    async def subscribe(self, symbol: str) -> SymbolFeed:
        symbol = normalize_symbol(symbol)
        feed = self.feeds.get(symbol)
        if feed is not None:
            return feed
        feed = SymbolFeed(self.rest, symbol)
        self.feeds[symbol] = feed
        try:
            await feed.bootstrap()
        except Exception:
            pass
        if self.feeds.get(symbol) is feed:
            feed.start()
        else:
            await feed.stop()
        return feed

    async def unsubscribe(self, symbol: str) -> None:
        feed = self.feeds.pop(normalize_symbol(symbol), None)
        if feed is not None:
            await feed.stop()

    async def stop_all(self) -> None:
        await asyncio.gather(*(feed.stop() for feed in self.feeds.values()))
        self.feeds.clear()
