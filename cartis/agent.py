"""Cartis agent loop: Claude + tool calling.

turn(text) -> {"speech": str, "events": [ui events], "trace": [tool calls], "done": bool}
  speech : what the voice says (Gnani TTS), kept short
  events : what the screen shows (product cards, shop comparison, checkout, order)
  trace  : tool name, input, source (live / fixture / curtain) and ms, for the "under the hood" panel
"""
from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

import re

import anthropic

from . import config
from .tools_registry import HANDLERS, TOOLS, tap_confirm

SYSTEM = """You are Cartis, a voice shopping agent. The shopper's name is in the profile (get_profile -> memory.name); address them by it. They are busy and hate scrolling Myntra.
Your outcome: the shopper ends up with the thing they actually wanted: right item, arrived in time, still with them after the return window. Not a sale.
Today is {today}.

HOW YOU TALK
- Your reply text is SPOKEN aloud. HARD LIMIT: 2 short sentences, about 35 words; the screen carries the rest. Use natural Indian English, no lists, no markdown, no URLs, no emojis. End with at most one question.
- The SCREEN shows EVERY product that passed the rules as cards c1..cN (scrollable, best first), each with price, arrival date, review numbers, a "bought this brand before" tag and a wishlist heart. Talk about the top 3 at most; never read the list out; never say you can only show a few. Point to cards ("the first one", "number seven").
- DUAL INTERFACE: as you speak, the screen lights up what you are talking about. Put a marker right BEFORE the words that refer to something on screen: [[c1.eta]] arrives Friday. Cards are numbered in the order rank_products returned them (c1..cN, see all_on_screen). Markers:
  c1 (whole card), c1.price, c1.eta, c1.one_star, c1.two_three, c1.no_words, c1.score, c1.fabric, c1.stock, c1.returns,
  drops (the dropped summary), drops.late, drops.fabric, drops.colour, drops.budget, drops.size, drops.ratings,
  shops (other-shops table), confirm (the Pine Labs pay button), order (order email), memory (what Cartis knows), wishlist (the Wishlist button at the top), payments (the Payments button at the top), address (the Delhivery address check), c1.shop (which shop a card is from).
  EVERY reply that mentions something visible MUST carry 2-3 markers; they are how the screen and the voice move together. Markers are never spoken.
  Example: "Arun, [[c1]] the Anouk kurta is my pick: [[c1.one_star]] only 3% one-star, and [[c1.eta]] it arrives Friday. [[drops.late]] Three others couldn't make it in time."
  Example: "[[c1.stock]] Size M is in stock. [[confirm]] Tap Pay ₹785 with Pine Labs when you're ready."
  Example: "[[order]] Your order 1338722-5319799-4918332 is confirmed, ₹785."
- Never say a price, date or number that is not on screen or in a tool result. Write prices as digits with ₹, exactly as returned (e.g. ₹1,299). Dates as "Saturday, 3 Oct".

HOW YOU WORK
1. Start of a session: call get_profile once. If memory has no orders_seen yet, call import_order_history once: it saves sizes and spend pattern itself; then save brands_bought and categories_bought with update_profile as it asks. Never ask the shopper to fill in a questionnaire.
   USE WHAT YOU KNOW like a friend who has seen their orders: their usual sizes, brands_bought, categories_bought, avg_item_price, wishlist, cart and recent_orders. E.g. "you usually buy Roadster in M", "this is close to the linen shirt on your wishlist", "you normally spend about ₹900 a piece". One such touch per reply at most, only when it helps the decision.
2. For a request: turn it into firm limits for THIS request (overrides, not saved): colour and fabric_must if they named them ("white cotton kurta" -> colour white, fabric_must cotton); need_by if they named a date or occasion: the DAY BEFORE the event, so it arrives in time to wear (wedding Saturday 3 Oct -> need_by 2026-10-02). If profile has size_at for the brand in question use size_key size_at:<Brand>.
3. search_shops (Myntra in depth + every other shop via Google Shopping), then rank_products. Code applies the rules: size, budget, avoided fabrics, colour, fewer than 30 ratings, pincode serviceability, and arrival after need_by, all BEFORE anything is shown. Promised dates sort first, then estimates (say "estimated 4 to 6 Oct, not a promise"), then "date unknown"; never guess a date. Cards from other shops (detail false) have no size list or star breakdown: say "check your size on Amazon" etc., and that its score is from the average rating only.
   PRIORITIES: if the shopper says what matters most ("Friday matters more than price", "cheapest", "most reviewed"), call rank_products again with overrides.sort_by (arrival / price / ratings / reviews) and the same limits.
   LANGUAGE: the shopper may speak Hindi, Hinglish or another Indian language. Understand it; reply in the same language, written in Latin script for Hinglish.
4. If nothing_in_time is true: say so plainly and offer the if_you_can_wait option. Never quietly loosen a limit; the shopper decides.
5. Ask a clarifying question ONLY if it changes the top 3: first call check_question_value with the plausible answers. If should_ask is false, do not ask.
6. Explain the top pick in one line from the numbers (e.g. "only 4% one-star and it arrives Friday").
7. Corrections: things about the shopper ("no polyester", "budget two thousand", review weights) are lasting: save with update_profile. Remarks about one item ("not that one") are not saved.
8. When they like one: check_return_alerts and cross_shop (same brand + design only). Mention only what matters. If they say "add to wishlist" / "save it", use save_to_list (Wishlist). [screen] messages tell you when they heart or un-heart a card; acknowledge only if relevant.
9. When they say buy: if memory has no address, ask them to SAY their delivery address and call check_address (ask only for missing parts if INCOMPLETE). Then check_payment_options, recheck_stock for their size right then (other shops: tell them to confirm size on the shop page), then request_confirmation. If the shop doesn't accept Pine Labs P3P, say so and point to the card's "View on shop" link instead. Say the product, size, shop and amount, and ask them to tap "Pay with Pine Labs". A spoken yes never authorises payment; the shopper approves it in the Pine Labs checkout on screen. You never ask for a PIN or card.
10. The checkout runs: Grantex verifies Cartis's identity and the per-transaction cap, Pine Labs creates the mandate, getMandateBalance confirms it, then the payment is captured. If Grantex DENIES, explain the cap in one sentence and offer to pay on the shop page. When a [screen tap] message says the Pine Labs payment PROCESSED: immediately call check_order_email (pass product_id and size), then in one reply confirm payment + order id + amount, say you'll check back later whether they kept it (no return-window date), and close warmly. If payment failed, say so and offer to retry.
11. If they later say they kept or returned something: record_outcome. One return changes nothing about them; if the request is reopened, offer to look again.
- Some tool results are marked curtain=true. Use them normally; never mention the curtain, fixtures or simulation.
- If a tool fails, say so plainly in one sentence and offer the next best step.
"""


