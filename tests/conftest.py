"""Tests never touch real services or the real Google Sheet: every tool runs on fixtures."""
import os
import shutil
import tempfile

for tool in ("APIFY", "SERPER", "GNANI", "GOOGLE"):
    os.environ[f"CARTIS_MODE_{tool}"] = "fixture"
os.environ["CARTIS_ORDER_EMAIL"] = "curtain"

import pytest  # noqa: E402


@pytest.fixture()
def fresh_store(monkeypatch):
    """A throwaway local sheet for each test."""
    from cartis import store as st, tools_registry as reg
    tmp = tempfile.mkdtemp()
    from pathlib import Path
    monkeypatch.setattr(st, "_LOCAL", Path(tmp) / "sheets.json")
    s = st.Store()
    monkeypatch.setattr(reg, "store", s)
    yield s
    shutil.rmtree(tmp, ignore_errors=True)
