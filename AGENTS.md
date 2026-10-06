# AGENTS.md

PulseShift is a local-first crypto paper-trading and research lab. It has two
parts that always run together:

- `app/` — Python + FastAPI quant engine (market data, strategy, paper broker,
  backtest). Serves `http://127.0.0.1:8000`.
- `web/` — React + TypeScript + Vite terminal UI. Serves
  `http://127.0.0.1:5173` and talks to the API via `API` in `web/src/main.tsx`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install ruff pre-commit pytest pytest-asyncio
.venv/bin/pre-commit install

cd web && npm install
```

## Run

```bash
./start-dev.sh            # API + Vite dev server (macOS/Linux)
# or manually:
.venv/bin/uvicorn app.main:app --reload --port 8000
cd web && npm run dev
```

## Checks (run before committing)

```bash
.venv/bin/pre-commit run --all-files   # ruff + prettier + file hygiene
.venv/bin/pytest                       # core accounting/persistence tests
cd web && npm run build                # tsc -b && vite build
```

## Conventions

- Market data comes only from Binance **public** endpoints
  (`app/market.py` REST, `app/live_market.py` websocket streams). No exchange
  credentials, no real-order endpoints — execution is local paper fills only
  (`app/paper.py`).
- Symbols are Binance spot symbols like `BTCUSDT`. Every endpoint takes a
  `symbol` parameter; the pinned watchlist lives in `app/watchlist.py` and is
  persisted under `data/` (gitignored).
- `MarketHub` (`app/live_market.py`) owns one `SymbolFeed` per subscribed
  symbol; subscribe via `POST /api/watchlist` or automatically when a symbol is
  requested.
- `PaperBroker` tracks one position per symbol plus shared cash; account
  snapshots are computed against a `{symbol: price}` map. State persists
  to `data/paper_account.json` after every trade/reset.
- External-model plugins (`jev`, `laya`) call endpoints synchronously; after
  a failure `app/ai.py` marks them offline for 60 s so they never block the
  live loop or a backtest. Keep that cooldown if you add another adapter.
- `/api/market` returns `edges` (one edge card per strategy) alongside the
  baseline `edge`; the UI shows the focused strategy's.
- Strategies implement `Strategy.decide(candles, spread_bps)` in
  `app/strategy.py` and must remain symbol-agnostic. Built-ins live in
  `app/strategy.py`; user plugins drop into `strategies/` (`.py` or `.json`,
  see `strategies/README.md`) and are loaded by `app/registry.py`. Editable
  params are declared via `specs` and persist to `data/strategy_params.json`.
- Backtests are long-only replays (`app/backtest.py`). While in a position
  they call `Strategy.should_exit(candles, spread_bps, side)`; override it
  for hysteresis/stops. Set `max_lookback` on a strategy to bound the window.
- `python -m app.research` runs multi-market walk-forward evaluation/tuning;
  judge strategy changes on its out-of-sample output, not one backtest.
- `ai_regime_fusion` (v3) loads per-timeframe models from
  `app/models/fusion_model.json` (features in `app/features.py`, training
  and evaluation in `app/modeling.py`). Retrain with
  `python -m app.modeling train` and commit the JSON. Decisions may carry
  `levels` (entry/stop/target) which the chart draws via `GET /api/signals`.
- `python -m app.alphalab <test>` holds the out-of-sample tests of the
  six researched effects (trend, xsmom, seasonality, leadlag, funding, gbm)
  on a 20-coin universe; data cached under `data/research/alphalab/`.
  Only `trend` validated: it ships as `strategies/trend_vol_target.py` and
  `app/portfolio.py` (`GET /api/portfolio/trend`, Trend portfolio panel).
  Decisions may carry `levels["size"]` (fraction of equity) which
  `run_backtest` honours.
- `app/forward.py` is the forward (live paper) test: the API records trend
  weights + AI daily signals once per closed daily candle into
  `data/forward/*.jsonl` (never backfill it). `GET /api/forward` scores it
  against backtest expectations with a pre-committed stop rule; don't
  loosen `STOP_RULE` after seeing live data.
  It tracks the fixed `portfolio.TRADING_UNIVERSE` (38 coins); bump
  `UNIVERSE_VERSION` if the coin set ever changes.
- `app/signals.py` computes the spread+fee-aware entry/exit edge card
  (`edge` in `/api/market` payloads).
- `app/monitor.py` (SignalMonitor) logs opt-in per-symbol signal events to
  `data/monitor/<symbol>.jsonl`.
- Strategies declare `styles` ("scalping" | "day" | "swing") for the UI filter.
- AI-provider endpoints/keys live in `data/config.json` (AppConfig,
  gitignored, masked via `/api/config`); `app/ai.py` AiProviderClient calls
  them. `app/patterns.py` emits chart overlay geometry consumed by the
  Chart component.
- The UI dashboard is a `react-grid-layout` grid; panel layout, pinned
  symbols, and the active tab persist in `localStorage`.
- Hosts, ports, CORS origins and the data dir come from `app/settings.py`
  (env vars / root `.env`; template in `.env.example`). The UI reads
  `VITE_API_URL` from the same file. There are no secrets in the repo;
  BYO AI keys live only in `data/config.json`.
- Don't commit `data/`, `.env`, `.venv/`, `node_modules/`, or anything
  containing credentials.
