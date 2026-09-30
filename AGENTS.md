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
.venv/bin/pip install ruff pre-commit
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
  snapshots are computed against a `{symbol: price}` map.
- Strategy logic stays in `app/strategy.py` and must remain symbol-agnostic
  (it operates on candle lists only).
- The UI dashboard is a `react-grid-layout` grid; panel layout, pinned
  symbols, and the active tab persist in `localStorage`.
- Don't commit `data/`, `.venv/`, `node_modules/`, or anything containing
  credentials.
