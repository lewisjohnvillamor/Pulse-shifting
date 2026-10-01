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

## Alpha lab: six researched effects, tested out of sample

`python -m app.alphalab <test>` downloads data from Binance's public
spot API and archive (`data.binance.vision`, including perp funding) into
`data/research/alphalab/`. It tests each idea on a 20-coin universe
(2020-09 onward). Parameters come from the literature or from the first half
only, and the verdict is taken on the **second, unseen half**, net of
~12 bps per unit of turnover.

| # | Idea (source) | Out-of-sample result | Verdict |
|---|---|---|---|
| 1 | Trend following + volatility targeting (EMA 8/32, 16/64, 32/128 days; 50% vol target) | Sharpe **1.17** vs 0.74 equal-weight and 1.04 BTC buy&hold; max DD **−24%** vs −70% / −53%; all 9 parameter variants Sharpe 1.02–1.24. 4h: 0.45 vs 0.33; 1h: no edge | **Shipped** |
| 2 | Cross-sectional 3-week momentum, weekly rotation (Liu, Tsyvinski & Wu) | Rank IC ≈ 0 (+0.002); top-4 alone lags buy&hold; with trend filter it beat random picks in the second half (98th pct) but not the first (62nd) | Not robust |
| 3 | Hour-of-day / weekday seasonality (21–23 UTC window) | Hourly means don't persist (corr −0.12); window +7 bps/day gross vs 24 bps cost; weekday signs flip | Rejected |
| 4 | BTC → altcoin lead-lag | IC ≈ 0 at 1m–1h, sign-unstable; +0.7 bps after big BTC moves vs 20 bps cost (published lags are seconds) | Rejected |
| 5 | Perp funding rate | Adds little beyond trend (partial IC +0.030 → +0.015); "flat when crowded" cut Sharpe 1.17 → 0.81 | Not adopted |
| 6 | Gradient-boosted trees vs factor model | Mixed per timeframe; differences within noise (±0.02–0.04 IC) | Not adopted |

Survivorship bias: the universe holds coins that still trade today, which
flatters every long-only result, buy&hold included.

**What shipped:**

- `trend_vol_target` strategy: per-coin, with vol-targeted `levels.size`.
  It abstains below 4h.
- `GET /api/portfolio/trend` and the **Trend portfolio** panel: today's
  target weights across the universe plus pinned symbols.
- The chart overlay can draw either the AI model or the Trend strategy.

The AI model (`ai_regime_fusion`) and Trend do different jobs. On unseen
daily data the AI model earned more (+94% avg, −53% avg max DD, 6 symbols).
Trend earned less with far smaller drawdowns (+32%, −21%, profitable 6/6).
Gating the AI model with trend did not improve it.

## Forward test (live paper tracking)

Backtests can be overfit by accident; a forward test cannot. While the API
runs, it records once per closed daily candle (checked hourly, idempotent):

- the **Trend portfolio**'s target weights and closes;
- the **AI model**'s daily signal for BTC, ETH, SOL, BNB, XRP and ADA.

Records go to `data/forward/` (gitignored). Nothing is backfilled, so the
log only holds genuinely out-of-sample decisions. The **Forward test** panel
(and `GET /api/forward`) scores the records with the backtest's accounting
(held close to close, 12 bps per unit of turnover). It shows live results
against the expectations from the out-of-sample backtest: Sharpe 1.17,
max DD −24%, ~25%/yr, and AI daily IC +0.093.

**Stop rule, fixed in advance.** After 180 days, a live Sharpe below 0.3
means the edge is treated as gone. So does a drawdown beyond −35% at any
time.

```bash
python -m app.forward record   # record now (e.g. from cron if the API isn't running)
python -m app.forward report   # live vs expected summary
```
