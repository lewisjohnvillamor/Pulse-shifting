# Strategy plugins

Drop files in this directory to add strategies — yours or other people's.
Restart the API or `POST /api/strategies/reload` to pick them up. Plugins get
an id, appear in the Strategy board, the params editor (if they expose
`specs`), and the backtest selector.

**Trust note:** `.py` plugins run with full Python privileges — only install
code you trust or have reviewed.

## JSON strategy (no code)

Reuse a builtin template (`ema_momentum`, `mean_reversion`, `breakout`) with
your own parameters:

```json
{
  "id": "fast_scalp",
  "name": "Fast Scalp",
  "kind": "ema_momentum",
  "description": "Tuned for scalping: quick EMAs, low threshold.",
  "params": { "fast": 5, "slow": 13, "lookback": 3, "threshold": 0.55 }
}
```

## Python strategy

A `.py` file must expose `STRATEGY` — an instance or class with a
`decide(candles, spread_bps) -> Decision` method. Easiest is subclassing
`app.strategy.Strategy` (gives you editable `specs` params for free):

```python
from app.strategy import Decision, ParamSpec, Strategy, ema

class MyStrategy(Strategy):
    id = "my_strategy"
    name = "My Strategy"
    description = "What it does."
    specs = [ParamSpec("period", "int", 10, 2, 100, 1, "Period")]

    def decide(self, candles, spread_bps=0.0):
        closes = [float(c["close"]) for c in candles]
        ...
        return Decision("LONG", 0.7, "TRENDING", 0.8, 0.9, ["why"])

STRATEGY = MyStrategy
```

External models (Jev, Laya, or any framework) plug in the same way: wrap the
model's signal in `decide()` and ship it as a `STRATEGY`.
