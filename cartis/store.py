"""Google Sheets store with 5 tabs: Memory, ReviewRules, Wishlist, Cart, Orders.

LIVE: reads/writes the sheet at CARTIS_SHEET_ID with the shared Google OAuth token.
FIXTURE: mirrors the same tabs in data/sheets.json so the terminal demo runs offline.
"""
from __future__ import annotations

import json
import ssl
import threading
from datetime import datetime
from typing import Any

from . import config

TABS: dict[str, list[str]] = {
    "Memory": ["key", "value", "note"],
    "ReviewRules": ["rule", "weight", "note"],
    "Wishlist": ["added_at", "product_id", "title", "price", "size", "url", "why"],
    "Cart": ["added_at", "product_id", "title", "price", "size", "url", "shop"],
    "Orders": ["logged_at", "order_id", "shop", "items", "amount", "status", "email_id",
               "brand", "size", "outcome", "watch_until"],
}

SEED: dict[str, list[list[Any]]] = {
    "Memory": [
        ["name", "Arun", "shopper's name"],
        ["size_top", "", "learned from order emails on first run"],
        ["size_bottom", "", "learned from order emails on first run"],
        ["budget_max", "1500", "INR per item"],
        ["avoid_fabrics", "polyester", "comma-separated"],
        ["pincode", config.DEFAULT_PINCODE, "delivery pincode"],
        ["preferred_shop", "Myntra", ""],
    ],
    "ReviewRules": [
        ["one_star_share", "0.5", "weight on share of 1-star ratings"],
        ["two_three_star_share", "0.3", "weight on share of 2-3 star ratings"],
        ["no_comment_share", "0.2", "weight on share of ratings with no written review"],
        ["min_ratings", "30", "drop products with fewer ratings than this"],
    ],
}

_LOCAL = config.DATA_DIR / "sheets.json"


_TL = threading.local()   # httplib2 is not thread-safe: one Sheets client per thread


class _Retry:
    """Wraps the Sheets client so a dropped TLS connection (e.g. "SSL record layer failure")
    is retried once on a fresh connection instead of crashing the request."""

    def __init__(self, store: "Store", chain=()):
        self._store, self._chain = store, chain

    def __getattr__(self, name):
        return _Retry(self._store, self._chain + ((name, None),))

    def __call__(self, *a, **kw):
        name, _ = self._chain[-1]
        return _Retry(self._store, self._chain[:-1] + ((name, (a, kw)),))

    def execute(self):
        for attempt in (1, 2):
            obj = self._store._client(fresh=attempt == 2)
            try:
                for name, args in self._chain:
                    obj = getattr(obj, name)
                    if args is not None:
                        obj = obj(*args[0], **args[1])
                return obj.execute(num_retries=2)
            except (ssl.SSLError, ConnectionError, OSError) as e:
                if attempt == 2:
                    raise
                print(f"[sheets] connection dropped ({e}); retrying on a fresh connection")


