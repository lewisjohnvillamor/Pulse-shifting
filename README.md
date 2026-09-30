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


## Evolutionary strategy discovery

PulseShift can now mutate any parameterized strategy from the Strategy Arena. The first strategy designed for this workflow is **AI Regime Fusion v1**.

The discovery loop is deliberately constrained:

1. Load up to 1,000 historical candles.
2. Split them chronologically into 60% train, 20% validation, and 20% holdout test.
3. Generate bounded parameter mutations from the current strategy genome.
4. Rank each generation using return, drawdown, profit factor, trade-count, and train/validation stability.
5. Keep the strongest elites and mutate them again.
6. Reveal the holdout test only after the final champion has been selected.
7. Require a manual **Promote champion** action before evolved parameters replace the current local strategy configuration.

Default UI experiment:

- 4 generations
- 16 candidates per generation
- 20% elite retention
- 35% mutation probability per gene
- 10% parameter-range mutation scale
- deterministic seed 42

Evolution runs in a worker thread so the live Binance market feed remains responsive. Promotion only changes the local paper-trading strategy configuration; it does not enable live exchange execution.

API:

```text
POST /api/evolution/run
POST /api/evolution/promote/{strategy_id}
```

The holdout result is evidence for comparison, not proof of future profitability. Re-run promising genomes across different symbols, periods, intervals, and market regimes before treating them as robust.

## Walk-forward research (CLI)

The in-app evolution tunes on a single market. For a sturdier read, score a
strategy on many symbol/interval pairs at once and only on data it was not
tuned on:

```bash
# Score current params on BTC/ETH/SOL/BNB x 5m/15m/1h/4h, 2 unseen blocks
python -m app.research --strategy ai_regime_fusion

# Re-tune inside each fold, then score the next (unseen) block
python -m app.research --strategy ai_regime_fusion --tune

# Try overrides, or stop the tuner touching some params
python -m app.research --set trail_atr=3 regime_ema=100
python -m app.research --tune --freeze trail_atr regime_ema
```

Each market is split into `folds + 1` chronological blocks. Fold *k* tunes on
everything before block *k+1* and reports block *k+1* against buy-and-hold.
Candles are cached under `data/research/`. Tuned params are printed (and
saved with `--json`), never written to the live strategy config.
Params marked `tunable=False` (e.g. fees) are never mutated.

## AI Regime Fusion v3: per-timeframe model and chart signals

`ai_regime_fusion` is driven by a small model per timeframe
(`app/models/fusion_model.json`), built from volatility-normalised
OHLCV and taker-buy order-flow features (`app/features.py`).

Research findings behind it (`python -m app.modeling eval`, 6 symbols,
walk-forward): the same features point opposite ways on different
timeframes. 1m/5m candles **mean-revert**, 15m follows **order flow**, and
4h/1d **trend**. One pooled model therefore cancels itself out. Each
timeframe keeps only factors whose sign held across symbols and across
time in its training data.

Out-of-sample reliability (IC = correlation of prediction with the
realised 12-candle move) is stored in the model and shown on the chart:

| tf | 1m | 5m | 15m | 1h | 4h | 1d |
|----|----|----|----|----|----|----|
| IC | +0.041 | 0.000 | +0.024 | −0.058 | −0.002 | +0.093 |

The strategy abstains where IC is not positive, and only enters when the
predicted move beats the round trip (`fee_bps` x 2 + spread). At 10 bps
taker fees only daily candles clear costs. At ~2 bps (maker/VIP) 15m
became profitable in 6/6 unseen markets. Set `fee_bps` to your real
fee.

```bash
python -m app.modeling eval                     # predictive power per timeframe
python -m app.modeling eval-trade --fee-bps 2   # unseen-data backtest by timeframe
python -m app.modeling train                    # refit and rewrite the model file
```

**Chart signals.** The main chart has a timeframe selector
(1m…1d). It calls `GET /api/signals?symbol=&interval=` and draws:

- BUY / EXIT arrows from a replay of the visible window, with the % result
  on each exit;
- green/red dots for **setups**: the model's strongest 10% leans, which
  do not necessarily beat fees;
- `AI ENTRY` / `AI STOP` / `AI TARGET` price lines for the current plan.
  Lines are solid when actionable and dashed when watch-only.

The strip under the chart shows the timeframe's reliability. The previous
rule-based version stays available as `ai_regime_fusion_v2`.
