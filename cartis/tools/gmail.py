"""Gmail, read-only, order emails only.

The query is hard-scoped to order/shipping confirmations from shops, so Cartis never reads
anything else in the inbox. This is the last step of the recording.
"""
from __future__ import annotations

import base64
import html
import json
import re
from typing import Any

from .. import config

SHOP_SENDERS = ["myntra.com", "ajio.com", "amazon.in", "flipkart.com", "tatacliq.com", "nykaa.com"]
# Confirmation emails only: placed/confirmed; skip the shipped/delivered/feedback/refund updates
CONFIRM = '(placed OR confirmed OR confirmation OR "thank you for your order" OR "thanks for your order")'
SKIP = '-subject:(delivered OR shipped OR dispatched OR "out for delivery" OR feedback OR review OR cancelled OR return OR refund OR exchange)'
ORDER_QUERY = '({senders}) subject:' + CONFIRM + ' ' + SKIP + ' newer_than:{days}d'
ANY_QUERY = '({senders}) newer_than:{days}d'


def _body(payload: dict) -> str:
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "ignore")
    return "\n".join(_body(p) for p in payload.get("parts", []) or [])


ORDER_ID_PATTERNS = [
    r"\b(\d{7}-\d{7}-\d{5,7})\b",                                   # Myntra / Amazon style 1234567-1234567-1234567
    r"\b(\d{3}-\d{7}-\d{7})\b",                                     # Amazon 403-1234567-1234567
    r"\b(OD\d{12,})\b",                                               # Flipkart
    r"order\s*(?:id|no\.?|number|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{6,})",  # generic, needs a label
]
AMOUNT_PATTERNS = [
    r"(?:order\s*total|total\s*amount|amount\s*paid|grand\s*total|total\s*paid|total)\s*[:\-]?\s*(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)",
    r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)",
]


def _parse(subject: str, sender: str, text: str) -> dict[str, Any]:
    blob = f"{subject}\n{text}"
    oid = next((m for p in ORDER_ID_PATTERNS if (m := re.search(p, blob, re.I))), None)
    amt = next((m for p in AMOUNT_PATTERNS if (m := re.search(p, text, re.I))), None)
    shop = next((s.split(".")[0].title() for s in SHOP_SENDERS if s in sender.lower()), sender)
    return {
        "order_id": oid.group(1) if oid else None,
        "amount": float(amt.group(1).replace(",", "")) if amt else None,
        "shop": shop,
    }


def recent_shop_emails(days: int = 365, n: int = 15) -> list[dict[str, Any]]:
    """Debug helper: subjects/senders of recent shop emails, to calibrate the filters."""
    from ..google_auth import service

    gm = service("gmail", "v1").users()
    q = ANY_QUERY.format(senders=" OR ".join(f"from:{s}" for s in SHOP_SENDERS), days=days)
    out = []
    for m in gm.messages().list(userId="me", q=q, maxResults=n).execute().get("messages", []):
        msg = gm.messages().get(userId="me", id=m["id"], format="metadata",
                                metadataHeaders=["Subject", "From", "Date"]).execute()
        h = {x["name"].lower(): x["value"] for x in msg["payload"].get("headers", [])}
        out.append({"date": h.get("date"), "from": h.get("from"), "subject": h.get("subject")})
    return out


SIZE_RE = r"\bSize\s*[:\-]?\s*(XXS|XS|S|M|L|XL|XXL|XXXL|2XL|3XL|4XL|\d{1,2}(?:\.\d)?|UK\s?\d{1,2}|Free\s?Size)\b"


def _message_text(gm, mid: str) -> tuple[dict, str]:
    m = gm.messages().get(userId="me", id=mid, format="full").execute()
    hdr = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
    text = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", _body(m["payload"]))
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return hdr, re.sub(r"\s+", " ", text)


def parse_items(text: str) -> list[dict[str, str]]:
    """Size + the item text just before it, from an order email. Claude reads the brand out of
    `item_text`; nothing is guessed here (Round 2 S0: a missing size stays unknown)."""
    items, prev_end = [], 0
    for m in re.finditer(SIZE_RE, text, re.I):
        start = max(prev_end, m.start() - 140)
        items.append({"size": m.group(1).upper().replace(" ", ""), "item_text": text[start:m.start()].strip()})
        prev_end = m.end()
    return items


def past_orders(days: int = 365, n: int = 20) -> list[dict[str, Any]]:
    """S0 'learn you without a form': read past ORDER CONFIRMATION emails once."""
    if config.mode("google") != "live":
        return json.loads((config.FIXTURES / "gmail_past_orders.json").read_text())
    from ..google_auth import service

    gm = service("gmail", "v1").users()
    q = ORDER_QUERY.format(senders=" OR ".join(f"from:{s}" for s in SHOP_SENDERS), days=days)
    out = []
    for ref in gm.messages().list(userId="me", q=q, maxResults=n).execute().get("messages", []):
        hdr, text = _message_text(gm, ref["id"])
        out.append({"date": hdr.get("date"), "subject": hdr.get("subject"),
                    **_parse(hdr.get("subject", ""), hdr.get("from", ""), text), "items": parse_items(text)})
    return out


def latest_text(days: int = 365) -> str:
    """Debug: cleaned text of the latest confirmation, to calibrate parse_items."""
    from ..google_auth import service

    gm = service("gmail", "v1").users()
    q = ORDER_QUERY.format(senders=" OR ".join(f"from:{s}" for s in SHOP_SENDERS), days=days)
    ref = gm.messages().list(userId="me", q=q, maxResults=1).execute().get("messages", [])
    return _message_text(gm, ref[0]["id"])[1] if ref else ""


