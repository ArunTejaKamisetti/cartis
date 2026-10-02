"""Deterministic shopping rules. Claude decides WHAT to do; this module decides the numbers.

Filters  : size in stock, price <= budget, fabric not avoided, rating_count >= min_ratings.
Score    : 100 x (1 - weighted risk), risk from 1-star share, 2-3 star share, no-comment share.
Delivery : (Round 2, S5) drop anything that can't arrive by the date needed BEFORE showing it.
           Unknown dates are kept but sit below every card with a real date.
Ask rule : ask a clarifying question only if some plausible answer changes the top 3 so much
           that overlap with the current top 3 falls below 50% (i.e. fewer than 2 of 3 survive).
Outcome  : (Round 2, S10) one return changes nothing; only a repeated pattern moves memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_WEIGHTS = {"one_star_share": 0.5, "two_three_star_share": 0.3, "no_comment_share": 0.2}
DEFAULT_MIN_RATINGS = 30
ASK_OVERLAP_THRESHOLD = 0.5


@dataclass
class Prefs:
    size: str | None = None
    budget_max: float | None = None
    avoid_fabrics: list[str] = field(default_factory=list)
    min_ratings: int = DEFAULT_MIN_RATINGS
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    need_by: str | None = None          # ISO date; None = no deadline
    colour: str | None = None           # one-off, from the request ("white kurta")
    fabric_must: str | None = None      # one-off, from the request ("cotton kurta")
    sort_by: str = "reviews"            # reviews | arrival | price | ratings  (Round 2 S8: "Friday matters more")

    @classmethod
    def from_memory(cls, memory: dict[str, str], rules: dict[str, float], size_key: str = "size_top") -> "Prefs":
        w = {k: rules.get(k, v) for k, v in DEFAULT_WEIGHTS.items()}
        return cls(
            size=memory.get(size_key) or None,
            budget_max=_num(memory.get("budget_max")),
            avoid_fabrics=[f.strip().lower() for f in (memory.get("avoid_fabrics") or "").split(",") if f.strip()],
            min_ratings=int(rules.get("min_ratings", DEFAULT_MIN_RATINGS)),
            weights=w,
        )

    def override(self, **kw: Any) -> "Prefs":
        d = {**self.__dict__, "weights": dict(self.weights), "avoid_fabrics": list(self.avoid_fabrics)}
        for k, v in kw.items():
            if v is None:
                continue
            if k == "weights":
                d["weights"].update(v)
            elif k == "avoid_fabrics" and isinstance(v, str):
                d[k] = [f.strip().lower() for f in v.split(",") if f.strip()]
            else:
                d[k] = v
        return Prefs(**d)


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def size_available(p: dict, size: str | None) -> bool:
    if not size or p.get("detail") is False:   # light listing (other shops): size checked on the shop page
        return True
    want = size.strip().upper()
    for s in p.get("sizes") or []:
        label = str(s.get("label", "")).strip().upper()
        if label == want and s.get("available", True):
            return True
    return False


def review_breakdown(p: dict) -> dict[str, float | None]:
    """Shares used by the score. Missing breakdown -> None components (flagged, not guessed)."""
    b = {int(k): v for k, v in (p.get("rating_breakdown") or {}).items()}  # JSON cache turns keys into str
    total = sum(b.values()) if b else 0
    rc = p.get("rating_count") or total or 0
    rv = p.get("reviews_count")
    return {
        "one_star_share": (b.get(1, 0) / total) if total else None,
        "two_three_star_share": ((b.get(2, 0) + b.get(3, 0)) / total) if total else None,
        "no_comment_share": (max(0.0, 1 - rv / rc) if rc and rv is not None else None),
    }


def review_score(p: dict, weights: dict[str, float]) -> tuple[float, dict[str, float | None]]:
    shares = review_breakdown(p)
    used = {k: w for k, w in weights.items() if shares.get(k) is not None and w > 0}
    if not used:
        r = p.get("rating")
        if r:   # shop shares only the average: a lower-confidence score from it, marked "rating only"
            return round(100 * (float(r) - 1) / 4 * 0.9, 1), shares
        return 0.0, shares
    risk = sum(shares[k] * w for k, w in used.items()) / sum(used.values())
    return round(100 * (1 - risk), 1), shares


SHOW_N = 4      # Round 2 Q6: start with four cards; the top three are read aloud
ASK_TOP = 3


def rank(products: list[dict], prefs: Prefs, top_n: int | None = None,
         delivery: dict[str, dict] | None = None) -> dict[str, Any]:
    """delivery: product_id -> {"serviceable": bool, "eta": "YYYY-MM-DD" | None}"""
    delivery = delivery or {}
    kept, dropped, late = [], [], []
    for p in products:
        reasons = []
        d = delivery.get(p["id"], {})
        if d.get("serviceable") is False:
            reasons.append("can't deliver to this pincode")
        if not size_available(p, prefs.size):
            reasons.append(f"size {prefs.size} not in stock")
        if prefs.budget_max is not None and (p.get("price") or 0) > prefs.budget_max:
            reasons.append(f"₹{p.get('price')} over budget ₹{int(prefs.budget_max)}")
        fab = (p.get("fabric") or "").lower()
        hit = [f for f in prefs.avoid_fabrics if f and f in fab]
        if hit:
            reasons.append(f"fabric contains {', '.join(hit)}")
        if prefs.colour and p.get("colour") and prefs.colour.lower() not in str(p["colour"]).lower():
            reasons.append(f"colour {p['colour']}, not {prefs.colour}")
        if prefs.fabric_must and prefs.fabric_must.lower() not in fab and (fab or p.get("detail") is not False):
            reasons.append(f"not {prefs.fabric_must} ({p.get('fabric') or 'fabric unknown'})")
        if (p.get("rating_count") or 0) < prefs.min_ratings:
            reasons.append(f"only {p.get('rating_count') or 0} ratings (<{prefs.min_ratings})")
        eta, kind = d.get("eta"), d.get("eta_kind") or ("promise" if d.get("eta") else "unknown")
        # a promise later than need_by is late; an estimate is late only if even its earliest day is
        is_late = bool(prefs.need_by and eta and eta > prefs.need_by)
        if reasons or is_late:
            if is_late:
                reasons.append(f"arrives {eta}, after {prefs.need_by}")
            dropped.append({"id": p["id"], "title": p.get("title"), "reasons": reasons})
            if is_late and len(reasons) == 1:          # fails ONLY on time -> "if you can wait" list
                late.append({**p, "eta": eta})
            continue
        score, shares = review_score(p, prefs.weights)
        kept.append({**p, "review_score": score, "shares": shares, "eta": eta, "eta_kind": kind,
                     "eta_to": d.get("eta_to"),
                     "may_be_late": bool(prefs.need_by and d.get("eta_to") and d["eta_to"] > prefs.need_by),
                     "score_basis": "breakdown" if any(v is not None for v in shares.values()) else "rating only",
                     "review_counts": review_counts(p)})

    # Round 2: promised dates first, then estimates, then "date unknown"; within that, the shopper's priority
    eta_rank = {"promise": 2, "estimate": 1, "unknown": 0}
    keys = {
        "reviews": lambda x: (eta_rank[x["eta_kind"]] if delivery else 0, x["review_score"], x.get("rating_count") or 0),
        "arrival": lambda x: (eta_rank[x["eta_kind"]] > 0, -_day(x["eta"]), x["review_score"]),
        "price": lambda x: (-(x.get("price") or 1e9), x["review_score"]),
        "ratings": lambda x: (x.get("rating_count") or 0, x["review_score"]),
    }
    kept.sort(key=keys.get(prefs.sort_by, keys["reviews"]), reverse=True)
    late.sort(key=lambda x: x["eta"])
    return {
        "top": kept[:top_n],
        "runners_up": kept[top_n:top_n + 3] if top_n else [],
        "dropped": dropped,
        "nothing_in_time": bool(prefs.need_by and not kept and late),
        "if_you_can_wait": [{"id": x["id"], "title": x.get("title"), "eta": x["eta"]} for x in late[:3]],
        "counts": {"in": len(products), "passed": len(kept), "dropped": len(dropped), "late": len(late)},
        "prefs_used": {
            "size": prefs.size, "budget_max": prefs.budget_max, "avoid_fabrics": prefs.avoid_fabrics,
            "min_ratings": prefs.min_ratings, "weights": prefs.weights, "need_by": prefs.need_by,
            "colour": prefs.colour, "fabric_must": prefs.fabric_must, "sort_by": prefs.sort_by,
        },
        "shops": sorted({str(p.get("shop") or "Myntra") for p in kept}),
    }


def _day(iso: str | None) -> int:
    return int(iso.replace("-", "")) if iso else 99999999


def review_counts(p: dict) -> dict[str, int | None]:
    """The numbers Round 2 promised on every card, instead of an AI summary."""
    b = {int(k): v for k, v in (p.get("rating_breakdown") or {}).items()}
    rc, rv = p.get("rating_count") or 0, p.get("reviews_count")
    return {"one_star": b.get(1), "two_three_star": (b.get(2, 0) + b.get(3, 0)) if b else None,
            "five_star": b.get(5), "ratings": rc, "written_reviews": rv,
            "no_comment": (rc - rv) if (rc and rv is not None) else None}


def overlap(a: list[dict], b: list[dict]) -> float:
    ia, ib = {p["id"] for p in a}, {p["id"] for p in b}
    if not ia:
        return 1.0 if not ib else 0.0
    return len(ia & ib) / max(len(ia), 1)


def question_value(products: list[dict], prefs: Prefs, alternatives: list[dict[str, Any]],
                   delivery: dict[str, dict] | None = None) -> dict[str, Any]:
    """For each plausible answer (a Prefs override), how much would the top 3 change?"""
    base = rank(products, prefs, ASK_TOP, delivery)["top"]
    out = []
    for alt in alternatives:
        label = alt.pop("label", str(alt))
        top = rank(products, prefs.override(**alt), ASK_TOP, delivery)["top"]
        ov = overlap(base, top)
        out.append({"answer": label, "overlap": round(ov, 2), "top_ids": [p["id"] for p in top]})
    worst = min((o["overlap"] for o in out), default=1.0)
    return {
        "should_ask": worst < ASK_OVERLAP_THRESHOLD,
        "worst_overlap": worst,
        "threshold": ASK_OVERLAP_THRESHOLD,
        "base_top_ids": [p["id"] for p in base],
        "by_answer": out,
    }


# ---------------------------------------------------------------- outcome learning (S10)
PATTERN_MIN = 2   # the same thing twice is a signal; once is a coincidence


def learn_from_outcome(history: list[dict], new: dict) -> dict[str, Any]:
    """history/new: {"brand", "size", "outcome": "kept"|"returned", "seller"?, "reason"?}.
    Returns what (if anything) memory should change. Return reasons are hints, never facts."""
    if new.get("outcome") != "returned":
        return {"memory_change": None, "why": "kept: confirms the size/brand already on record"}
    same = [h for h in history + [new] if h.get("outcome") == "returned"
            and h.get("brand", "").lower() == new.get("brand", "").lower()
            and str(h.get("size", "")).upper() == str(new.get("size", "")).upper()]
    if len(same) >= PATTERN_MIN:
        return {"memory_change": {"key": f"size_lean:{new['brand']}", "value": f"avoid {new['size']}, try next size"},
                "why": f"{len(same)} returns of size {new['size']} at {new['brand']}: a pattern"}
    return {"memory_change": None, "reopen_request": True,
            "why": "one return changes nothing about you; the reason is only a hint for the retry",
            "retry_hint": new.get("reason")}


# ---------------------------------------------------------------- sizes from order emails (S0)
LETTER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "XXXL"]


def size_summary(items: list[dict], min_samples: int = 3) -> dict[str, Any]:
    """Most common letter size (tops) and waist size 26-44 (bottoms) across past orders.
    Needs min_samples of a kind before claiming anything; otherwise stays unknown."""
    from collections import Counter

    letters = Counter(i["size"] for i in items if i.get("size") in LETTER)
    waists = Counter(i["size"] for i in items if str(i.get("size", "")).isdigit() and 26 <= int(i["size"]) <= 44)
    out: dict[str, Any] = {}
    for key, c in (("size_top", letters), ("size_bottom", waists)):
        if sum(c.values()) >= min_samples:
            size, n = c.most_common(1)[0]
            out[key] = {"value": size, "seen": n, "of": sum(c.values())}
    return out


def order_behaviour(orders: list[dict]) -> dict[str, Any]:
    """Spend pattern from past order confirmation emails (no guessing: only what the emails state)."""
    from collections import Counter
    from email.utils import parsedate_to_datetime

    amounts = [o["amount"] for o in orders if o.get("amount")]
    per_item = [o["amount"] / len(o["items"]) for o in orders if o.get("amount") and o.get("items")]
    dates = []
    for o in orders:
        try:
            dates.append(parsedate_to_datetime(o.get("date")).date())
        except (TypeError, ValueError):
            pass
    shops = Counter(o.get("shop") for o in orders if o.get("shop"))
    return {
        "orders_seen": len(orders),
        "avg_item_price": round(sum(per_item) / len(per_item)) if per_item else None,
        "typical_order_value": round(sorted(amounts)[len(amounts) // 2]) if amounts else None,
        "last_order": max(dates).isoformat() if dates else None,
        "shops_used": ", ".join(f"{s} ({n})" for s, n in shops.most_common(3)),
    }
