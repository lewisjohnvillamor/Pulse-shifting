from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .backtest import run_backtest, signal_overlay, trade_markers
from .evolution import run_evolution
from .live_market import MarketHub, SymbolFeed
from .market import BinancePublicClient, MarketSnapshot, normalize_symbol
from .monitor import SignalMonitor
from .paper import PaperBroker
from .patterns import detect_patterns
from .config import AppConfig
from .registry import StrategyRegistry
from .signals import edge_assessment
from .watchlist import Watchlist

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
market = BinancePublicClient()
broker = PaperBroker(path=DATA_DIR / "paper_account.json")
broker.load()
hub = MarketHub(market)
watchlist = Watchlist(DATA_DIR / "watchlist.json")
monitor = SignalMonitor(DATA_DIR / "monitor")
config = AppConfig(DATA_DIR / "config.json")
registry = StrategyRegistry(
    plugin_dir=BASE_DIR.parent / "strategies",
    params_file=DATA_DIR / "strategy_params.json",
)


async def _forward_recorder() -> None:
    """Record the forward test once per closed daily candle (checked hourly;
    recording is idempotent per date)."""
    from . import forward

    while True:
        try:
            await forward.record_now(market, registry, watchlist.symbols)
        except Exception:
            pass  # network hiccup: try again next hour
        await asyncio.sleep(3600)


async def _warm_symbols() -> None:
    try:
        await market.list_symbols()
    except Exception:
        pass  # the first search will retry


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.gather(*(hub.subscribe(symbol) for symbol in watchlist.symbols))
    recorder = asyncio.create_task(_forward_recorder())
    # Warm the symbol list so the first search in the UI is instant.
    warm = asyncio.create_task(_warm_symbols())
    yield
    recorder.cancel()
    warm.cancel()
    await hub.stop_all()
    await market.close()


APP_VERSION = "0.5.0"
app = FastAPI(title="PulseShift", version=APP_VERSION, lifespan=lifespan)

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
    symbol: str = "BTCUSDT"


class PinIn(BaseModel):
    symbol: str


class ParamsIn(BaseModel):
    params: dict[str, float]


class MonitorIn(BaseModel):
    enabled: bool


class ProviderIn(BaseModel):
    url: str | None = None
    api_key: str | None = None
    timeout_ms: int | None = None


class EvolutionIn(BaseModel):
    symbol: str = "BTCUSDT"
    interval: str = "5m"
    limit: int = Field(default=1000, ge=240, le=1000)
    strategy_id: str = "ai_regime_fusion"
    generations: int = Field(default=4, ge=1, le=20)
    population: int = Field(default=16, ge=6, le=100)
    elite_fraction: float = Field(default=0.2, ge=0.05, le=0.5)
    mutation_rate: float = Field(default=0.35, ge=0.05, le=0.9)
    mutation_scale: float = Field(default=0.10, ge=0.01, le=0.35)
    seed: int = 42


def _clean_symbol(symbol: str) -> str:
    try:
        return normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid symbol") from exc


async def _feed_for(symbol: str) -> SymbolFeed:
    """Return the live feed for a symbol, subscribing on first use."""
    try:
        return await hub.subscribe(symbol)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Market data unavailable for {symbol}: {exc}"
        ) from exc


async def _snapshot_for(symbol: str) -> MarketSnapshot:
    feed = hub.feed(symbol)
    if feed and feed.market:
        return feed.market
    try:
        return await market.snapshot(symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Market data unavailable for {symbol}: {exc}"
        ) from exc


def _price_map(extra: dict[str, float] | None = None) -> dict[str, float]:
    prices = {
        symbol: feed.market.price
        for symbol, feed in hub.feeds.items()
        if feed.market and feed.market.price
    }
    if extra:
        prices.update(extra)
    return prices


