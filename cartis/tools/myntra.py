"""Myntra via Apify.

search()        -> sian.agency/myntra-product-scraper, scrapeMode=detail, `queries` input.
recheck_stock() -> khadinakbar/myntra-product-scraper, productIds + enrichDetails, per-size stock.

The actors' exact output keys vary a little, so `normalize()` is defensive and every raw
payload is saved under data/raw/ so the mapping can be tightened after the first live run.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

import requests

from .. import config

APIFY = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
SEARCH_ACTOR = "sian.agency~myntra-product-scraper"
STOCK_ACTOR = "khadinakbar~myntra-product-scraper"
RAW = config.DATA_DIR / "raw"
CACHE = config.DATA_DIR / "cache"
RAW.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)


def _run_actor(actor: str, payload: dict, timeout: int = 240) -> list[dict]:
    r = requests.post(
        APIFY.format(actor=actor),
        params={"token": config.APIFY_TOKEN},
        json=payload,
        timeout=timeout,
    )
    r.raise_for_status()
    items = r.json()
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{actor.split('~')[0]}_{int(time.time())}.json").write_text(json.dumps(items, indent=2, ensure_ascii=False))
    return items


# ---------------------------------------------------------------- normalise
def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return default


def _breakdown(raw: Any) -> dict[int, int]:
    """Accept {"1": 4, ...}, {"one": 4}, [{"rating":1,"count":4}], [4, 2, 3, 10, 50] (1..5)."""
    out: dict[int, int] = {}
    if isinstance(raw, dict):
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        for k, v in raw.items():
            m = re.search(r"\d", str(k))
            star = int(m.group()) if m else words.get(str(k).lower())
            if star and isinstance(v, (int, float)):
                out[star] = int(v)
            elif star and isinstance(v, dict):
                out[star] = int(_first(v, "count", "value", default=0))
    elif isinstance(raw, list):
        if all(isinstance(x, (int, float)) for x in raw) and len(raw) == 5:
            out = {i + 1: int(v) for i, v in enumerate(raw)}
        else:
            for x in raw:
                if isinstance(x, dict):
                    star = _first(x, "rating", "star", "stars", "name")
                    m = re.search(r"\d", str(star))
                    if m:
                        out[int(m.group())] = int(_first(x, "count", "value", default=0))
    return out


def _sizes(raw: Any) -> list[dict]:
    out = []
    for s in raw or []:
        if isinstance(s, str):
            out.append({"label": s, "available": True})
        elif isinstance(s, dict):
            label = _first(s, "label", "size", "name", "value", default="")
            avail = _first(s, "available", "inStock", "in_stock", default=None)
            if avail is None and "inventory" in s:
                avail = (s.get("inventory") or 0) > 0
            out.append({"label": str(label), "available": bool(True if avail is None else avail),
                        "sku": _first(s, "skuId", "sku_id", "sku")})
    return out


def _spec(p: dict, *names: str) -> str | None:
    specs = _first(p, "specifications", "specs", "attributes", default={}) or {}
    if isinstance(specs, list):
        specs = {str(_first(x, "name", "key", default="")): _first(x, "value", default="") for x in specs if isinstance(x, dict)}
    # flattened keys like "specifications.Fabrics" (Apify dataset field projection)
    specs = {**specs, **{k.split(".", 1)[1]: v for k, v in p.items() if k.startswith("specifications.")}}
    low = {str(k).lower(): v for k, v in specs.items()}
    for n in names:  # exact key first ("Fabrics" before "Fabric Purity")
        if n in low and low[n]:
            return str(low[n])
    for n in names:
        for k, v in specs.items():
            if n in str(k).lower():
                return str(v)
    return None


def _fabric(p: dict) -> str | None:
    base = _first(p, "fabric", "material", default=None) or _spec(p, "fabrics", "fabric", "material")
    purity = _spec(p, "fabric purity")
    if base and purity and purity.lower() not in base.lower():
        return f"{base} ({purity})"   # e.g. "Cotton (Blended)" vs "Cotton (Pure)"
    return base


def normalize(p: dict) -> dict:
    pid = str(_first(p, "style_id", "styleId", "productId", "product_id", "id", default=""))
    url = _first(p, "url", "product_url", "productUrl", "landingPageUrl", default="")
    if url and not url.startswith("http"):
        url = "https://www.myntra.com/" + url.lstrip("/")
    if not pid and url:
        m = re.search(r"/(\d{6,})", url)
        pid = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:10]
    images = _first(p, "images", "image_urls", default=[]) or []
    image = images[0] if images and isinstance(images[0], str) else _first(p, "image", "searchImage", "image_url")
    if isinstance(image, dict):
        image = _first(image, "src", "url")
    if isinstance(image, str) and image.startswith("http://"):
        image = "https://" + image[7:]
    return {
        "id": pid,
        "shop": "Myntra",
        "title": _first(p, "product_name", "productName", "name", "title", default=""),
        "brand": _first(p, "brand", default=""),
        "price": _first(p, "price", "selling_price", "sellingPrice", default=None),
        "mrp": _first(p, "mrp", default=None),
        "discount_pct": _first(p, "discount_percent", "discount", default=None),
        "rating": _first(p, "rating", "average_rating", default=None),
        "rating_count": int(_first(p, "rating_count", "ratingCount", "ratings_count", default=0) or 0),
        "reviews_count": _first(p, "reviews_count", "reviewCount", "review_count", default=None),
        "rating_breakdown": _breakdown(_first(p, "rating_breakdown", "ratingBreakdown", "ratings_breakdown", default={})),
        "sizes": _sizes(_first(p, "sizes", "sizes_available", "availableSizes", default=[])),
        "fabric": _fabric(p),
        "fit": _first(p, "fit", default=None) or _spec(p, "fit", "shape"),
        "returnable": p.get("is_returnable"),
        "colour": _first(p, "primary_colour", "baseColour", "primaryColour", "colour", default=None),
        "url": url,
        "image": image,
    }


# ---------------------------------------------------------------- tools
def search(query: str, gender: str | None = None, max_results: int = 25) -> dict[str, Any]:
    if config.mode("apify") == "live":
        payload: dict[str, Any] = {"queries": [query], "scrapeMode": "detail", "maxResults": max_results, "sort": "popularity"}
        if gender:
            payload["gender"] = gender
        raw = _run_actor(SEARCH_ACTOR, payload)
        source = "live:apify/sian.agency"
    else:
        raw = json.loads((config.FIXTURES / "myntra_search.json").read_text())
        source = "fixture"
    products = [normalize(p) for p in raw]
    products = [p for p in products if p["id"]]
    sid = "s" + hashlib.md5(f"{query}{time.time()}".encode()).hexdigest()[:6]
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{sid}.json").write_text(json.dumps({"query": query, "products": products}, ensure_ascii=False))
    return {"search_id": sid, "query": query, "source": source, "count": len(products)}


def load_search(search_id: str) -> list[dict]:
    return json.loads((CACHE / f"{search_id}.json").read_text())["products"]


def recheck_stock(product_id: str, size: str, pincode: str | None = None) -> dict[str, Any]:
    if config.mode("apify") == "live":
        payload = {"productIds": [product_id], "enrichDetails": True, "maxItems": 1}
        if pincode:
            payload["pincode"] = pincode
        items = _run_actor(STOCK_ACTOR, payload, timeout=120)
        src = "live:apify/khadinakbar"
        p = items[0] if items else {}
        sizes = _sizes(p.get("sizes"))
        in_stock_any = p.get("inStock")
        live_price = p.get("price")
    else:
        fx = json.loads((config.FIXTURES / "myntra_stock.json").read_text())
        sizes = fx.get(product_id, fx["_default"])
        src, in_stock_any, live_price = "fixture", None, None
    hit = next((s for s in sizes if s["label"].upper() == size.upper()), None)
    return {
        "product_id": product_id, "size": size, "source": src,
        "available": bool(hit and hit.get("available")),
        "all_sizes": [{"label": s["label"], "available": s.get("available", True)} for s in sizes],
        "in_stock_any": in_stock_any,
        "price_now": live_price,
    }
