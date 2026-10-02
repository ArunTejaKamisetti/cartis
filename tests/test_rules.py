import json
from pathlib import Path

from cartis import rules
from cartis.tools.myntra import normalize

FIX = json.loads((Path(__file__).parent.parent / "fixtures" / "myntra_search.json").read_text())
PRODUCTS = [normalize(p) for p in FIX]
BASE = rules.Prefs(size="L", budget_max=1500, avoid_fabrics=["polyester"])


def test_normalize_breakdown_and_sizes():
    p = PRODUCTS[0]
    assert p["rating_breakdown"] == {5: 410, 4: 120, 3: 30, 2: 12, 1: 18}
    assert {"label": "L", "available": True} in p["sizes"]
    assert p["fabric"] == "Pure Cotton"


def test_filters_drop_each_rule():
    out = rules.rank(PRODUCTS, BASE)
    reasons = {d["title"]: " ".join(d["reasons"]) for d in out["dropped"]}
    assert "over budget" in reasons["Men Linen Blend Kurta"]            # 2499 > 1500
    assert "polyester" in reasons["Men Printed Kurta"]                  # avoided fabric
    assert "ratings" in reasons["Men Self-Design Kurta"]                # 21 < 30
    assert "size L" in reasons["Men Pintuck Cotton Kurta"]              # no L
    assert len(out["top"]) == out["counts"]["passed"] == 8   # every passing product is shown


def test_score_prefers_low_one_star_and_high_comments():
    good = {"rating_breakdown": {5: 90, 4: 8, 3: 1, 2: 0, 1: 1}, "rating_count": 100, "reviews_count": 80}
    bad = {"rating_breakdown": {5: 60, 4: 10, 3: 10, 2: 5, 1: 15}, "rating_count": 100, "reviews_count": 10}
    w = rules.DEFAULT_WEIGHTS
    assert rules.review_score(good, w)[0] > rules.review_score(bad, w)[0]


def test_ask_only_if_top3_changes():
    # Budget answer that changes nothing -> don't ask
    same = rules.question_value(PRODUCTS, BASE, [{"label": "1500 is fine", "budget_max": 1500}])
    assert same["should_ask"] is False
    # Allowing a big budget + rayon avoidance reshuffles the top 3 -> ask
    diff = rules.question_value(PRODUCTS, BASE, [{"label": "up to 3000, no cotton",
                                                  "budget_max": 3000, "avoid_fabrics": "polyester,pure cotton,khadi"}])
    assert diff["worst_overlap"] < 0.5 and diff["should_ask"] is True


def test_custom_weights_change_scores():
    heavy = BASE.override(weights={"one_star_share": 0.9, "no_comment_share": 0.0})
    a = rules.rank(PRODUCTS, BASE)["top"][0]["review_score"]
    b = rules.rank(PRODUCTS, heavy)["top"][0]["review_score"]
    assert a != b


def test_breakdown_survives_json_roundtrip():
    p = json.loads(json.dumps(PRODUCTS[0]))  # keys become strings, as in the search cache
    assert rules.review_breakdown(p)["one_star_share"] > 0


def test_normalize_real_apify_shape():
    raw = json.loads((Path(__file__).parent.parent / "fixtures" / "myntra_live_sample.json").read_text())[0]
    p = normalize(raw)
    assert p["id"] == "33559379" and p["fabric"] == "Cotton (Blended)" and p["fit"] == "Straight"
    assert p["rating_breakdown"][1] == 104 and p["image"].startswith("https://")
    flat = {k: v for k, v in raw.items() if k != "specifications"} | {"specifications.Fabrics": "Cotton Silk"}
    assert normalize(flat)["fabric"] == "Cotton Silk"


# ---- Round 2 promises ----
def test_late_items_dropped_before_showing_and_unknown_sinks():
    ids = [p["id"] for p in rules.rank(PRODUCTS, BASE)["top"]]
    delivery = {pid: {"serviceable": True, "eta": "2026-10-03"} for pid in ids}
    delivery[ids[0]] = {"serviceable": True, "eta": "2026-10-09"}      # too late
    delivery[ids[1]] = {"serviceable": True, "eta": None}              # unknown
    out = rules.rank(PRODUCTS, BASE.override(need_by="2026-10-04"), delivery=delivery)
    top = [p["id"] for p in out["top"]]
    assert ids[0] not in top                                           # late never shown
    assert any("after 2026-10-04" in " ".join(d["reasons"]) for d in out["dropped"])
    known = [p for p in out["top"] if p["eta"]]
    assert out["top"][: len(known)] == known                           # dated cards first


def test_nothing_in_time_offers_wait_option():
    delivery = {p["id"]: {"serviceable": True, "eta": "2026-10-12"} for p in PRODUCTS}
    out = rules.rank(PRODUCTS, BASE.override(need_by="2026-10-04"), delivery=delivery)
    assert out["top"] == [] and out["nothing_in_time"] and out["if_you_can_wait"]


def test_review_counts_on_card():
    c = rules.rank(PRODUCTS, BASE)["top"][0]["review_counts"]
    assert c["two_three_star"] is not None and c["no_comment"] == c["ratings"] - c["written_reviews"]


def test_one_return_changes_nothing_two_is_a_pattern():
    r1 = {"brand": "Anouk", "size": "M", "outcome": "returned", "reason": "colour not as expected"}
    first = rules.learn_from_outcome([], r1)
    assert first["memory_change"] is None and first["reopen_request"]
    second = rules.learn_from_outcome([r1], dict(r1, reason="size"))
    assert second["memory_change"]["key"] == "size_lean:Anouk"


def test_request_colour_and_fabric_are_hard_limits():
    prods = [dict(p, colour="White" if i % 2 else "Blue") for i, p in enumerate(PRODUCTS)]
    out = rules.rank(prods, BASE.override(colour="white", fabric_must="cotton"))
    assert all(p["colour"] == "White" and "cotton" in p["fabric"].lower() for p in out["top"])
    assert any("not white" in " ".join(d["reasons"]) for d in out["dropped"])


def test_size_summary_from_real_order_shapes():
    items = [{"size": s} for s in ["S", "M", "L", "M", "M", "50", "32", "32", "M", "M", "9", "32", "32", "M", "32", "6.5"]]
    s = rules.size_summary(items)
    assert s["size_top"]["value"] == "M" and s["size_bottom"]["value"] == "32"
    assert rules.size_summary([{"size": "M"}]) == {}          # one sample is not enough


def test_order_behaviour_from_emails():
    orders = [{"date": "Sun, 23 Aug 2026 18:38:10 +0000", "amount": 5016.0, "shop": "Myntra", "items": [{}] * 5},
              {"date": "Tue, 18 Aug 2026 19:23:26 +0000", "amount": 10344.0, "shop": "Myntra", "items": [{}] * 11},
              {"date": "Fri, 10 Oct 2025 21:33:35 +0530", "amount": None, "shop": "Flipkart", "items": []}]
    b = rules.order_behaviour(orders)
    assert b["orders_seen"] == 3 and b["last_order"] == "2026-08-23" and 900 < b["avg_item_price"] < 1000
    assert b["shops_used"].startswith("Myntra (2)")