def _market_payload(feed: SymbolFeed, snapshot: MarketSnapshot) -> dict:
    candles = feed.candles
    decisions = registry.decide_all(candles, snapshot.spread_bps) if candles else {}
    # Primary decision comes from the first registered (baseline) strategy.
    primary_id = next(iter(registry.strategies), None)
    decision = decisions.get(primary_id) if primary_id else None
    primary = registry.get(primary_id) if primary_id else None
    edge = edge_assessment(
        candles,
        snapshot,
        None if primary is None else _decision_obj(decision),
        fee_bps=broker.fee_bps,
    )
    # One edge card per strategy so the UI can follow whichever strategy
    # the user focuses (the top-level `edge` stays the baseline's).
    edges = {
        sid: edge_assessment(
            candles, snapshot, _decision_obj(d), fee_bps=broker.fee_bps
        )
        for sid, d in decisions.items()
        if d.get("action") != "ERROR"
    }
    payload = {
        "market": snapshot.as_dict(),
        "decision": decision,
        "decisions": decisions,
        "edge": edge,
        "edges": edges,
        "account": broker.snapshot(_price_map(), snapshot.symbol),
        "candles": candles[-120:],
        "strategies": [s["id"] for s in registry.describe()],
        "mode": "PAPER",
        "stream_connected": feed.connected,
        "stream_last_event_ms": feed.last_event_ms,
        "monitoring": monitor.is_enabled(snapshot.symbol),
        "patterns": detect_patterns(candles),
    }
    monitor.record(snapshot.symbol, payload)
    return payload


def _decision_obj(d: dict | None):
    from .strategy import Decision

    if not d or "action" not in d:
        return None
    return Decision(
        action=d["action"],
        confidence=d["confidence"],
        regime=d["regime"],
        regime_confidence=d["regime_confidence"],
        execution_confidence=d["execution_confidence"],
        reasons=d["reasons"],
    )


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "mode": "paper",
        "version": APP_VERSION,
        "streams": {s: f.connected for s, f in hub.feeds.items()},
    }


@app.get("/api/symbols")
async def symbols(
    query: str = Query(default=""), limit: int = Query(default=50, le=200)
):
    try:
        results = await market.search_symbols(query, limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Symbol list unavailable: {exc}"
        ) from exc
    pinned = set(watchlist.symbols)
    return {
        "symbols": [{**item, "pinned": item["symbol"] in pinned} for item in results]
    }


@app.get("/api/watchlist")
async def get_watchlist():
    return {
        "symbols": [
            {
                "symbol": symbol,
                "market": (feed.market.as_dict() if feed and feed.market else None),
                "stream_connected": feed.connected if feed else False,
            }
            for symbol in watchlist.symbols
            for feed in [hub.feed(symbol)]
        ]
    }


@app.post("/api/watchlist")
async def pin_symbol(body: PinIn):
    symbol = _clean_symbol(body.symbol)
    # Validate the symbol exists on Binance before pinning.
    try:
        await market.snapshot(symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Unknown or untraded symbol: {symbol}"
        ) from exc
    watchlist.add(symbol)
    await hub.subscribe(symbol)
    return {"symbols": watchlist.symbols}


@app.delete("/api/watchlist/{symbol}")
async def unpin_symbol(symbol: str):
    symbol = _clean_symbol(symbol)
    removed = watchlist.remove(symbol)
    if removed:
        await hub.unsubscribe(symbol)
    return {"symbols": watchlist.symbols}


@app.get("/api/strategies")
async def list_strategies():
    return {"strategies": registry.describe()}


@app.put("/api/strategies/{strategy_id}")
async def configure_strategy(strategy_id: str, body: ParamsIn):
    result = registry.configure(strategy_id, body.params)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy_id}")
    return {"id": strategy_id, "params": result}


@app.post("/api/strategies/reload")
async def reload_strategies():
    registry.reload()
    return {"strategies": [s["id"] for s in registry.describe()]}


@app.post("/api/evolution/run")
async def evolve_strategy(body: EvolutionIn):
    symbol = _clean_symbol(body.symbol)
    strategy = registry.get(body.strategy_id)
    if strategy is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown strategy: {body.strategy_id}"
        )
    if not getattr(strategy, "specs", None):
        raise HTTPException(
            status_code=400,
            detail=f"Strategy {body.strategy_id} does not expose mutable parameters",
        )

    try:
        candles = await market.klines(
            symbol,
            interval=body.interval,
            limit=body.limit,
        )
        result = await asyncio.to_thread(
            run_evolution,
            candles,
            strategy,
            generations=body.generations,
            population=body.population,
            elite_fraction=body.elite_fraction,
            mutation_rate=body.mutation_rate,
            mutation_scale=body.mutation_scale,
            seed=body.seed,
        )
        result["symbol"] = symbol
        result["interval"] = body.interval
        result["window"] = {
            "start_ms": candles[0]["open_time"],
            "end_ms": candles[-1]["close_time"],
            "candles": len(candles),
        }
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Evolution experiment failed: {exc}"
        ) from exc


