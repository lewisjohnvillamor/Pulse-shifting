"""Test a strategy the honest way: on data it was not tuned on.

    python examples/walk_forward_my_strategy.py                      # example plugin
    python examples/walk_forward_my_strategy.py strategies/laya.py   # any plugin file

Splits each market into 3 chronological blocks and scores blocks 2 and 3
separately (each after the earlier history), against buy-and-hold, net of
fees. No network needed after the first run (candles are cached in
data/research/). For tuning + walk-forward use `python -m app.research --tune`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.registry import _load_python_plugin  # noqa: E402
from app.research import load_candles, walk_forward  # noqa: E402

MARKETS = [(s, i) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT") for i in ("1h", "4h")]


def main() -> None:
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else ROOT / "examples/strategies/rsi_dip.py"
    )
    strategy = _load_python_plugin(path)
    if strategy is None:
        raise SystemExit(f"{path} does not define STRATEGY")
    print(f"strategy: {strategy.name}  ({path})")
    data = {m: load_candles(m[0], m[1], 3000) for m in MARKETS}
    report = walk_forward(
        strategy,
        dict(getattr(strategy, "params", {})),
        data,
        folds=2,
        tune_params=False,
    )
    verdict = (
        "beats buy&hold"
        if report["avg_test_return_pct"] > report["avg_test_buy_hold_pct"]
        else "does NOT beat buy&hold"
    )
    print(f"\nunseen-data verdict: {verdict}")


if __name__ == "__main__":
    main()
