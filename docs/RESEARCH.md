# PulseShift research notes

Everything the app ships was tested on data it was not tuned on. This file
records how, and what the results were, so you can reproduce or challenge
them. The README has the user guide; this is the evidence.

**Reading the numbers.** *IC* is the correlation between a prediction and
the move that actually followed: 0 means no skill, and +0.05 to +0.10 is
respectable for price-only crypto signals. *Out of sample* / *unseen* means
the data came after everything used to choose the settings. *Sharpe* is
return per unit of risk (higher is better). *Max DD* is the worst
peak-to-trough drop.

Contents:

1. [Walk-forward research (CLI)](#walk-forward-research-cli)
2. [Evolutionary strategy discovery](#evolutionary-strategy-discovery)
3. [AI Regime Fusion v3](#ai-regime-fusion-v3-per-timeframe-model-and-chart-signals)
4. [Alpha lab: six researched effects](#alpha-lab-six-researched-effects-tested-out-of-sample)
5. [Forward test](#forward-test-live-paper-tracking)

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

## Evolutionary strategy discovery

PulseShift can now mutate any parameterized strategy from the Strategy Arena. It works with every strategy that declares editable `specs`.

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

### Expanded universe (38 coins)

The trend portfolio and forward test now cover 38 coins
(`alphalab.EXPANDED_UNIVERSE`). The rule for picking them: top-70 USDT
pairs by volume, no stablecoins or tokenized stocks, listed on Binance by
2023-03-31. The original 20 are always included. Out of sample
(`python -m app.alphalab trend --universe expanded`), from 2023-09-17:

| | 20 coins | 38 coins |
|---|---|---|
| Sharpe | 1.17 | **1.35** |
| Max drawdown | −23.7% | **−22.1%** |
| In-sample Sharpe | 1.10 | 1.28 |
| Sharpe range, 9 variants | 1.02–1.24 | 1.12–1.40 |

Survivorship check: on only the 18 added coins, buy&hold had Sharpe 0.74
and −84% max DD, while trend had 1.40 and −22%. The gain comes from
diversification, not from picking past winners. Selecting by today's
volume still adds bias, so treat absolute returns as optimistic.

The forward test tags records with the universe they were computed on and
only scores the current one, because changing the coin set restarts the
clock. Pinned coins are no longer mixed into the tracked portfolio. AI
daily signals are logged for all 38 coins and scored in two groups:

- the 6 training coins, expected IC +0.093;
- the 32 others, expected IC +0.044 (positive for 21/32 on the same
  unseen period).
