from __future__ import annotations

import math
from typing import Any

from .market import MarketSnapshot
from .strategy import Decision, atr_pct


def edge_assessment(
    candles: list[dict[str, float]],
    snapshot: MarketSnapshot,
    decision: Decision | None,
    fee_bps: float = 10.0,
    horizon: int = 5,
) -> dict[str, Any]:
    """Spread+fee-aware entry/exit read on the live stream.

    A round trip pays roughly the full quoted spread (half in, half out) plus
    the taker fee twice. Anything the setup projects to move less than that
    cost is a loss even if the direction call is right.
    """
    price = snapshot.price
    cost_bps = snapshot.spread_bps + 2 * fee_bps
    cost_pct = cost_bps / 100.0

    atrp = atr_pct(candles, 14) if candles else 0.0
    atr_value = price * atrp
    # Cheap projection: volatility over a short horizon scaled by conviction.
    expected_pct = atrp * math.sqrt(max(horizon, 1)) * 100.0
    if decision and decision.action == "FLAT":
        expected_pct *= decision.confidence * 0.5
    elif decision:
        expected_pct *= decision.confidence

    net_edge_pct = expected_pct - cost_pct
    breakeven_price = price * (1 + cost_pct / 100.0)

    if decision and decision.action == "LONG" and net_edge_pct > 0:
        verdict = "ENTER_LONG"
    elif decision and decision.action == "SHORT" and net_edge_pct > 0:
        verdict = "ENTER_SHORT"
    elif decision and decision.action != "FLAT":
        verdict = "WAIT_EDGE"  # signal exists but costs eat it
    else:
        verdict = "NO_TRADE"

    # Heuristic win probability: coin flip shifted by conviction and net edge.
    conf = decision.confidence if decision else 0.5
    win_probability = max(
        0.02, min(0.95, 0.45 + 0.3 * (conf - 0.5) + min(net_edge_pct, 3.0) * 0.04)
    )

    return {
        "verdict": verdict,
        "side": decision.action if decision else "FLAT",
        "cost_pct": round(cost_pct, 4),
        "breakeven_price": round(breakeven_price, 8),
        "breakeven_move_pct": round(cost_pct, 4),
        "expected_move_pct": round(expected_pct, 4),
        "net_edge_pct": round(net_edge_pct, 4),
        "atr_pct": round(atrp * 100, 4),
        "suggested_entry": round(snapshot.bid, 8),
        "suggested_stop": round(price - 1.5 * atr_value, 8) if atr_value else None,
        "suggested_target": round(price + 2.0 * atr_value, 8) if atr_value else None,
        "win_probability": round(win_probability, 3),
        "horizon_candles": horizon,
        "note": "Heuristic estimate — not a guarantee. Costs = spread + fees both ways.",
    }