@app.post("/api/evolution/promote/{strategy_id}")
async def promote_evolved_strategy(strategy_id: str, body: ParamsIn):
    result = registry.configure(strategy_id, body.params)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy_id}")
    return {
        "ok": True,
        "strategy_id": strategy_id,
        "params": result,
        "note": "Promoted parameters are persisted locally. Paper trading remains enabled.",
    }


@app.get("/api/config")
async def get_config():
    return config.as_public_dict()


@app.put("/api/config/{provider}")
async def set_provider(provider: str, body: ProviderIn):
    name = provider.strip().lower()
    if not name or len(name) > 40:
        raise HTTPException(status_code=400, detail="Invalid provider name")
    merged = config.set_provider(
        name,
        {
            "url": body.url,
            "api_key": body.api_key,
            "timeout_ms": body.timeout_ms,
        },
    )
    registry.reload()
    safe = config.as_public_dict()["providers"].get(name, {})
    return {"provider": name, "config": safe, "stored_keys": list(merged)}


@app.post("/api/monitor/{symbol}")
async def set_monitor(symbol: str, body: MonitorIn):
    symbol = _clean_symbol(symbol)
    if body.enabled:
        await _feed_for(symbol)
    monitor.set_enabled(symbol, body.enabled)
    return {
        "symbol": symbol,
        "enabled": monitor.is_enabled(symbol),
        "monitoring": monitor.symbols(),
    }


@app.get("/api/monitor/{symbol}")
async def get_monitor(symbol: str, limit: int = Query(default=50, le=200)):
    symbol = _clean_symbol(symbol)
    return {
        "symbol": symbol,
        "enabled": monitor.is_enabled(symbol),
        "events": monitor.recent(symbol, limit),
    }


@app.get("/api/patterns")
async def patterns(symbol: str = "BTCUSDT", lookback: int = Query(default=120, le=500)):
    symbol = _clean_symbol(symbol)
    feed = hub.feed(symbol)
    candles = (
        feed.candles
        if feed and feed.candles
        else await market.klines(symbol, limit=min(lookback, 500))
    )
    return {"symbol": symbol, **detect_patterns(candles, lookback)}


@app.get("/api/monitor")
async def monitored_symbols():
    return {"monitoring": monitor.symbols()}


@app.get("/api/market")
async def get_market(symbol: str = "BTCUSDT"):
    symbol = _clean_symbol(symbol)
    feed = await _feed_for(symbol)
    snapshot = feed.market or await _snapshot_for(symbol)
    return _market_payload(feed, snapshot)


@app.websocket("/ws/market")
async def market_socket(socket: WebSocket, symbol: str = "BTCUSDT"):
    await socket.accept()
    symbol = _clean_symbol(symbol)
    try:
        feed = await hub.subscribe(symbol)
    except Exception:
        await socket.close(code=1011)
        return
    try:
        last_sent = 0
        while True:
            await asyncio.sleep(0.25)
            snapshot = feed.market
            if snapshot is None:
                continue
            if snapshot.ts_ms == last_sent and feed.last_event_ms == last_sent:
                continue
            payload = _market_payload(feed, snapshot)
            payload["type"] = "market"
            payload["candle"] = feed.current_candle()
            payload.pop("candles", None)
            await socket.send_json(payload)
            last_sent = max(snapshot.ts_ms, feed.last_event_ms)
    except WebSocketDisconnect:
        return


@app.post("/api/order")
async def order(body: OrderIn):
    symbol = _clean_symbol(body.symbol)
    snapshot = await _snapshot_for(symbol)

    side = body.side.upper()
    if side == "BUY":
        trade = broker.buy(symbol, body.amount_usd, snapshot.ask)
    elif side == "SELL":
        qty = broker.position_qty(symbol)
        if qty <= 0:
            raise HTTPException(
                status_code=400, detail=f"No {symbol} paper position to sell."
            )
        trade = broker.sell(
            symbol, min(qty, body.amount_usd / snapshot.bid), snapshot.bid
        )
    elif side == "CLOSE":
        qty = broker.position_qty(symbol)
        if qty <= 0:
            raise HTTPException(
                status_code=400, detail=f"No {symbol} paper position to close."
            )
        trade = broker.sell(symbol, qty, snapshot.bid)
    else:
        raise HTTPException(status_code=400, detail="side must be BUY, SELL, or CLOSE")

    return {
        "trade": trade.as_dict(),
        "account": broker.snapshot(_price_map({symbol: snapshot.price}), symbol),
    }


