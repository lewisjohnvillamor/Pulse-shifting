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
- Pluggable strategies: built-ins (EMA Momentum, RSI Mean Reversion, Channel Breakout) plus drop-in `.py` / `.json` plugins in `strategies/` — see `strategies/README.md`
- Signal, regime, and execution confidence
- Multi-strategy live comparison board with editable parameters
- Real-time entry/exit edge card: spread+fee breakeven, net edge, entry/stop/target, win-probability estimate
- Strategy style tags (scalping / day / swing) with a filter on the board
- Opt-in signal monitor per symbol: logs every live entry/exit call to `data/monitor/<symbol>.jsonl`
- Historical backtest arena: any strategy, interval (1m–1d), and past date window
- `strategies/laya.py` + `strategies/jev.py`: adapters that delegate decisions to a local model or a configured AI endpoint
- Settings gear (BYO key): provider endpoints + API keys saved to `data/config.json` (gitignored), masked in the UI
- Auto pattern drawing: heuristic detection (S/R levels, triangles, double top/bottom, head & shoulders, flags) drawn on live + backtest charts with projected entry/stop/target
- Manual trendlines: two-click draw on the chart, saved per symbol
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