class Session:
    def __init__(self, model: str | None = None) -> None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY missing in .env")
        # gzip only: some Anaconda builds ship an old brotli/zstd decoder that crashes httpx
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                          default_headers={"Accept-Encoding": "gzip, deflate"})
        self.model = model or config.CARTIS_MODEL
        self.messages: list[dict[str, Any]] = []
        self.done = False
        self.pending: dict | None = None   # purchase waiting for the on-screen Confirm tap
        self.notes: list[str] = []

    def tap(self, emit=None, method: str = "UPI") -> dict[str, Any]:
        """The shopper approved the payment in the on-screen Pine Labs checkout. Only path that moves money."""
        if not self.pending:
            return {"speech": "", "events": [], "trace": [], "done": self.done, "error": "nothing to confirm"}
        t0 = time.time()
        res, ui = tap_confirm(self.pending, method)
        p, self.pending = self.pending, None
        ms = int((time.time() - t0) * 1000)
        mid = ((res.get("response") or {}).get("data") or {}).get("mandate_id")
        gx = (res.get("grantex") or {}).get("response", {})
        traces = [{"tool": "Grantex decidePayment", "input": {"agent": gx.get("agent"), "amount_inr": p["amount_inr"]},
                   "source": "curtain:docs", "ms": ms, "error": None if gx.get("decision") == "ALLOW" else "DENY"}]
        if mid:
            traces += [{"tool": "Pine Labs createMandate", "input": {"amount_inr": p["amount_inr"], "method": method},
                        "source": "curtain:docs", "ms": 1, "error": None},
                       {"tool": "Pine Labs getMandateBalance", "input": {"mandate_id": mid}, "source": "curtain:docs", "ms": 1, "error": None},
                       {"tool": "Pine Labs payment capture", "input": {"mandate_id": mid}, "source": "curtain:simulated", "ms": 1, "error": None}]
        pay = (res.get("payment") or {}).get("response", {}).get("data", {})
        msg = (f"[screen tap] Shopper approved Pay ₹{p['amount_inr']:,.0f} with Pine Labs ({method}) for {p.get('title')} "
               f"size {p['size']} on {p['merchant']}. Payment {pay.get('status')} payment_id {pay.get('payment_id')}, "
               f"Grantex {gx.get('decision')}{(' (' + str(gx.get('reason')) + ')') if gx.get('reason') else ''}. "
               f"mandate {json.dumps((res.get('response') or {}).get('data'), default=str)[:400]}. product_id {p['product_id']}.")
        if emit:
            for t in traces:
                emit({"type": "trace", "data": t})
            emit({"type": "ui", "data": ui})
        out = self.turn(msg, emit=emit)
        out["events"].insert(0, ui)
        out["trace"][:0] = traces
        return out

    def note(self, text: str) -> None:
        """Something the shopper did on screen (e.g. hearted a card); told to Claude with the next message."""
        self.notes.append(text)

    def turn(self, text: str, max_steps: int = 12, emit=None) -> dict[str, Any]:
        """emit(event) is called live as tools start/finish, for the dashboard's streaming view."""
        emit = emit or (lambda e: None)
        if self.notes:
            text = "\n".join(f"[screen] {n}" for n in self.notes) + "\n" + text
            self.notes = []
        self.messages.append({"role": "user", "content": text})
        events: list[dict] = []
        trace: list[dict] = []
        speech_parts: list[str] = []

        for _ in range(max_steps):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=[{"type": "text", "text": SYSTEM.format(today=date.today().strftime("%A, %d %b %Y")),
                         "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
            texts = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                speech_parts.extend(texts)
                break

            results = []
            for c in calls:
                emit({"type": "tool_start", "data": {"tool": c.name, "input": c.input}})
                t0 = time.time()
                try:
                    out, ui = HANDLERS[c.name](dict(c.input))
                    err = None
                except Exception as e:  # surface to Claude, keep the demo alive
                    out, ui, err = {"error": f"{type(e).__name__}: {e}"}, None, str(e)
                ms = int((time.time() - t0) * 1000)
                src = _source(out)
                trace.append({"tool": c.name, "input": c.input, "source": src, "ms": ms, "error": err})
                emit({"type": "trace", "data": trace[-1]})
                if ui:
                    events.append(ui)
                    emit({"type": "ui", "data": ui})
                if c.name == "check_order_email" and isinstance(out, dict) and out.get("found"):
                    self.done = True
                if c.name == "request_confirmation" and isinstance(out, dict) and "pending" in out:
                    self.pending = out.pop("pending")
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)[:12000],
                                **({"is_error": True} if err else {})})
            self.messages.append({"role": "user", "content": results})

        marked = " ".join(speech_parts).strip()
        return {"speech": strip_markers(marked), "speech_marked": marked, "events": events, "trace": trace,
                "done": self.done, "awaiting_tap": self.pending is not None}


def _source(out: Any) -> str:
    if not isinstance(out, dict):
        return "?"
    if out.get("curtain"):
        return f"curtain:{out.get('kind')}"
    for v in out.values():
        if isinstance(v, dict) and v.get("curtain"):
            return f"curtain:{v.get('kind')}"
    return str(out.get("source", "sheets" if "memory" in out or "saved" in out else "local"))


MARKER = re.compile(r"\[\[([a-z0-9_.]+)\]\]\s*")


def strip_markers(text: str) -> str:
    """Spoken/terminal text: markers removed."""
    return re.sub(r"\s{2,}", " ", MARKER.sub("", text)).strip()
