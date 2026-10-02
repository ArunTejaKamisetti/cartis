"""Tool schemas Claude sees + the Python that runs them.

Each handler returns (result_for_claude, ui_event_or_None). UI events are what the dashboard
renders on screen; Claude's final text is what the voice says.

Round 2 mapping (capability names from our Q4 answer):
  read_order_emails  -> import_order_history      can_deliver_here / get_delivery_promise -> inside rank_products
  should_ask         -> check_question_value       start_purchase_by_voice -> request_confirmation (tap required)
  learn_from_outcome -> record_outcome             search_shops -> Myntra (Apify) + Google Shopping (Serper)
  clean_up/check_address -> check_address        prove_it_is_cartis + confirm_permission_status -> inside the payment
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from . import rules
from .store import Store
from .tools import curtain, gmail, myntra, serper

store = Store()
WATCH_DAYS = 14
# Recording ends on the order email. "curtain" = simulated Myntra email built from the tapped purchase;
# "gmail" = read the real inbox (order confirmation emails only).
from . import config as _cfg  # noqa: E402
# "send" (default when Google is connected): Cartis emails a real receipt to the shopper's Gmail, then reads it back.
# "curtain": a simulated shop email, nothing sent. "gmail": read the shop's own confirmation from the inbox.
ORDER_EMAIL = (_cfg.env("CARTIS_ORDER_EMAIL") or ("send" if _cfg.mode("google") == "live" else "curtain")).lower()
_LAST_PURCHASE: dict | None = None
PAYMENTS: list[dict] = []          # Pine Labs payments made in this server run (the Payments button)   # how long Cartis watches an order before asking "did you keep it?"

_PREF_PROPS = {
    "size": {"type": "string"},
    "budget_max": {"type": "number"},
    "avoid_fabrics": {"type": "string", "description": "comma-separated"},
    "min_ratings": {"type": "integer"},
    "need_by": {"type": "string", "description": "YYYY-MM-DD the item must arrive by"},
    "colour": {"type": "string", "description": "colour named in THIS request, e.g. white"},
    "fabric_must": {"type": "string", "description": "fabric named in THIS request, e.g. cotton"},
    "sort_by": {"type": "string", "enum": ["reviews", "arrival", "price", "ratings"],
                "description": "the shopper's priority, e.g. 'Friday matters more than price' -> arrival"},
    "weights": {"type": "object", "description": "one_star_share / two_three_star_share / no_comment_share"},
}

TOOLS: list[dict[str, Any]] = [
    {"name": "get_profile",
     "description": "Read the shopper's saved profile (name, sizes, sizes per brand, budget, avoided fabrics, pincode) and review rules from Google Sheets. Call once at the start.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "import_order_history",
     "description": "S0, learn without a form: read past ORDER CONFIRMATION emails (never the rest of the inbox) and return sizes with the item text they appeared next to. Then save sizes per brand with update_profile (key 'size_at:<Brand>'). Call when the profile has no sizes per brand yet, or when asked.",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "default": 365}}}},
    {"name": "update_profile",
     "description": "Save a LASTING preference the shopper states about themselves (budget_max, avoid_fabrics, size_top, size_at:<Brand>) or a review-rule weight (tab ReviewRules: one_star_share, two_three_star_share, no_comment_share, min_ratings). Never save one-off remarks about a single item.",
     "input_schema": {"type": "object", "required": ["key", "value"], "properties": {
         "key": {"type": "string"}, "value": {"type": "string"},
         "tab": {"type": "string", "enum": ["Memory", "ReviewRules"], "default": "Memory"}}}},
    {"name": "search_shops",
     "description": "Search ALL shops at once: Myntra in depth (Apify detail scrape: sizes, fabric, rating breakdown) plus every other Indian shop via Google Shopping (Amazon, Ajio, Flipkart, Tata CLiQ, brand sites; price, rating, link). Returns a search_id; call rank_products next.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "description": "shopper-style query, e.g. 'men brown casual shirt'"},
         "gender": {"type": "string", "enum": ["men", "women", "boys", "girls", "unisex"]}}}},
    {"name": "rank_products",
     "description": "Apply the shopper's rules to a search and put up to 4 cards on screen. Drops wrong size / over budget / avoided fabric / <min ratings, AND anything Delhivery can't deliver to the pincode or that can't arrive by need_by (checked before showing). Scores reviews (1-star share, 2-3 star share, no-comment share). Each card has an arrival date: eta_kind promise (Delhivery promise), estimate (eta..eta_to range, 'estimate, not a promise') or unknown, plus review counts. Cards from other shops are light: size and star breakdown are not available (score_basis 'rating only'); say so if relevant. overrides.sort_by re-sorts by the shopper's priority. Overrides are one-off, not saved.",
     "input_schema": {"type": "object", "required": ["search_id"], "properties": {
         "search_id": {"type": "string"}, "size_key": {"type": "string", "default": "size_top",
                                                       "description": "size_top, size_bottom, or size_at:<Brand>"},
         "overrides": {"type": "object", "properties": _PREF_PROPS}}}},
    {"name": "check_question_value",
     "description": "BEFORE asking any clarifying question, test the plausible answers. should_ask=true only if some answer would change the top 3 by more than half (overlap < 50%). If false, do not ask.",
     "input_schema": {"type": "object", "required": ["search_id", "question", "answers"], "properties": {
         "search_id": {"type": "string"}, "question": {"type": "string"},
         "need_by": {"type": "string"},
         "answers": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, **_PREF_PROPS}, "required": ["label"]}}}}},
    {"name": "recheck_stock",
     "description": "Live per-size stock and price recheck on Myntra for one product (second Apify actor). Always call right before request_confirmation.",
     "input_schema": {"type": "object", "required": ["product_id", "size"], "properties": {
         "product_id": {"type": "string"}, "size": {"type": "string"}}}},
    {"name": "cross_shop",
     "description": "Find the same product on OTHER shops (Google Shopping via Serper), excluding the shop it came from. Query with brand + product name. Only call a listing the same product if brand and design match.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "exclude_shop": {"type": "string", "default": "Myntra"}}}},
    {"name": "check_delivery",
     "description": "Delhivery for one product: pincode serviceability (docs response) and the delivery promise date (imagined capability).",
     "input_schema": {"type": "object", "required": ["product_id"], "properties": {
         "product_id": {"type": "string"}, "pincode": {"type": "string"}}}},
    {"name": "check_return_alerts",
     "description": "Delhivery return alerts to buyers (imagined capability): how often this product/size comes back and why.",
     "input_schema": {"type": "object", "required": ["product_id", "size"], "properties": {
         "product_id": {"type": "string"}, "size": {"type": "string"}}}},
    {"name": "check_payment_options",
     "description": "Pine Labs: does this shop accept P3P (docs), and is P3P available via Pine Labs checkout (imagined).",
     "input_schema": {"type": "object", "required": ["merchant"], "properties": {"merchant": {"type": "string"}}}},
    {"name": "check_address",
     "description": "Delhivery Maps: standardize_address, validate_address and verify_address for the delivery address the shopper SAID or typed. Pass the raw text and your parse into parts. If VALID it is saved as their address (and pincode). If INCOMPLETE, ask only for the missing part. Needed once before the first payment.",
     "input_schema": {"type": "object", "required": ["raw"], "properties": {
         "raw": {"type": "string"}, "house": {"type": "string"}, "street": {"type": "string"},
         "locality": {"type": "string"}, "landmark": {"type": "string"}, "city": {"type": "string"},
         "state": {"type": "string"}, "pincode": {"type": "string"}}}},
    {"name": "request_confirmation",
     "description": "Put a 'Pay ₹X with Pine Labs' button on screen for this product, size, shop and amount. Money never moves on a spoken yes: the shopper taps it, approves in the Pine Labs checkout, and you then receive a [screen tap] message with the mandate and payment result. Call after recheck_stock.",
     "input_schema": {"type": "object", "required": ["merchant", "amount_inr", "product_id", "size"], "properties": {
         "merchant": {"type": "string"}, "amount_inr": {"type": "number"}, "product_id": {"type": "string"},
         "size": {"type": "string"}}}},
    {"name": "save_to_list",
     "description": "Save a product to the shopper's Wishlist or Cart tab.",
     "input_schema": {"type": "object", "required": ["list", "product_id", "size"], "properties": {
         "list": {"type": "string", "enum": ["Wishlist", "Cart"]}, "product_id": {"type": "string"},
         "size": {"type": "string"}, "why": {"type": "string"}}}},
    {"name": "check_order_email",
     "description": "Find the shop's order confirmation email (order confirmation emails only) and log it to Orders with a check-back date. Call when the shopper says they have paid / placed the order.",
     "input_schema": {"type": "object", "properties": {"shop": {"type": "string"},
                                                       "product_id": {"type": "string"}, "size": {"type": "string"}}}},
    {"name": "record_outcome",
     "description": "S10: the shopper says they kept or returned an order. One return changes nothing about them (the request is reopened, the reason is only a hint); only a repeated pattern (same brand + size returned twice) updates memory.",
     "input_schema": {"type": "object", "required": ["order_id", "outcome"], "properties": {
         "order_id": {"type": "string"}, "outcome": {"type": "string", "enum": ["kept", "returned"]},
         "reason": {"type": "string"}}}},
]

# products seen this session, for lookups by id
_SEEN: dict[str, dict] = {}


def _prefs(size_key: str = "size_top", overrides: dict | None = None) -> rules.Prefs:
    p = rules.Prefs.from_memory(store.memory(), store.review_rules(), size_key)
    return p.override(**(overrides or {}))


def _card(p: dict) -> dict:
    keys = ("id", "title", "brand", "price", "mrp", "rating", "rating_count", "reviews_count",
            "fabric", "fit", "colour", "url", "image", "bought_brand", "wishlisted",
            "eta_kind", "eta_to", "may_be_late", "score_basis", "detail", "review_score", "shares", "review_counts", "eta", "shop")
    return {k: p.get(k) for k in keys}


def _delivery_map(products: list[dict], prefs: rules.Prefs) -> dict[str, dict]:
    """can_deliver_here (docs) once per pincode + get_delivery_promise (imagined) per product,
    only for products that pass the cheap filters."""
    pin = store.memory().get("pincode")
    ok = curtain.serviceable(curtain.delhivery_serviceability(pin)) if pin else True
    cheap = rules.rank(products, prefs.override(need_by=None), top_n=len(products))["top"]
    import hashlib as _h

    out = {}
    for p in cheap:
        if not (pin and ok):
            out[p["id"]] = {"serviceable": ok, "eta": None, "eta_kind": "unknown"}
            continue
        light = p.get("detail") is False
        promise = None
        if not (light and int(_h.md5(p["id"].encode()).hexdigest(), 16) % 2):   # fewer lane promises for other shops
            promise = curtain.delhivery_delivery_promise(pin, p["id"])["response"].get("promise_date")
        if promise:
            out[p["id"]] = {"serviceable": ok, "eta": promise, "eta_kind": "promise"}
        else:   # Round 2 cap 11: estimate range from road travel time, labelled "estimate, not a promise"
            est = curtain.delhivery_estimate(pin, p["id"])["response"]
            out[p["id"]] = {"serviceable": ok, "eta": est["estimate_from"], "eta_to": est["estimate_to"],
                            "eta_kind": "estimate"}
    return out


def wishlist_rows() -> list[dict]:
    return [r for r in store.rows("Wishlist") if r.get("why") != "removed"]


def h_get_profile(_: dict):
    mem = store.memory()
    return {"memory": mem, "review_rules": store.review_rules(), "today": date.today().isoformat(),
            "sizes_by_brand": {k.split(":", 1)[1]: v for k, v in mem.items() if k.startswith("size_at:")},
            "wishlist": [{k: r.get(k) for k in ("title", "price", "size", "product_id", "added_at")} for r in wishlist_rows()][-15:],
            "cart": [{k: r.get(k) for k in ("title", "price", "size", "shop")} for r in store.rows("Cart")][-5:],
            "recent_orders": [{k: r.get(k) for k in ("order_id", "items", "amount", "brand", "size", "outcome")}
                              for r in store.rows("Orders")][-8:]}, None


def h_import(a: dict):
    orders = gmail.past_orders(a.get("days", 365))
    items = [i for o in orders for i in o.get("items", [])]
    learned = rules.size_summary(items)
    for key, v in learned.items():   # deterministic: most common size, only with >=3 samples
        store.upsert_kv("Memory", key, v["value"], f"learned from order emails: {v['seen']} of {v['of']} items")
    behaviour = rules.order_behaviour(orders)
    for key in ("orders_seen", "avg_item_price", "typical_order_value", "last_order", "shops_used"):
        if behaviour.get(key) not in (None, ""):
            store.upsert_kv("Memory", key, str(behaviour[key]), "learned from order emails")
    res = {"orders_read": len(orders), "items_with_size": len(items), "learned": learned, "behaviour": behaviour,
           "orders": [{"date": o.get("date"), "items": [{"size": i["size"], "item_text": i["item_text"][-90:]}
                                                        for i in o.get("items", [])]} for o in orders],
           "next": ("Sizes and spend pattern saved. Now read the apparel brands and categories out of item_text and save them "
                    "with update_profile: brands_bought (comma list, most frequent first) and categories_bought "
                    "(e.g. casual shirts, t-shirts, shorts, linen trousers). Add size_at:<Brand> only where clear.")}
    return res, {"type": "history_imported", "data": {"orders_read": len(orders), "items": len(items),
                                                      "learned": learned, "behaviour": behaviour}}


def h_update_profile(a: dict):
    tab = a.get("tab", "Memory")
    store.upsert_kv(tab, a["key"], str(a["value"]))
    return {"saved": True, "tab": tab, a["key"]: a["value"]}, {"type": "profile_updated", "data": a}


def h_search(a: dict):
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as ex:
        f_m = ex.submit(myntra.search, a["query"], a.get("gender"))
        f_g = ex.submit(serper.shop_search, a["query"])
        errors = {}
        try:
            res = f_m.result()
            products = myntra.load_search(res["search_id"])
        except Exception as e:             # one shop failing never blanks the whole search
            errors["myntra"] = str(e)[:160]
            res, products = {"search_id": "s" + str(abs(hash(a["query"])))[:6], "query": a["query"], "source": "error"}, []
        try:
            g = f_g.result()
            others, g_src = g["products"], g["source"]
        except Exception as e:
            errors["google_shopping"] = str(e)[:160]
            others, g_src = [], "error"
    allp = products + others
    for p in allp:
        _SEEN[p["id"]] = p
    myntra.CACHE.mkdir(parents=True, exist_ok=True)
    (myntra.CACHE / f"{res['search_id']}.json").write_text(_json.dumps({"query": a["query"], "products": allp}, ensure_ascii=False))
    shops = sorted({p.get("shop") or "Myntra" for p in allp})
    out = {"search_id": res["search_id"], "query": a["query"], "count": len(allp), "myntra": len(products),
           "other_shops": len(others), "shops": shops, "source": res.get("source"), "google_source": g_src,
           **({"errors": errors} if errors else {})}
    return out, {"type": "searching", "data": out}


def h_rank(a: dict):
    products = myntra.load_search(a["search_id"])
    prefs = _prefs(a.get("size_key", "size_top"), a.get("overrides"))
    delivery = _delivery_map(products, prefs)
    out = rules.rank(products, prefs, delivery=delivery)        # every product that passed, best first
    mem = store.memory()
    bought = {b.strip().lower() for b in (mem.get("brands_bought") or "").split(",") if b.strip()}
    wished = {str(r.get("product_id")) for r in wishlist_rows()}
    for p in out["top"]:
        p["bought_brand"] = bool(bought and str(p.get("brand", "")).lower() in bought)
        p["wishlisted"] = str(p["id"]) in wished
        _SEEN[p["id"]] = {**_SEEN.get(p["id"], {}), **p}
    top = [_card(p) for p in out["top"]]
    compact = [{"card": f"c{i + 1}", "id": p["id"], "shop": p.get("shop"), "brand": p.get("brand"), "title": p.get("title"),
                "price": p.get("price"), "eta": p.get("eta"), "eta_kind": p.get("eta_kind"), "eta_to": p.get("eta_to"),
                "score": p.get("review_score"), "score_basis": p.get("score_basis"), "bought_brand": p["bought_brand"],
                "wishlisted": p["wishlisted"]} for i, p in enumerate(out["top"])]
    result = {**out, "top": top[:4], "all_on_screen": compact, "shown_on_screen": len(top),
              "dropped_by_reason": _group(out["dropped"]),
              "note": "EVERY passing product is on screen as cards c1..cN (scrollable). Talk about the top 3; never claim a display limit."}
    result.pop("dropped"); result.pop("runners_up", None)
    return result, {"type": "products", "data": {"search_id": a["search_id"], "top": top,
                                                 "counts": out["counts"], "dropped": out["dropped"],
                                                 "dropped_by_reason": result["dropped_by_reason"],
                                                 "nothing_in_time": out["nothing_in_time"],
                                                 "if_you_can_wait": out["if_you_can_wait"],
                                                 "prefs_used": out["prefs_used"]}}


def _group(dropped: list[dict]) -> dict[str, int]:
    g: dict[str, int] = {}
    for d in dropped:              # one count per product, by its first (main) reason, so totals add up
        for r in d["reasons"][:1]:
            if r.startswith("₹"):
                key = "over budget"
            elif r.startswith("arrives"):
                key = "can't arrive in time"
            else:
                key = {"size": "wrong size / out of stock", "fabric": "avoided fabric", "only": "too few ratings",
                       "can't": "not deliverable to pincode", "colour": "wrong colour",
                       "not": "wrong fabric"}.get(r.split(" ")[0], r)
            g[key] = g.get(key, 0) + 1
    return g


def h_question(a: dict):
    products = myntra.load_search(a["search_id"])
    prefs = _prefs(overrides={"need_by": a.get("need_by")})
    res = rules.question_value(products, prefs, [dict(x) for x in a["answers"]], _delivery_map(products, prefs))
    res["question"] = a["question"]
    return res, {"type": "question_check", "data": res}


def h_stock(a: dict):
    p = _SEEN.get(a["product_id"], {})
    if str(a["product_id"]).startswith("g") or p.get("detail") is False:
        res = {"product_id": a["product_id"], "size": a["size"], "source": "n/a", "available": None,
               "note": f"{p.get('shop', 'This shop')} doesn't share size stock; the shopper checks size on the shop page."}
        return res, {"type": "stock", "data": res}
    try:
        res = myntra.recheck_stock(a["product_id"], a["size"], store.memory().get("pincode"))
    except Exception as e:   # e.g. Apify credit used up (403): fall back to the sizes seen at search time
        sizes = p.get("sizes") or []
        hit = next((x for x in sizes if str(x.get("label", "")).upper() == str(a["size"]).upper()), None)
        res = {"product_id": a["product_id"], "size": a["size"], "source": "search snapshot",
               "available": bool(hit and hit.get("available", True)) if sizes else None,
               "all_sizes": [{"label": x.get("label"), "available": x.get("available", True)} for x in sizes],
               "live_recheck": f"unavailable ({type(e).__name__}: {str(e)[:80]})",
               "note": "Live recheck unavailable; this is the stock from the search a few minutes ago. "
                       "Do NOT block the purchase: mention it in a few words and continue to request_confirmation."}
    return res, {"type": "stock", "data": res}


def h_cross(a: dict):
    res = serper.cross_shop(a["query"], a.get("exclude_shop", "Myntra"))
    return res, {"type": "compare_shops", "data": res}


def h_delivery(a: dict):
    pin = a.get("pincode") or store.memory().get("pincode")
    res = {"serviceability": curtain.delhivery_serviceability(pin),
           "promise": curtain.delhivery_delivery_promise(pin, a["product_id"])}
    return res, {"type": "delivery", "data": res}


def h_returns(a: dict):
    res = curtain.delhivery_return_alert(a["product_id"], a["size"])
    return res, {"type": "return_alert", "data": res}


def h_payment(a: dict):
    res = {"acceptance": curtain.pine_labs_p3p_acceptance(a["merchant"]),
           "via_pine_checkout": curtain.pine_labs_p3p_at_merchant(a["merchant"])}
    return res, {"type": "payment_options", "data": res}


def h_address(a: dict):
    parts = {k: a.get(k) for k in ("house", "street", "locality", "landmark", "city", "state", "pincode")}
    res = curtain.delhivery_address(a["raw"], parts)
    r = res["response"]
    if r["validation"]["status"] == "VALID":
        sd = r["standardized"]
        line = ", ".join(x for x in (sd["house"], sd["street"], sd["locality"], sd["landmark"], sd["city"], sd["pincode"]) if x)
        store.upsert_kv("Memory", "address", line, "validated by Delhivery")
        store.upsert_kv("Memory", "pincode", sd["pincode"], "from validated address")
    return res, {"type": "address_checked", "data": r}


def h_request_confirmation(a: dict):
    if not store.memory().get("address"):
        return {"error": "no delivery address yet: ask the shopper to say their address, then call check_address"}, None
    p = _SEEN.get(a["product_id"], {"id": a["product_id"]})
    pending = {"merchant": a["merchant"], "amount_inr": a["amount_inr"], "product_id": a["product_id"],
               "size": a["size"], "title": p.get("title"), "url": p.get("url"), "image": p.get("image")}
    res = {"status": "awaiting_tap", "button": f"Pay ₹{a['amount_inr']:,.0f} with Pine Labs",
           "note": "Nothing is authorised until the shopper taps. Wait for the [screen tap] message."}
    return {**res, "pending": pending}, {"type": "confirm_required", "data": pending | {"button": res["button"]}}


def tap_confirm(pending: dict, method: str = "UPI") -> tuple[dict, dict]:
    """Called by the UI (Pine Labs checkout, after the shopper approves), never by Claude:
    creates the mandate (docs response) and captures the payment (simulated)."""
    global _LAST_PURCHASE
    gx = curtain.grantex_decide(pending["amount_inr"], pending["merchant"])
    if gx["response"]["decision"] != "ALLOW":
        return {"grantex": gx, "payment": {"response": {"data": {"status": "DENIED"}}}}, \
            {"type": "checkout", "data": {"grantex": gx, "denied": True, "product": pending, "method": method}}
    res = curtain.pine_labs_create_mandate(pending["amount_inr"], pending["merchant"])
    res["grantex"] = gx
    res["balance"] = curtain.mandate_balance(res)
    res["payment"] = curtain.pine_labs_payment(res, method)
    seen = _SEEN.get(pending["product_id"], {})
    pin = store.memory().get("pincode")      # the address may have changed since the cards were ranked
    fresh = curtain.delhivery_delivery_promise(pin, pending["product_id"])["response"].get("promise_date") if pin else None
    pay = (res["payment"].get("response") or {}).get("data") or {}
    PAYMENTS.append({"at": store.now(), "title": pending.get("title"), "shop": pending["merchant"], "amount": pending["amount_inr"],
                     "method": method, "payment_id": pay.get("payment_id"), "mandate_id": pay.get("mandate_id"),
                     "status": pay.get("status"), "agent_check": gx["response"]["decision"],
                     "mandate_status": res["balance"]["response"].get("status")})
    _LAST_PURCHASE = {**pending, "eta": fresh or seen.get("eta"), "brand": seen.get("brand"), "name": store.memory().get("name"),
                      "method": method, "payment_id": pay.get("payment_id"), "mandate_id": pay.get("mandate_id")}
    store.append("Cart", {"added_at": store.now(), "product_id": pending["product_id"], "title": pending.get("title"),
                          "price": pending["amount_inr"], "size": pending["size"], "url": pending.get("url"),
                          "shop": pending["merchant"]})
    return res, {"type": "checkout", "data": {"mandate": res, "payment": res["payment"], "grantex": gx,
                                              "balance": res["balance"], "product": pending,
                                              "pay_url": pending.get("url"), "method": method}}


def h_save(a: dict):
    p = _SEEN.get(a["product_id"], {"id": a["product_id"]})
    tab = a["list"]
    row = {"added_at": store.now(), "product_id": p["id"], "title": p.get("title"), "price": p.get("price"),
           "size": a["size"], "url": p.get("url"), "why": a.get("why", ""), "shop": p.get("shop", "Myntra")}
    store.append(tab, row)
    return {"saved": True, "list": tab, "product_id": p["id"]}, {"type": "saved", "data": row | {"list": tab}}


def h_order(a: dict):
    if ORDER_EMAIL in ("curtain", "send"):
        wrapped = curtain.myntra_order_email(_LAST_PURCHASE)
        res = {**wrapped["response"], "curtain": True, "kind": "simulated", "api": wrapped["api"]}
        from .google_auth import can_send
        if ORDER_EMAIL == "send" and res.get("found") and not can_send():
            res["send_error"] = ("Google hasn't allowed Cartis to send email yet. Run python -m cartis.cli auth "
                                 "and tick 'Send email on your behalf'")
        elif ORDER_EMAIL == "send" and res.get("found"):
            try:   # real email: send the receipt to the shopper, then read it back from Gmail
                lp, mem = _LAST_PURCHASE or {}, store.memory()
                check_back = (date.today() + timedelta(days=WATCH_DAYS)).isoformat()
                sent = gmail.send_receipt({**lp, "order_id": res["order_id"], "amount": res["amount"], "shop": res["shop"],
                                           "eta_text": lp.get("eta") or "", "address": mem.get("address", ""),
                                           "name": mem.get("name"), "check_back_on": check_back})
                back = gmail.read_receipt(res["order_id"])
                if back.get("found"):
                    res = {**res, **{k: v for k, v in back.items() if v}, "order_id": res["order_id"],
                           "amount": res["amount"], "shop": res["shop"], "sent_to": sent["to"], "real_email": True}
                    res.pop("curtain", None); res.pop("kind", None)
            except Exception as e:   # missing gmail.send permission etc.: keep the simulated email, say why
                res["send_error"] = f"{type(e).__name__}: {str(e)[:160]}"
    else:
        res = gmail.latest_order_confirmation(a.get("shop"))
    if res.get("found"):
        p = _SEEN.get(a.get("product_id") or (_LAST_PURCHASE or {}).get("product_id", ""), {})
        res["check_back_on"] = (date.today() + timedelta(days=WATCH_DAYS)).isoformat()
        if not any(r.get("order_id") == res.get("order_id") for r in store.rows("Orders")):
            store.append("Orders", {"logged_at": store.now(), "order_id": res.get("order_id"), "shop": res.get("shop"),
                                    "items": p.get("title") or res.get("subject"), "amount": res.get("amount"),
                                    "status": "confirmed", "email_id": res.get("email_id"),
                                    "brand": p.get("brand", ""), "size": a.get("size") or (_LAST_PURCHASE or {}).get("size", ""),
                                    "outcome": "watching", "watch_until": res["check_back_on"]})
    return res, {"type": "order_confirmed" if res.get("found") else "order_pending", "data": res}


def h_outcome(a: dict):
    rows = store.rows("Orders")
    row = next((r for r in rows if r.get("order_id") == a["order_id"]), None)
    if not row:
        return {"error": f"order {a['order_id']} not in Orders"}, None
    history = [{"brand": r.get("brand"), "size": r.get("size"), "outcome": r.get("outcome")}
               for r in rows if r.get("order_id") != a["order_id"]]
    new = {"brand": row.get("brand"), "size": row.get("size"), "outcome": a["outcome"], "reason": a.get("reason")}
    verdict = rules.learn_from_outcome(history, new)
    store.update_row("Orders", "order_id", a["order_id"], {"outcome": a["outcome"]})
    if verdict.get("memory_change"):
        mc = verdict["memory_change"]
        store.upsert_kv("Memory", mc["key"], mc["value"], "learned from repeated returns")
    return verdict, {"type": "outcome", "data": {"order_id": a["order_id"], **new, **verdict}}


def toggle_wishlist(product_id: str, on: bool) -> dict:
    """The heart button on a card (UI action, not a Claude tool)."""
    p = _SEEN.get(str(product_id), {"id": product_id})
    rows = store.rows("Wishlist")
    if on:
        if not any(str(r.get("product_id")) == str(product_id) and r.get("why") != "removed" for r in rows):
            store.append("Wishlist", {"added_at": store.now(), "product_id": p["id"], "title": p.get("title"),
                                      "price": p.get("price"), "size": "", "url": p.get("url"), "why": "hearted on screen"})
    else:
        store.update_row("Wishlist", "product_id", str(product_id), {"why": "removed"})
    return {"product_id": product_id, "wishlisted": on, "title": p.get("title"), "count": len(wishlist_rows())}


HANDLERS: dict[str, Callable[[dict], tuple[Any, dict | None]]] = {
    "get_profile": h_get_profile, "import_order_history": h_import, "update_profile": h_update_profile,
    "search_shops": h_search, "check_address": h_address, "rank_products": h_rank, "check_question_value": h_question,
    "recheck_stock": h_stock, "cross_shop": h_cross, "check_delivery": h_delivery,
    "check_return_alerts": h_returns, "check_payment_options": h_payment,
    "request_confirmation": h_request_confirmation, "save_to_list": h_save,
    "check_order_email": h_order, "record_outcome": h_outcome,
}
