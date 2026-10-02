"""Workflow tests for the Round 2 capabilities (fixture tools, no network)."""
import json
from pathlib import Path

from cartis import rules
from cartis.tools import curtain, serper
from cartis import tools_registry as reg

FIX = Path(__file__).parent.parent / "fixtures"


# ---- search: more than Myntra -------------------------------------------------------------
def test_google_shopping_listing_is_a_light_record():
    p = serper.normalize_listing({"title": "Roadster Men Coffee Brown Pure Cotton Casual Shirt", "source": "AJIO.com",
                                  "price": "₹799", "rating": 4.2, "ratingCount": 310, "link": "https://x.test/1"})
    assert p["detail"] is False and p["shop"] == "AJIO.com" and p["price"] == 799
    assert p["fabric"] == "Pure Cotton" and p["colour"] == "Brown"      # coffee -> brown family
    assert p["sizes"] == [] and p["rating_breakdown"] == {}


def test_search_shops_merges_myntra_and_other_shops(fresh_store):
    out, ui = reg.h_search({"query": "men white cotton kurta"})
    assert out["myntra"] > 0 and out["other_shops"] > 0
    assert "Myntra" not in [p["shop"] for p in reg.myntra.load_search(out["search_id"]) if p.get("detail") is False]
    assert ui["type"] == "searching" and len(out["shops"]) >= 5


def test_one_shop_failing_does_not_blank_the_search(fresh_store, monkeypatch):
    monkeypatch.setattr(serper, "shop_search", lambda q: (_ for _ in ()).throw(RuntimeError("serper down")))
    out, _ = reg.h_search({"query": "kurta"})
    assert out["myntra"] > 0 and out["other_shops"] == 0 and "google_shopping" in out["errors"]


# ---- rules on light listings --------------------------------------------------------------
def light(**kw):
    base = {"id": "g1", "shop": "Amazon.in", "title": "x", "brand": "x", "price": 700, "rating": 4.2,
            "rating_count": 500, "rating_breakdown": {}, "sizes": [], "detail": False, "fabric": None, "colour": None}
    return {**base, **kw}


def test_light_listing_size_unknown_not_dropped_but_known_wrong_fabric_is():
    prefs = rules.Prefs(size="M", budget_max=1500, fabric_must="cotton")
    out = rules.rank([light(id="g1"), light(id="g2", fabric="Polyester"), light(id="g3", fabric="Cotton")], prefs)
    kept = [p["id"] for p in out["top"]]
    assert "g1" in kept and "g3" in kept and "g2" not in kept
    assert all(p["score_basis"] == "rating only" for p in out["top"])


def test_sort_by_priority():
    ps = [light(id="a", price=900, rating=4.6), light(id="b", price=500, rating=4.0), light(id="c", price=700, rating=4.3)]
    d = {"a": {"eta": "2026-10-08", "eta_kind": "promise"}, "b": {"eta": "2026-10-05", "eta_kind": "promise"},
         "c": {"eta": "2026-10-06", "eta_kind": "estimate", "eta_to": "2026-10-08"}}
    by = lambda s: [p["id"] for p in rules.rank(ps, rules.Prefs(sort_by=s), delivery=d)["top"]]
    assert by("price") == ["b", "c", "a"]
    assert by("arrival")[0] == "b"
    assert by("reviews")[0] == "a" and by("reviews")[-1] == "c"          # promises before estimates


def test_estimate_is_a_range_and_flags_maybe_late():
    est = curtain.delhivery_estimate("560034", "g42")["response"]
    assert est["estimate_from"] <= est["estimate_to"] and est["label"] == "estimate, not a promise"
    d = {"g1": {"eta": "2026-10-04", "eta_to": "2026-10-07", "eta_kind": "estimate"}}
    p = rules.rank([light()], rules.Prefs(need_by="2026-10-05"), delivery=d)["top"][0]
    assert p["may_be_late"] is True and p["eta_kind"] == "estimate"


# ---- address (Delhivery caps 6, 7, 8) -----------------------------------------------------
def test_address_valid_is_saved_and_incomplete_names_the_gap(fresh_store):
    bad, _ = reg.h_address({"raw": "HSR layout Bangalore", "locality": "HSR Layout", "city": "Bengaluru"})
    assert bad["response"]["validation"]["status"] == "INCOMPLETE"
    assert "pincode" in bad["response"]["validation"]["missing"] and not fresh_store.memory().get("address")
    ok, ui = reg.h_address({"raw": "4B, 12th Main, HSR Layout, Bengaluru 560102", "house": "4B", "street": "12th Main",
                            "locality": "HSR Layout", "city": "Bengaluru", "pincode": "560102"})
    assert ok["response"]["validation"]["status"] == "VALID" and ok["response"]["verification"]["has_delivered"]
    assert fresh_store.memory()["pincode"] == "560102" and "HSR Layout" in fresh_store.memory()["address"]
    assert ui["type"] == "address_checked"


def test_no_payment_button_without_an_address(fresh_store):
    out, ui = reg.h_request_confirmation({"merchant": "Myntra", "amount_inr": 799, "product_id": "x", "size": "M"})
    assert "address" in out["error"] and ui is None


