# PulseShift

Local-first crypto paper-trading and algorithm research lab.

PulseShift now uses a **Python/FastAPI quant engine** with a **React + TypeScript terminal UI**. It runs on your own computer, pulls public spot market data from Binance, simulates trades locally, records strategy decisions with confidence, and provides a polished browser interface for research.

## Current stack

- **Quant engine:** Python + FastAPI
- **Frontend:** React + TypeScript + Vite
- **Charting:** TradingView Lightweight Charts
- **Market source:** Binance public API
- **Execution:** local paper broker only
- **Storage:** in-memory for V0.2; DuckDB/Parquet planned next
- **AI adapters:** Jev/Laya-ready, not yet connected

## V0.4 features

- Local terminal UI at `http://127.0.0.1:5173` with symbol tabs and draggable, resizable panels
- Local API at `http://127.0.0.1:8000`
- Any Binance USDT spot pair with a pinned, multi-symbol watchlist
- Professional candlestick chart
- Live bid / ask / spread
- Paper portfolio and simulated fills across multiple symbols
- EMA Momentum v1 baseline
- Signal, regime, and execution confidence
- Transparent strategy reasons
- Execution ledger
- Quick 500 × 5-minute backtest
- Return, win rate, max drawdown, and profit factor
- No exchange credentials required
- No real-order endpoint

## Quick start

### Windows

Run:

```powershell
start-dev.bat
```

This opens the API and React UI, then launches the browser.

### macOS / Linux

```bash
chmod +x start-dev.sh
./start-dev.sh
```

### Manual development

Backend:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Frontend:

```bash
cd web
npm install
npm run dev
```

Then open:

```
http://127.0.0.1:5173
```

## Architecture

```
React + TypeScript terminal
        |
        v
FastAPI quant engine
        |
        +-- Binance public market adapter
        +-- Strategy engine
        +-- Confidence model
        +-- Local paper broker
        +-- Backtest runner
        |
        v
Future: DuckDB + Parquet recorder/replay
```

## Next milestone

The next major build should be **persistent market recording + deterministic replay + multi-strategy comparison**, so the same BTC session can be replayed through baseline, Jev, Laya, and experimental strategies.

## Safety

PulseShift is a research and simulation tool. The current build does **not** place real exchange orders. Keep paper and live broker layers physically separated and validate strategies out-of-sample before considering real-capital use.
