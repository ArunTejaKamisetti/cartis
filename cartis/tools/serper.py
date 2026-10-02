"""Serper Shopping: cross-shop listings for the same product (India)."""
from __future__ import annotations

import json
import re
from typing import Any

import requests

from .. import config


def _price(s: Any) -> float | None:
    m = re.search(r"[\d,]+(?:\.\d+)?", str(s or ""))
    return float(m.group().replace(",", "")) if m else None


def cross_shop(query: str, exclude_shop: str | None = "Myntra", limit: int = 6) -> dict[str, Any]:
    if config.mode("serper") == "live":
        r = requests.post(
            "https://google.serper.dev/shopping",
            headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "gl": "in", "hl": "en"},
            timeout=30,
        )
        r.raise_for_status()
        items, src = r.json().get("shopping", []), "live:serper"
    else:
        items, src = json.loads((config.FIXTURES / "serper_shopping.json").read_text())["shopping"], "fixture"
    listings = [
        {
            "shop": it.get("source"),
            "title": it.get("title"),
            "price": _price(it.get("price")),
            "price_text": it.get("price"),
            "rating": it.get("rating"),
            "rating_count": it.get("ratingCount"),
            "url": it.get("link"),
            "image": it.get("imageUrl"),
        }
        for it in items
        if not (exclude_shop and exclude_shop.lower() in str(it.get("source", "")).lower())
    ][:limit]
    listings.sort(key=lambda x: x["price"] or 1e9)
    return {"query": query, "source": src, "excluded_shop": exclude_shop, "listings": listings,
            "note": "Listings may be a different variant; compare titles before calling one the same product."}


# ---------------------------------------------------------------- multi-shop search
FABRICS = ["pure cotton", "cotton blend", "cotton", "linen blend", "linen", "polyester", "rayon", "viscose", "silk",
           "khadi", "denim", "wool", "nylon", "modal", "lyocell", "chambray", "corduroy"]
COLOURS = ["off white", "white", "black", "brown", "beige", "navy", "blue", "green", "olive", "maroon", "red", "pink",
           "grey", "gray", "yellow", "mustard", "orange", "purple", "khaki", "cream", "tan", "charcoal", "rust", "coffee"]
# brown-family words count as brown, etc., so a colour filter isn't fooled by shop wording
COLOUR_FAMILY = {"coffee": "brown", "tan": "brown", "rust": "brown", "chocolate": "brown", "off white": "white",
                 "cream": "white", "gray": "grey", "charcoal": "grey", "olive": "green", "navy": "blue"}


def _find(words: list[str], text: str) -> str | None:
    t = f" {text.lower()} "
    for w in words:
        if f" {w} " in t or f" {w}," in t or f" {w}-" in t:
            return w
    return None


def normalize_listing(it: dict) -> dict:
    """A Google Shopping listing as a Cartis product. Light record: no size list and no star
    breakdown, so the rules mark those as unknown instead of guessing."""
    import hashlib

    title = it.get("title") or ""
    colour = _find(COLOURS, title)
    return {
        "id": "g" + hashlib.md5((it.get("link") or title).encode()).hexdigest()[:10],
        "shop": it.get("source") or "Shop",
        "title": title,
        "brand": title.split(" ")[0] if title else "",
        "price": _price(it.get("price")),
        "mrp": None,
        "rating": it.get("rating"),
        "rating_count": int(it.get("ratingCount") or 0),
        "reviews_count": None,
        "rating_breakdown": {},
        "sizes": [],
        "fabric": (_find(FABRICS, title) or "").title() or None,
        "colour": (COLOUR_FAMILY.get(colour, colour) or "").title() or None,
        "url": it.get("link"),
        "image": it.get("imageUrl"),
        "detail": False,          # light listing: size, fabric, star breakdown not verified
        "source": "google_shopping",
    }


def shop_search(query: str, num: int = 40, exclude_shops: tuple[str, ...] = ("myntra",)) -> dict[str, Any]:
    """Google Shopping (via Serper) across every Indian shop. Myntra is excluded here because
    the Apify detail scrape gives a much richer record for Myntra."""
    if config.mode("serper") == "live":
        r = requests.post(
            "https://google.serper.dev/shopping",
            headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "gl": "in", "hl": "en", "num": num},
            timeout=30,
        )
        r.raise_for_status()
        items, src = r.json().get("shopping", []), "live:serper"
    else:
        items, src = json.loads((config.FIXTURES / "serper_search.json").read_text())["shopping"], "fixture"
    out = [normalize_listing(i) for i in items
           if not any(x in str(i.get("source", "")).lower() for x in exclude_shops)]
    return {"source": src, "products": [p for p in out if p["price"]]}
