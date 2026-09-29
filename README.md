# PulseShift

Local-first crypto paper-trading and algorithm research lab.

PulseShift runs on your own computer, pulls public BTC/USDT market data from Binance, simulates trades locally, records strategy decisions with confidence, and lets you compare algorithm behavior from a browser.

## MVP

- Local web UI at `http://127.0.0.1:8000`
- BTC/USDT public Binance market data
- Live paper account with simulated fills and fees
- EMA Momentum strategy with transparent confidence factors
- Manual paper Buy / Sell / Close controls
- Historical candle loading and quick backtest endpoint
- Strategy metrics: return, trades, win rate, drawdown, profit factor
- API-first strategy interface ready for Jev, Laya, and future research policies
- No Binance account, API key, cloud database, or VPS required

## Quick start

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

Or run `start.bat`.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Open:

```
http://127.0.0.1:8000
```

## Architecture

```
Browser UI
   |
FastAPI
   |
   +-- Binance public market-data adapter
   +-- Strategy engine
   +-- Confidence model
   +-- Local paper broker
   +-- Replay/backtest runner
```

The MVP deliberately keeps market data and account state in memory. Persistent Parquet/DuckDB recording and a time-controlled replay screen are the next storage/replay milestones once the live paper workflow is validated.

## Safety

PulseShift is a research and simulation tool. The current build does **not** place real exchange orders. Keep the paper layer separate from any future live broker adapter and validate strategies out-of-sample before considering real-capital use.
