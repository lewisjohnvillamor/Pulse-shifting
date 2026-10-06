"""Drive PulseShift from your own scripts through its HTTP API.

Start the app first (`./start-dev.sh` or `python -m app.main`), then:

    python examples/api_client.py                 # signals + portfolio + backtest
    python examples/api_client.py --paper-buy 100 # also place a $100 PAPER order

Everything here is paper only: the API has no real-order endpoints.
"""

from __future__ import annotations

import argparse
import os

import httpx

API = os.environ.get("VITE_API_URL", "http://127.0.0.1:8000")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--paper-buy", type=float, default=0.0, help="USD, paper only")
    args = ap.parse_args()

    with httpx.Client(base_url=API, timeout=60) as api:
        print("health:", api.get("/api/health").json())

        # 1) The AI model's signal + chart plan for any timeframe.
        sig = api.get(
            "/api/signals",
            params={"symbol": args.symbol, "interval": args.interval},
        ).json()
        d = sig["decision"]
        print(
            f"\nAI model on {args.symbol} {args.interval}: {d['action']} ({d['regime']})"
        )
        for reason in d["reasons"]:
            print("  -", reason)
        if sig.get("plan"):
            p = sig["plan"]
            print(
                f"  plan {p['side']}: entry {p['entry']:.6g} stop {p['stop']:.6g} target {p['target']:.6g}"
            )
        trades = [m for m in sig["markers"] if m["side"] == "BUY"]
        print(f"  {len(trades)} BUY signals in the last {len(sig['candles'])} candles")

        # 2) Every strategy's live call (1m candles) and its fee-aware edge.
        market = api.get("/api/market", params={"symbol": args.symbol}).json()
        print("\nlive calls (1m):")
        for sid, dec in sorted(market["decisions"].items()):
            edge = market["edges"].get(sid, {})
            print(f"  {sid:<22} {dec['action']:<5} edge {edge.get('verdict', '—')}")

        # 3) Today's validated trend-portfolio weights.
        pf = api.get("/api/portfolio/trend").json()
        print(f"\ntrend portfolio: exposure {pf['exposure_pct']}%")
        for h in pf["holdings"][:5]:
            print(
                f"  {h['symbol']:<10} weight {h['weight_pct']:5.2f}%  trend {h['trend']:+.2f}"
            )

        # 4) Backtest any strategy on any window.
        bt = api.get(
            "/api/backtest",
            params={
                "symbol": args.symbol,
                "interval": args.interval,
                "limit": 1000,
                "strategy": "trend_vol_target",
            },
        ).json()
        print(
            f"\nbacktest trend_vol_target: return {bt['return_pct']}%, "
            f"{bt['round_trips']} trades, max DD {bt['max_drawdown_pct']}%"
        )

        # 5) Optional paper order.
        if args.paper_buy > 0:
            r = api.post(
                "/api/order",
                json={
                    "symbol": args.symbol,
                    "side": "BUY",
                    "amount_usd": args.paper_buy,
                },
            ).json()
            print("\npaper fill:", r["trade"])


if __name__ == "__main__":
    main()
