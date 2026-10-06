"""Plug your own AI model (or an LLM) into PulseShift.

The built-in **Laya** strategy POSTs recent candles to `LAYA_URL`
(default http://127.0.0.1:8791/decide) and turns the JSON reply into a
signal — live on the Strategy board, on the chart, and in backtests.
This file is a complete, dependency-free server you can edit:

    python examples/my_ai_server.py          # serves http://127.0.0.1:8791/decide

Then pick "Laya" on the Strategy board, or backtest it:

    curl "localhost:8000/api/backtest?symbol=BTCUSDT&interval=1h&strategy=laya"

Request body:  {"spread_bps": 1.2, "closes": [...60 floats], "volumes": [...]}
Reply:         {"action": "LONG"|"SHORT"|"FLAT", "confidence": 0..1,
                "regime": "...", "reasons": ["..."]}

Replace `decide()` with your model: a scikit-learn / PyTorch model, or a call
to an LLM API that returns that JSON. For a hosted endpoint with an API key,
use the **Jev** adapter instead: set its URL + key in the app's settings gear
(stored in data/config.json, gitignored). OpenAI-style chat responses whose
message content is that JSON are understood too.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def decide(closes: list[float], volumes: list[float], spread_bps: float) -> dict:
    """Toy model: 10-candle momentum, confidence from its strength."""
    if len(closes) < 11:
        return {"action": "FLAT", "confidence": 0.0, "reasons": ["not enough data"]}
    momentum = closes[-1] / closes[-11] - 1
    confidence = min(0.95, 0.5 + abs(momentum) * 20)
    action = "LONG" if momentum > 0.002 else "SHORT" if momentum < -0.002 else "FLAT"
    return {
        "action": action,
        "confidence": round(confidence, 3),
        "regime": "TRENDING" if abs(momentum) > 0.01 else "RANGING",
        "reasons": [f"10-candle momentum {momentum * 100:+.2f}%"],
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        reply = decide(
            body.get("closes", []), body.get("volumes", []), body.get("spread_bps", 0.0)
        )
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # keep the console quiet
        pass


if __name__ == "__main__":
    print("my AI model listening on http://127.0.0.1:8791/decide")
    HTTPServer(("127.0.0.1", 8791), Handler).serve_forever()
