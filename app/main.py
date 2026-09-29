from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .backtest import run_backtest
from .market import BinancePublicClient
from .paper import PaperBroker
from .strategy import EmaMomentumStrategy

BASE_DIR = Path(__file__).resolve().parent
market = BinancePublicClient()
strategy = EmaMomentumStrategy()
broker = PaperBroker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await market.close()


app = FastAPI(title="PulseShift", version="0.2.0", lifespan=lifespan)

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
    return {"ok": True, "mode": "paper", "version": "0.2.0"}


@app.get("/api/market")
async def get_market():
    try:
        snapshot = await market.snapshot()
        candles = await market.klines(limit=120)
        decision = strategy.decide(candles, snapshot.spread_bps)
        return {
            "market": snapshot.as_dict(),
            "decision": decision.as_dict(),
            "account": broker.snapshot(snapshot.price),
            "candles": candles[-120:],
            "strategy": strategy.name,
            "mode": "PAPER",
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market data unavailable: {exc}") from exc


@app.post("/api/order")
async def order(body: OrderIn):
    try:
        snapshot = await market.snapshot()
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