class Store:
    def __init__(self) -> None:
        self.live = config.mode("google") == "live" and bool(config.SHEET_ID)
        self._svc = _Retry(self) if self.live else None
        if self.live:
            pass
        elif not _LOCAL.exists():
            self._save_local({t: [h] + SEED.get(t, []) for t, h in TABS.items()})

    def _client(self, fresh: bool = False):
        if fresh or getattr(_TL, "sheets", None) is None:
            from .google_auth import service

            _TL.sheets = service("sheets", "v4").spreadsheets()
        return _TL.sheets

    # ---------- low level ----------
    def _load_local(self) -> dict[str, list[list[Any]]]:
        if not _LOCAL.exists():   # first run, or the data folder was cleared while running
            _LOCAL.parent.mkdir(parents=True, exist_ok=True)
            self._save_local({t: [h] + SEED.get(t, []) for t, h in TABS.items()})
        return json.loads(_LOCAL.read_text())

    def _save_local(self, d: dict) -> None:
        _LOCAL.write_text(json.dumps(d, indent=2, ensure_ascii=False))

    def rows(self, tab: str) -> list[dict[str, Any]]:
        if self.live:
            res = self._svc.values().get(spreadsheetId=config.SHEET_ID, range=f"{tab}!A1:Z").execute()
            values = res.get("values", [])
        else:
            values = self._load_local().get(tab, [TABS[tab]])
        if not values:
            return []
        head = values[0]
        return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in values[1:]]

    def append(self, tab: str, row: dict[str, Any]) -> None:
        vals = [str(row.get(h, "")) for h in TABS[tab]]
        if self.live:
            self._svc.values().append(
                spreadsheetId=config.SHEET_ID, range=f"{tab}!A1",
                valueInputOption="USER_ENTERED", body={"values": [vals]},
            ).execute()
        else:
            d = self._load_local()
            d.setdefault(tab, [TABS[tab]]).append(vals)
            self._save_local(d)

    def upsert_kv(self, tab: str, key: str, value: str, note: str = "") -> None:
        keycol = TABS[tab][0]
        rows = self.rows(tab)
        idx = next((i for i, r in enumerate(rows) if r[keycol] == key), None)
        if idx is None:
            self.append(tab, {keycol: key, TABS[tab][1]: value, "note": note})
            return
        if self.live:
            self._svc.values().update(
                spreadsheetId=config.SHEET_ID, range=f"{tab}!B{idx + 2}",
                valueInputOption="USER_ENTERED", body={"values": [[value]]},
            ).execute()
        else:
            d = self._load_local()
            d[tab][idx + 1][1] = value
            self._save_local(d)

    def update_row(self, tab: str, key_col: str, key: str, updates: dict[str, Any]) -> bool:
        head = TABS[tab]
        rows = self.rows(tab)
        idx = next((i for i, r in enumerate(rows) if str(r.get(key_col)) == str(key)), None)
        if idx is None:
            return False
        if self.live:
            data = [{"range": f"{tab}!{chr(65 + head.index(k))}{idx + 2}", "values": [[str(v)]]}
                    for k, v in updates.items() if k in head]
            self._svc.values().batchUpdate(spreadsheetId=config.SHEET_ID,
                                           body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
        else:
            d = self._load_local()
            row = d[tab][idx + 1]
            row += [""] * (len(head) - len(row))
            for k, v in updates.items():
                if k in head:
                    row[head.index(k)] = str(v)
            d[tab][idx + 1] = row
            self._save_local(d)
        return True

    # ---------- typed helpers ----------
    def memory(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.rows("Memory")}

    def review_rules(self) -> dict[str, float]:
        out = {}
        for r in self.rows("ReviewRules"):
            try:
                out[r["rule"]] = float(r["weight"])
            except (TypeError, ValueError):
                pass
        return out

    @staticmethod
    def now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")


def reset_profile() -> None:
    """Rehearsal reset: Memory + ReviewRules back to the seed, Cart/Wishlist emptied. Orders kept."""
    st = Store()
    for tab in ("Memory", "ReviewRules", "Cart", "Wishlist"):
        values = [TABS[tab]] + SEED.get(tab, [])
        if st.live:
            st._svc.values().clear(spreadsheetId=config.SHEET_ID, range=f"{tab}!A1:Z").execute()
            st._svc.values().update(spreadsheetId=config.SHEET_ID, range=f"{tab}!A1", valueInputOption="RAW",
                                    body={"values": values}).execute()
        else:
            d = st._load_local()
            d[tab] = values
            st._save_local(d)
    print("Reset: Memory, ReviewRules, Cart, Wishlist back to defaults (Orders kept).")


def setup_sheet() -> None:
    """Create the 5 tabs with headers + seed rows in the live sheet (idempotent)."""
    from .google_auth import service

    ss = service("sheets", "v4").spreadsheets()
    meta = ss.get(spreadsheetId=config.SHEET_ID).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}
    reqs = [{"addSheet": {"properties": {"title": t}}} for t in TABS if t not in existing]
    if reqs:
        ss.batchUpdate(spreadsheetId=config.SHEET_ID, body={"requests": reqs}).execute()
    for t, head in TABS.items():
        cur = ss.values().get(spreadsheetId=config.SHEET_ID, range=f"{t}!A1:Z1").execute().get("values")
        if cur and cur[0] != head:   # columns added since the tab was created: rewrite the header row
            ss.values().update(spreadsheetId=config.SHEET_ID, range=f"{t}!A1", valueInputOption="RAW",
                               body={"values": [head]}).execute()
        if not cur:
            ss.values().update(
                spreadsheetId=config.SHEET_ID, range=f"{t}!A1", valueInputOption="RAW",
                body={"values": [head] + SEED.get(t, [])},
            ).execute()
    print("Sheet ready:", ", ".join(TABS))
