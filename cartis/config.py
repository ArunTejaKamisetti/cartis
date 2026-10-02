"""Central config. Every external tool runs LIVE when its key is present, else FIXTURE.

Force a mode per tool with e.g. CARTIS_MODE_APIFY=fixture (useful for rehearsals).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
FIXTURES = ROOT / "fixtures"
CURTAIN = ROOT / "curtain"
DATA_DIR.mkdir(exist_ok=True)


def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name, default)
    return v.strip() if isinstance(v, str) and v.strip() else default


ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
CARTIS_MODEL = env("CARTIS_MODEL", "claude-sonnet-5-5")
GNANI_API_KEY = env("GNANI_API_KEY")
APIFY_TOKEN = env("APIFY_TOKEN")
SERPER_API_KEY = env("SERPER_API_KEY")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", str(ROOT / "credentials.json"))
GOOGLE_TOKEN = env("GOOGLE_TOKEN", str(ROOT / "token.json"))
SHEET_ID = env("CARTIS_SHEET_ID")

# Chatur's defaults (overridden by the Memory tab)
DEFAULT_PINCODE = env("CARTIS_PINCODE", "560034")


def mode(tool: str) -> str:
    """Return 'live' or 'fixture' for a tool."""
    forced = env(f"CARTIS_MODE_{tool.upper()}")
    if forced in ("live", "fixture"):
        return forced
    has_key = {
        "apify": bool(APIFY_TOKEN),
        "serper": bool(SERPER_API_KEY),
        "gnani": bool(GNANI_API_KEY),
        "google": Path(GOOGLE_TOKEN).exists() or bool(env("GOOGLE_TOKEN_JSON")),  # `cli auth` first
    }.get(tool, False)
    return "live" if has_key else "fixture"