@app.post("/api/reset")
async def reset():
    broker.reset()
    return {"ok": True}


def _plan_for(strategy, candles: list[dict]) -> dict | None:
    try:
        return strategy.decide(candles, 1.0).as_dict().get("levels")
    except Exception:
        return None


_FORWARD_CACHE: dict[str, tuple[float, dict]] = {}


@app.get("/api/forward")
async def forward_report():
    """Live (forward) paper results of the trend portfolio and the AI model's
    daily signals vs. what the out-of-sample backtests predicted."""
    import time

    from . import forward

    hit = _FORWARD_CACHE.get("report")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    result = await forward.report_now(market)
    _FORWARD_CACHE["report"] = (time.time(), result)
    return result


@app.post("/api/forward/record")
async def forward_record():
    from . import forward

    result = await forward.record_now(market, registry, watchlist.symbols)
    _FORWARD_CACHE.clear()
    return result


_TREND_CACHE: dict[str, tuple[float, dict]] = {}


@app.get("/api/portfolio/trend")
async def trend_portfolio(symbols: str = "", cost_bps: float = 12.0):
    """Today's target weights for the validated trend + vol-target portfolio
    (see app/portfolio.py). Defaults to the research universe plus pins."""
    import time

    from . import portfolio
    from .portfolio import TRADING_UNIVERSE

    chosen = [_clean_symbol(x) for x in symbols.split(",") if x.strip()] or list(
        dict.fromkeys([*TRADING_UNIVERSE, *watchlist.symbols])
    )
    key = ",".join(chosen) + f"|{cost_bps}"
    hit = _TREND_CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]

    async def daily(sym: str):
        try:
            return sym, await market.klines(sym, interval="1d", limit=1000)
        except Exception:
            return sym, []

    fetched = dict(await asyncio.gather(*(daily(s) for s in chosen)))
    result = await asyncio.to_thread(portfolio.build, fetched, cost_bps)
    result["skipped"] = [s for s in chosen if len(fetched.get(s, [])) < 140]
    _TREND_CACHE[key] = (time.time(), result)
    return result


SIGNAL_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}


@app.get("/api/signals")
async def chart_signals(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    limit: int = Query(default=1000, ge=100, le=1000),
    strategy: str = "ai_regime_fusion",
):
    """Candles for any timeframe plus the strategy's chart overlay: past
    BUY/EXIT markers from a replay of this window and the current
    entry/stop/target plan."""
    symbol = _clean_symbol(symbol)
    if interval not in SIGNAL_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval {interval}")
    instance = registry.get(strategy)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy}")
    try:
        candles = await market.klines(symbol, interval=interval, limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Market data unavailable: {exc}"
        ) from exc
    feed = hub.feed(symbol)
    spread = feed.market.spread_bps if feed and feed.market else 1.0
    overlay = await asyncio.to_thread(
        signal_overlay, candles, instance, spread, broker.fee_bps
    )
    return {
        "symbol": symbol,
        "interval": interval,
        "strategy": strategy,
        "candles": candles[-300:],
        **overlay,
    }


@app.get("/api/backtest")
async def backtest(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    limit: int = 500,
    strategy: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
):
    """Replay a strategy over historical klines. `start_ms`/`end_ms` are
    epoch milliseconds — pick any point in the past Binance has data for."""
    symbol = _clean_symbol(symbol)
    instance = None
    if strategy:
        instance = registry.get(strategy)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy}")
    try:
        candles = await market.klines(
            symbol,
            interval=interval,
            limit=limit,
            start_time=start_ms,
            end_time=end_ms,
        )
        if len(candles) < 60:
            raise HTTPException(
                status_code=400,
                detail=f"Only {len(candles)} candles in that window — widen it.",
            )
        result = run_backtest(candles, strategy=instance)
        all_trades = result.pop("all_trades", [])
        result["signals"] = {
            "markers": trade_markers(all_trades),
            "plan": _plan_for(instance, candles) if instance else None,
        }
        result["symbol"] = symbol
        result["interval"] = interval
        result["patterns"] = detect_patterns(candles)
        result["candles"] = candles[-500:]
        result["strategy_id"] = strategy or "ema_momentum"
        result["window"] = {
            "start_ms": candles[0]["open_time"],
            "end_ms": candles[-1]["close_time"],
            "candles": len(candles),
        }
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Backtest data unavailable: {exc}"
        ) from exc


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
