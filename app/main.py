from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .backtest import run_backtest
from .market import BinancePublicClient
from .live_market import LiveMarketFeed
from .paper import PaperBroker
from .strategy import EmaMomentumStrategy

BASE_DIR = Path(__file__).resolve().parent
market = BinancePublicClient()
strategy = EmaMomentumStrategy()
broker = PaperBroker()
live_feed = LiveMarketFeed(market)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await live_feed.bootstrap()
    except Exception:
        pass
    task = __import__('asyncio').create_task(live_feed.run())
    yield
    await live_feed.stop()
    task.cancel()
    try:
        await task
    except Exception:
        pass
    await market.close()


app = FastAPI(title="PulseShift", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrderIn(BaseModel):
    side: str
    amount_usd: float = Field(default=250.0, gt=0)


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "mode": "paper", "version": "0.3.0", "market_stream": live_feed.connected}


@app.get("/api/market")
async def get_market():
    try:
        snapshot = live_feed.market or await market.snapshot()
        candles = live_feed.candles or await market.klines(limit=120)
        decision = strategy.decide(candles, snapshot.spread_bps)
        return {
            "market": snapshot.as_dict(),
            "decision": decision.as_dict(),
            "account": broker.snapshot(snapshot.price),
            "candles": candles[-120:],
            "strategy": strategy.name,
            "mode": "PAPER",
            "stream_connected": live_feed.connected,
            "stream_last_event_ms": live_feed.last_event_ms,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market data unavailable: {exc}") from exc


@app.websocket("/ws/market")
async def market_socket(socket: WebSocket):
    await socket.accept()
    try:
        last_sent = 0
        while True:
            await __import__('asyncio').sleep(0.25)
            snapshot = live_feed.market
            if snapshot is None:
                continue
            if snapshot.ts_ms == last_sent and live_feed.last_event_ms == last_sent:
                continue
            candles = live_feed.candles
            decision = strategy.decide(candles, snapshot.spread_bps) if candles else None
            payload = {
                "type": "market",
                "market": snapshot.as_dict(),
                "candle": live_feed.current_candle(),
                "decision": decision.as_dict() if decision else None,
                "account": broker.snapshot(snapshot.price),
                "stream_connected": live_feed.connected,
                "stream_last_event_ms": live_feed.last_event_ms,
            }
            await socket.send_json(payload)
            last_sent = max(snapshot.ts_ms, live_feed.last_event_ms)
    except WebSocketDisconnect:
        return


@app.post("/api/order")
async def order(body: OrderIn):
    try:
        snapshot = live_feed.market or await market.snapshot()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market data unavailable: {exc}") from exc

    side = body.side.upper()
    if side == "BUY":
        trade = broker.buy(body.amount_usd, snapshot.ask)
    elif side == "SELL":
        if broker.qty <= 0:
            raise HTTPException(status_code=400, detail="No BTC paper position to sell.")
        qty = min(broker.qty, body.amount_usd / snapshot.bid)
        trade = broker.sell(qty, snapshot.bid)
    elif side == "CLOSE":
        if broker.qty <= 0:
            raise HTTPException(status_code=400, detail="No BTC paper position to close.")
        trade = broker.sell(broker.qty, snapshot.bid)
    else:
        raise HTTPException(status_code=400, detail="side must be BUY, SELL, or CLOSE")

    return {"trade": trade.as_dict(), "account": broker.snapshot(snapshot.price)}


@app.post("/api/reset")
async def reset():
    broker.reset()
    return {"ok": True}


@app.get("/api/backtest")
async def backtest(interval: str = "5m", limit: int = 500):
    try:
        candles = await market.klines(interval=interval, limit=limit)
        return run_backtest(candles)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backtest data unavailable: {exc}") from exc


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