# ---- Pine Labs (caps 15, 16, 17) ----------------------------------------------------------
def test_payment_runs_grantex_mandate_balance_capture(fresh_store):
    res, ui = reg.tap_confirm({"merchant": "Myntra", "amount_inr": 799, "product_id": "p1", "size": "M", "title": "Shirt"})
    assert res["grantex"]["response"]["decision"] == "ALLOW"
    assert res["balance"]["response"]["status"] == "ACTIVE" and res["balance"]["response"]["blocked_paise"] == 79900
    assert res["payment"]["response"]["data"]["status"] == "PROCESSED"
    assert ui["type"] == "checkout" and fresh_store.rows("Cart")


def test_grantex_denies_above_the_cap_and_no_mandate_is_made(fresh_store):
    res, ui = reg.tap_confirm({"merchant": "Myntra", "amount_inr": 9999, "product_id": "p2", "size": "M"})
    assert res["grantex"]["response"]["decision"] == "DENY" and "response" not in res
    assert ui["data"]["denied"] is True


def test_p3p_acceptance_tolerates_shop_name_variants():
    assert curtain.pine_labs_p3p_acceptance("AJIO.com")["response"]["accepts_p3p"]
    assert not curtain.pine_labs_p3p_acceptance("Meesho")["response"]["accepts_p3p"]


# ---- wishlist + history -------------------------------------------------------------------
def test_wishlist_heart_round_trip(fresh_store):
    reg._SEEN["w1"] = {"id": "w1", "title": "Linen Shirt", "price": 999, "url": "u"}
    assert reg.toggle_wishlist("w1", True)["count"] == 1
    assert reg.toggle_wishlist("w1", True)["count"] == 1               # no duplicates
    prof, _ = reg.h_get_profile({})
    assert prof["wishlist"][0]["title"] == "Linen Shirt"
    assert reg.toggle_wishlist("w1", False)["count"] == 0


def test_import_learns_sizes_and_spend(fresh_store):
    out, ui = reg.h_import({})
    assert out["behaviour"]["orders_seen"] == 2 and fresh_store.memory().get("orders_seen") == "2"
    assert ui["type"] == "history_imported"


def test_rank_shows_every_passing_product(fresh_store):
    s, _ = reg.h_search({"query": "men white cotton kurta"})
    fresh_store.upsert_kv("Memory", "size_top", "L")
    out, ui = reg.h_rank({"search_id": s["search_id"], "overrides": {"colour": "white", "fabric_must": "cotton"}})
    assert ui["data"]["counts"]["passed"] == len(ui["data"]["top"]) == out["shown_on_screen"]
    assert {c["card"] for c in out["all_on_screen"]} == {f"c{i + 1}" for i in range(out["shown_on_screen"])}
    assert any(p["detail"] is False for p in ui["data"]["top"])          # other shops made it on screen


# ---- real receipt email --------------------------------------------------------------------
def test_receipt_email_is_built_sent_and_read_back(fresh_store, monkeypatch):
    import base64
    from cartis.tools import gmail
    sent = {}

    class FakeGmail:
        def users(self): return self
        def getProfile(self, userId): return self
        def messages(self): return self
        def send(self, userId, body): sent["raw"] = body["raw"]; return self
        def list(self, **kw): return self
        def execute(self): return {"emailAddress": "arun@example.com", "id": "m1", "messages": [{"id": "m1"}]}

    monkeypatch.setattr("cartis.google_auth.service", lambda *a, **k: FakeGmail())
    monkeypatch.setattr(gmail, "_message_text", lambda gm, mid: ({"subject": "Cartis · Order confirmed · Myntra · 1300000-1-1",
                                                                   "from": "Cartis <arun@example.com>"}, "Order 1300000-1234567-1234567 ₹612"))
    out = gmail.send_receipt({"order_id": "1300000-1234567-1234567", "amount": 612.0, "shop": "Myntra", "title": "Linen Shirt",
                              "size": "M", "payment_id": "v1-pay-1", "url": "https://myntra.com/x"})
    import email
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    html = next(p.get_payload(decode=True).decode() for p in msg.walk() if p.get_content_type() == "text/html")
    assert out["to"] == "arun@example.com" and msg["To"] == "arun@example.com" and "Order confirmed" in out["subject"]
    assert "Linen Shirt" in html and "v1-pay-1" in html and "View on Myntra" in html
    back = gmail.read_receipt("1300000-1234567-1234567", tries=1)
    assert back["found"] and back["source"] == "live:gmail"


def test_address_endpoint_and_payments_list(fresh_store):
    from fastapi.testclient import TestClient
    from cartis import server
    c = TestClient(server.app)
    r = c.post("/address", json={"house": "4B", "street": "12th Main", "locality": "HSR Layout", "city": "Bengaluru",
                                 "pincode": "560102"}).json()
    assert r["response"]["validation"]["status"] == "VALID"
    reg.tap_confirm({"merchant": "Myntra", "amount_inr": 612, "product_id": "p9", "size": "M", "title": "Shirt"}, "UPI ReservePay")
    pays = c.get("/payments").json()
    assert pays["history"][0]["status"] == "PROCESSED" and pays["history"][0]["agent_check"] == "ALLOW"
    assert pays["address"] and "HSR Layout" in pays["address"]
