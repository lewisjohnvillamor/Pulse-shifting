"""Runtime configuration from environment variables and an optional `.env`.

Nothing here is secret: PulseShift talks only to Binance *public* endpoints
and needs no exchange credentials. Bring-your-own AI provider keys are
stored separately in data/config.json (gitignored) via the settings UI.

Copy `.env.example` to `.env` to override the defaults. Vite reads the
same file for `VITE_*` variables (see web/vite.config.ts).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    # Minimal fallback so a missing python-dotenv never breaks startup.
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()

HOST = os.environ.get("PULSESHIFT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PULSESHIFT_PORT", "8000"))
# Origins allowed to call the API (the Vite dev server by default).
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "PULSESHIFT_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if o.strip()
]
DATA_DIR = Path(os.environ.get("PULSESHIFT_DATA_DIR", ROOT / "data")).resolve()