def latest_order_confirmation(shop: str | None = None, days: int = 1) -> dict[str, Any]:
    senders = [s for s in SHOP_SENDERS if not shop or shop.lower() in s] or SHOP_SENDERS
    q = ORDER_QUERY.format(senders=" OR ".join(f"from:{s}" for s in senders), days=days)
    if config.mode("google") != "live":
        fx = json.loads((config.FIXTURES / "gmail_order.json").read_text())
        return {"found": True, "source": "fixture", "query": q, **fx}

    from ..google_auth import service

    gm = service("gmail", "v1").users()
    res = gm.messages().list(userId="me", q=q, maxResults=1).execute()
    msgs = res.get("messages", [])
    if not msgs:
        return {"found": False, "source": "live:gmail", "query": q}
    m = gm.messages().get(userId="me", id=msgs[0]["id"], format="full").execute()
    hdr = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
    text = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", _body(m["payload"]))
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))   # &#8377; -> ₹, &nbsp; -> space
    text = re.sub(r"\s+", " ", text)
    parsed = _parse(hdr.get("subject", ""), hdr.get("from", ""), text)
    money = [text[max(0, x.start() - 60):x.end() + 20] for x in re.finditer(r"₹|Rs\.?\s?\d|INR|[Tt]otal", text)][:6]
    return {
        "found": True, "source": "live:gmail", "query": q, "email_id": m["id"],
        **({"amount_context": money} if parsed["amount"] is None else {}),
        "subject": hdr.get("subject"), "from": hdr.get("from"), "date": hdr.get("date"),
        "snippet": html.unescape(m.get("snippet") or ""), **parsed,
    }



# ---------------------------------------------------------------- real order receipt (sent, then read back)
RECEIPT_SUBJECT = "Cartis · Order confirmed"


def my_address() -> str:
    from ..google_auth import service
    return service("gmail", "v1").users().getProfile(userId="me").execute()["emailAddress"]


def send_receipt(order: dict) -> dict:
    """Email the shopper (from and to their own Gmail) a receipt for the order Cartis just placed."""
    import base64 as b64
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from ..google_auth import service

    me = my_address()
    o = order
    rows = "".join(
        f'<tr><td style="padding:6px 0;color:#6b7690;width:140px">{k}</td><td style="padding:6px 0;color:#0f1a2e"><b>{v}</b></td></tr>'
        for k, v in [("Order ID", o["order_id"]), ("Item", o.get("title", "")), ("Size", o.get("size", "")),
                     ("Shop", o.get("shop", "")), ("Amount", f"₹{o['amount']:,.0f}"),
                     ("Paid via", f"Pine Labs · {o.get('method', 'UPI')}"), ("Payment ID", o.get("payment_id", "")),
                     ("Mandate", o.get("mandate_id", "")), ("Delivery", o.get("eta_text", "")),
                     ("Deliver to", o.get("address", ""))] if v)
    link = f'<p style="margin:18px 0 0"><a href="{o["url"]}" style="background:#2f6bff;color:#fff;text-decoration:none;padding:10px 16px;border-radius:10px;font-weight:600">View on {o.get("shop", "shop")}</a></p>' if o.get("url") else ""
    html_body = f"""<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #e6e9f0;border-radius:16px;overflow:hidden">
<div style="background:#0b0d17;color:#fff;padding:18px 22px"><b style="font-size:18px">cartis</b><span style="float:right;color:#8b7cff">Order confirmed</span></div>
<div style="padding:22px"><p style="margin:0 0 12px;font-size:15px">Hi {o.get("name") or "there"}, your order is confirmed.</p>
<table style="width:100%;border-collapse:collapse;font-size:14px">{rows}</table>{link}
<p style="margin:20px 0 0;color:#6b7690;font-size:12.5px">Cartis will check back on {o.get("check_back_on", "")}: did you keep it?<br>
Demo order placed through Cartis (The Ken case competition). Payment was a Pine Labs sandbox simulation.</p></div></div>"""
    msg = MIMEMultipart("alternative")
    msg["To"], msg["From"] = me, f"Cartis <{me}>"
    msg["Subject"] = f"{RECEIPT_SUBJECT} · {o.get('shop', '')} · {o['order_id']}"
    msg.attach(MIMEText(f"Order {o['order_id']} confirmed: {o.get('title')} ({o.get('size')}) ₹{o['amount']:,.0f}", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    raw = b64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = service("gmail", "v1").users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"sent": True, "to": me, "message_id": sent.get("id"), "subject": msg["Subject"]}


def read_receipt(order_id: str, tries: int = 6) -> dict:
    """Read the receipt back from the inbox (proves the email really arrived)."""
    import time as _t

    from ..google_auth import service

    gm = service("gmail", "v1").users()
    q = f'subject:"{RECEIPT_SUBJECT}" "{order_id}" newer_than:1d'
    for _ in range(tries):
        refs = gm.messages().list(userId="me", q=q, maxResults=1).execute().get("messages", [])
        if refs:
            hdr, text = _message_text(gm, refs[0]["id"])
            return {"found": True, "source": "live:gmail", "query": q, "email_id": refs[0]["id"],
                    "subject": hdr.get("subject"), "from": hdr.get("from"), "date": hdr.get("date"),
                    **_parse(hdr.get("subject", ""), "", text)}
        _t.sleep(1.5)
    return {"found": False, "source": "live:gmail", "query": q}
