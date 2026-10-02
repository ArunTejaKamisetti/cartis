"""Behind the curtain (Wizard-of-Oz).

Two kinds of simulated calls, both labelled so the dashboard can show a "curtain" badge:
  kind="docs"     : a real API whose response is pasted from the provider's docs
                    (Delhivery pincode serviceability, Pine Labs createMandate, P3P acceptance).
  kind="imagined" : a capability that does not exist yet (Delhivery delivery promise,
                    Delhivery return alerts to buyers, Pine Labs P3P at Pine Labs merchants).

To change what Cartis sees, edit the JSON files in /curtain. Placeholders like {pincode}
are filled at call time.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date, timedelta
from typing import Any

from .. import config


def _load(name: str, **fill: Any) -> Any:
    raw = (config.CURTAIN / f"{name}.json").read_text()
    for k, v in fill.items():
        raw = raw.replace("{" + k + "}", str(v))
    return json.loads(raw)


def _wrap(kind: str, api: str, request: dict, response: Any) -> dict:
    return {"curtain": True, "kind": kind, "api": api, "request": request, "response": response}


# ---------------- docs-backed (real API, pasted response) ----------------
def delhivery_serviceability(pincode: str) -> dict:
    req = {"method": "GET", "url": "https://track.delhivery.com/c/api/pin-codes/json/",
           "params": {"filter_codes": pincode}}
    return _wrap("docs", "Delhivery pincode serviceability", req,
                 _load("delhivery_serviceability", pincode=pincode))


def pine_labs_p3p_acceptance(merchant: str) -> dict:
    cfg = _load("pine_labs_p3p_merchants")
    accepted = any(m.lower() in merchant.strip().lower() or merchant.strip().lower() in m.lower()
                   for m in cfg["accepting_merchants"])
    req = {"merchant": merchant}
    return _wrap("docs", "Pine Labs P3P merchant acceptance", req,
                 {"merchant": merchant, "accepts_p3p": accepted, "note": cfg.get("note")})


def pine_labs_create_mandate(amount_inr: float, merchant: str, order_ref: str | None = None) -> dict:
    ref = order_ref or f"CARTIS-{uuid.uuid4().hex[:8].upper()}"
    req = {"method": "POST", "url": "/mpp/v1/pre-authorize",
           "body": {"merchant_order_reference": ref, "amount": {"value": int(amount_inr * 100), "currency": "INR"},
                    "merchant": merchant}}
    resp = _load("pine_labs_create_mandate", ref=ref, amount_paise=int(amount_inr * 100),
                 ts=int(time.time()), merchant=merchant)
    return _wrap("docs", "Pine Labs createMandate", req, resp)


def pine_labs_payment(mandate: dict, method: str) -> dict:
    """Simulated: the payment captured against the mandate (sandbox-style response)."""
    import uuid as _u
    data = (mandate.get("response") or {}).get("data") or {}
    amount = data.get("amount") or {}
    body = {"data": {"order_id": f"v1-ord-{_u.uuid4().hex[:12]}", "payment_id": f"v1-pay-{_u.uuid4().hex[:12]}",
                     "status": "PROCESSED", "amount": amount, "payment_method": method,
                     "mandate_id": data.get("mandate_id"), "merchant_order_reference": data.get("merchant_order_reference"),
                     "captured_at": int(time.time())}}
    return _wrap("simulated", "Pine Labs payment capture", {"mandate_id": data.get("mandate_id"), "method": method}, body)


# ---------------- imagined capabilities ----------------
def delhivery_delivery_promise(pincode: str, product_id: str) -> dict:
    """Imagined: a per-parcel promise date. Deterministic per product (seller warehouse lane),
    and some products get no promise, so 'date unknown' cards appear like they would in life."""
    import hashlib

    cfg = _load("delhivery_promise")
    h = int(hashlib.md5(f"{product_id}:{pincode}".encode()).hexdigest(), 16)
    req = {"pincode": pincode, "product_id": product_id}
    if h % cfg.get("unknown_every", 13) == 0:
        return _wrap("imagined", "Delhivery delivery promise", req,
                     {"promise_date": None, "reason": "no lane history for this seller to this pincode"})
    d = cfg["days_by_pin_prefix"].get(pincode[:3], cfg["default_days"]) + h % cfg.get("lane_spread_days", 4)
    eta = date.today() + timedelta(days=d)
    return _wrap("imagined", "Delhivery delivery promise", req,
                 {"promise_date": eta.isoformat(), "days": d, "confidence": cfg["confidence"],
                  "basis": "lane history for this seller warehouse -> pincode"})


def serviceable(resp: dict) -> bool:
    try:
        pc = resp["response"]["delivery_codes"][0]["postal_code"]
        return str(pc.get("pre_paid", "N")).upper() == "Y"
    except (KeyError, IndexError, TypeError):
        return False


def delhivery_return_alert(product_id: str, size: str) -> dict:
    cfg = _load("delhivery_return_alerts")
    hit = cfg.get(product_id) or cfg["_default"]
    return _wrap("imagined", "Delhivery return alerts to buyers", {"product_id": product_id, "size": size},
                 {**hit, "size": size})


def pine_labs_p3p_at_merchant(merchant: str) -> dict:
    return _wrap("imagined", "Pine Labs P3P at merchants on Pine Labs checkout", {"merchant": merchant},
                 {"merchant": merchant, "p3p_enabled_via_pine_checkout": True,
                  "flow": "mandate pre-authorised by Cartis; Chatur taps Pay on the shop page and the mandate settles"})


# ---------------- simulated order email (recording ends here) ----------------
def myntra_order_email(purchase: dict | None) -> dict:
    """Stands in for Myntra's real 'Order Confirmation' email, in the same shape our Gmail
    reader returns (subject/from/snippet/order_id/amount), built from the tapped purchase."""
    import random
    from datetime import datetime

    if not purchase:
        return _wrap("simulated", "Myntra order confirmation email", {},
                     {"found": False, "reason": "no confirmed purchase in this session yet"})
    rnd = random.Random(purchase.get("product_id"))
    oid = f"{rnd.randint(1300000, 1399999)}-{rnd.randint(1000000, 9999999)}-{rnd.randint(1000000, 9999999)}"
    eta = purchase.get("eta")
    title = purchase.get("title") or "your item"
    shop = purchase.get("merchant") or "Myntra"
    body = {
        "found": True,
        "subject": f"Your {shop} Order Confirmation.",
        "from": "Myntra Updates <updates@myntra.com>" if shop.lower() == "myntra" else f"{shop} <order-update@{shop.lower().replace(' ', '')}>",
        "date": datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0530"),
        "snippet": f"Hello {purchase.get('name') or 'there'}! Sit Back And Relax. Your Order Is Confirmed. "
                   f"{title} | Size: {purchase.get('size')} | Qty 1 ₹{purchase['amount_inr']:,.2f}"
                   + (f" | Delivery by {eta}" if eta else ""),
        "order_id": oid,
        "amount": float(purchase["amount_inr"]),
        "shop": purchase.get("merchant", "Myntra"),
    }
    return _wrap("simulated", "Myntra order confirmation email", {"query": f"from:{shop} subject:confirmation"}, body)


# ---------------- Round 2 capability 11: guess_arrival_date (we build it) ----------------
def delhivery_estimate(pincode: str, product_id: str, seller_pin: str | None = None) -> dict:
    """No promise available -> estimate a RANGE from road travel time (Delhivery Maps
    compute_distance_matrix, docs) + a handling time per distance band. Never a single date."""
    import hashlib

    cfg = _load("delhivery_estimate")
    h = int(hashlib.md5(f"est:{product_id}:{pincode}".encode()).hexdigest(), 16)
    seller_pin = seller_pin or cfg["seller_pins"][h % len(cfg["seller_pins"])]
    km = cfg["km_by_pair"].get(f"{seller_pin[:3]}-{pincode[:3]}", 400 + h % 1400)
    hours = round(km / cfg["truck_kmph"], 1)
    band = next(b for b in cfg["handling_days_by_km"] if km <= b["max_km"])
    lo = band["days"] + int(hours // 24)
    hi = lo + cfg["spread_days"]
    start, end = date.today() + timedelta(days=lo), date.today() + timedelta(days=hi)
    matrix = {"origin": seller_pin, "destination": pincode, "distance_km": km, "duration_hours": hours,
              "profile": "truck"}
    return _wrap("docs", "Delhivery Maps compute_distance_matrix + Cartis estimate",
                 {"origins": [seller_pin], "destinations": [pincode], "profile": "truck"},
                 {"distance_matrix": matrix, "estimate_from": start.isoformat(), "estimate_to": end.isoformat(),
                  "label": "estimate, not a promise"})


# ---------------- Round 2 capabilities 6, 7, 8: address ----------------
def delhivery_address(raw: str, parts: dict) -> dict:
    """standardize_address -> validate_address -> verify_address (Delhivery Maps)."""
    pin = str(parts.get("pincode") or "").strip()
    std = {k: (parts.get(k) or "").strip() for k in ("house", "street", "locality", "landmark", "city", "state")}
    std["pincode"] = pin
    missing = [k for k in ("house", "locality", "city", "pincode") if not std.get(k)]
    valid_pin = len(pin) == 6 and pin.isdigit()
    if pin and not valid_pin:
        missing.append("pincode (6 digits)")
    status = "VALID" if not missing else "INCOMPLETE"
    cfg = _load("delhivery_address")
    verified = valid_pin and pin[:2] in cfg["delivered_recently_prefixes"]
    return _wrap("docs", "Delhivery Maps standardize + validate + verify address", {"address": raw},
                 {"standardized": std, "validation": {"status": status, "missing": missing},
                  "verification": {"delivered_in_last_months": 12, "has_delivered": bool(verified)}})


# ---------------- Round 2 capabilities 16, 17: Pine Labs checks around the payment ----------------
def grantex_decide(amount_inr: float, merchant: str) -> dict:
    """prove_it_is_cartis: Grantex agent identity + scope, enforced in Pine Labs decidePayment."""
    import uuid as _u
    cfg = _load("pine_labs_grantex")
    allowed = amount_inr <= cfg["per_transaction_cap_inr"]
    return _wrap("docs", "Pine Labs decidePayment (Grantex)",
                 {"agent": cfg["agent_did"], "scope": cfg["scope"], "amount_inr": amount_inr, "merchant": merchant},
                 {"decision": "ALLOW" if allowed else "DENY", "decision_id": f"gx-{_u.uuid4().hex[:10]}",
                  "agent": cfg["agent_did"], "scope": cfg["scope"], "per_transaction_cap_inr": cfg["per_transaction_cap_inr"],
                  "reason": None if allowed else "amount above the per-transaction cap the shopper set"})


def mandate_balance(mandate: dict) -> dict:
    """confirm_permission_status: Pine Labs getMandateBalance (GET /mpp/v1/balance)."""
    data = (mandate.get("response") or {}).get("data") or {}
    blocked = int(str((data.get("amount") or {}).get("value") or 0))
    return _wrap("docs", "Pine Labs getMandateBalance", {"method": "GET", "url": "/mpp/v1/balance",
                                                         "mandate_id": data.get("mandate_id")},
                 {"mandate_id": data.get("mandate_id"), "status": "ACTIVE", "blocked_paise": blocked,
                  "spent_paise": 0, "remaining_paise": blocked})
