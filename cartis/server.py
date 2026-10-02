"""HTTP layer + dashboard. Run: uvicorn cartis.server:app --port 8000   then open http://localhost:8000

GET  /                 dashboard (static/index.html)
GET  /status           tool modes
GET  /profile          Memory + ReviewRules + recent Orders (the "what Cartis knows" panel)
POST /reset            rehearsal reset (profile/rules to defaults) + fresh session
POST /chat             {"session_id","text"} -> Server-Sent Events: tool_start, trace, ui, final
POST /tap              {"session_id"}        -> SSE: Confirm ₹X tapped, creates the mandate
POST /stt              multipart audio (WAV, <=60 s) -> {"text"}
POST /tts              {"text"} -> audio/wav
"""
from __future__ import annotations

import json
import queue
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import config
from .agent import Session
from .tools import gnani

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Cartis")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
PASSCODE = config.env("DEMO_PASSCODE")   # optional: protects the paid APIs behind a public link


@app.middleware("http")
async def passcode(request: Request, call_next):
    if PASSCODE and request.url.path not in ("/", "/healthz") and request.headers.get("X-Cartis-Key") != PASSCODE:
        return JSONResponse({"detail": "passcode required"}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


SESSIONS: dict[str, Session] = {}
LOCKS: dict[str, threading.Lock] = {}


class ChatIn(BaseModel):
    text: str
    session_id: str | None = None


class TapIn(BaseModel):
    session_id: str
    method: str = "UPI"


class WishIn(BaseModel):
    product_id: str
    on: bool = True
    session_id: str | None = None


class TTSIn(BaseModel):
    text: str


def _session(sid: str | None) -> tuple[str, Session]:
    sid = sid or uuid.uuid4().hex[:8]
    if sid not in SESSIONS:
        SESSIONS[sid] = Session()
        LOCKS[sid] = threading.Lock()
    return sid, SESSIONS[sid]


def _stream(sid: str, run) -> StreamingResponse:
    """Run a turn in a worker thread; forward its live events as SSE."""
    q: queue.Queue = queue.Queue()

    def work():
        with LOCKS[sid]:
            try:
                out = run(q.put)
                q.put({"type": "final", "data": {"session_id": sid, **out}})
            except Exception as e:  # keep the stream well-formed for the UI
                q.put({"type": "error", "data": {"message": f"{type(e).__name__}: {e}"}})
            finally:
                q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen():
        yield f"data: {json.dumps({'type': 'session', 'data': {'session_id': sid}})}\n\n"
        while (e := q.get()) is not None:
            yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/status")
def status():
    from .tools_registry import ORDER_EMAIL

    return {**{t: config.mode(t) for t in ("apify", "serper", "gnani", "google")},
            "order_email": ORDER_EMAIL, "model": config.CARTIS_MODEL}


@app.get("/profile")
def profile():
    from .tools_registry import store

    from .tools_registry import wishlist_rows

    return {"memory": store.rows("Memory"), "review_rules": store.rows("ReviewRules"),
            "orders": store.rows("Orders")[-5:], "wishlist": wishlist_rows()[-8:],
            "wishlist_count": len(wishlist_rows())}


@app.post("/reset")
def reset():
    from .store import reset_profile
    from .tools_registry import PAYMENTS

    reset_profile()
    PAYMENTS.clear()
    SESSIONS.clear()
    LOCKS.clear()
    return {"ok": True}


@app.post("/chat")
def chat(body: ChatIn):
    sid, s = _session(body.session_id)
    return _stream(sid, lambda emit: s.turn(body.text, emit=emit))


@app.post("/tap")
def tap(body: TapIn):
    """The on-screen Confirm button. The only way a mandate gets created."""
    if body.session_id not in SESSIONS:
        raise HTTPException(404, "unknown session")
    s = SESSIONS[body.session_id]
    return _stream(body.session_id, lambda emit: s.tap(emit=emit, method=body.method))


class AddressIn(BaseModel):
    raw: str = ""
    house: str = ""
    street: str = ""
    locality: str = ""
    landmark: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    session_id: str | None = None


@app.post("/address")
def address(body: AddressIn):
    """The Address button: Delhivery standardize + validate + verify, saved if VALID."""
    from .tools_registry import h_address

    data = body.model_dump()
    sid = data.pop("session_id", None)
    data["raw"] = data["raw"] or ", ".join(v for k, v in data.items() if k != "raw" and v)
    res, _ = h_address(data)
    if sid in SESSIONS:
        r = res["response"]
        SESSIONS[sid].note(f"Shopper entered their address on screen: {r['validation']['status']} "
                           f"({', '.join(v for v in r['standardized'].values() if v)}).")
    return res


@app.get("/wishlist")
def wishlist_list():
    from .tools_registry import _SEEN, wishlist_rows

    items = []
    for r in wishlist_rows()[::-1]:
        seen = _SEEN.get(str(r.get("product_id")), {})   # photo + shop when the card was seen this run
        items.append({**r, "shop": seen.get("shop") or "", "image": seen.get("image") or ""})
    return {"items": items}


@app.get("/payments")
def payments(session_id: str | None = None):
    """The Payments button: what's waiting for approval, and what was paid through Pine Labs."""
    from .tools_registry import PAYMENTS, store

    s = SESSIONS.get(session_id or "")
    return {"pending": s.pending if s else None, "history": PAYMENTS[::-1],
            "address": store.memory().get("address"), "methods": ["UPI ReservePay", "Card pre-auth"]}


@app.post("/wishlist")
def wishlist(body: WishIn):
    """The heart on a card. Saved to the Wishlist tab; Cartis hears about it with the next message."""
    from .tools_registry import toggle_wishlist

    res = toggle_wishlist(body.product_id, body.on)
    if body.session_id in SESSIONS:
        SESSIONS[body.session_id].note(f"Shopper {'added' if body.on else 'removed'} '{res.get('title')}' "
                                       f"{'to' if body.on else 'from'} their wishlist.")
    return res


@app.post("/stt")
async def stt(audio: UploadFile = File(...), language: str = Form("en-IN")):
    try:
        return {"text": gnani.stt(await audio.read(), audio.filename or "clip.wav", language), "language": language}
    except Exception as e:
        raise HTTPException(502, f"STT failed: {e}")


@app.post("/tts")
def tts(body: TTSIn):
    try:
        return Response(gnani.tts(body.text), media_type="audio/wav")
    except Exception as e:
        raise HTTPException(502, f"TTS failed: {e}")
