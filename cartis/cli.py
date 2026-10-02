"""Terminal harness for Cartis.

  python -m cartis.cli                 chat in the terminal (type as the shopper)
  python -m cartis.cli --voice         also speak each reply with Gnani TTS (saved + played)
  python -m cartis.cli script          run the scripted demo end to end
  python -m cartis.cli status          show which tools are live vs fixture
  python -m cartis.cli auth            one-time Google OAuth (Gmail read-only + Sheets)
  python -m cartis.cli setup-sheet     create the 5 tabs in CARTIS_SHEET_ID
  python -m cartis.cli reset           before a rehearsal/recording: profile + rules back to defaults
  python -m cartis.cli speak "text"    Gnani TTS test -> data/audio/*.wav
  python -m cartis.cli hear clip.wav   Gnani STT test
  python -m cartis.cli orders 365      test Gmail order-email parsing on your latest past order
  python -m cartis.cli orders --import past order emails -> sizes per item (S0)
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time

from . import config

B, D, G, Y, C, R, X = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[36m", "\033[31m", "\033[0m"
AUDIO = config.DATA_DIR / "audio"
AUDIO.mkdir(exist_ok=True)

DEMO_SCRIPT = [
    "Hey Cartis, I need a white cotton kurta for a friend's wedding three days from now.",
    "Why the first one over the others?",
    "Actually I'd trust it more if one-star reviews counted even more. Make that weight 0.6.",
    "OK the top one looks good. Any return issues with my size, and is it cheaper anywhere else?",
    "Fine, let's buy it on Myntra.",
    "<TAP>",
    "Done, I've paid on Myntra.",
]


def status() -> None:
    print(f"{B}Cartis tool modes{X}")
    print(f"  model        {config.CARTIS_MODEL}  key={'yes' if config.ANTHROPIC_API_KEY else R + 'MISSING' + X}")
    for t, label in [("apify", "Apify (Myntra search + stock)"), ("serper", "Serper Shopping"),
                     ("gnani", "Gnani STT/TTS"), ("google", "Gmail + Sheets")]:
        m = config.mode(t)
        print(f"  {label:32} {G if m == 'live' else Y}{m}{X}")
    print(f"  {'Sheets id':32} {config.SHEET_ID or Y + 'not set -> data/sheets.json' + X}")
    print(f"  {'Delhivery / Pine Labs':32} {C}curtain{X} (edit /curtain/*.json)")
    from .tools_registry import ORDER_EMAIL
    print(f"  {'Order email (last step)':32} {C if ORDER_EMAIL == 'curtain' else G}{ORDER_EMAIL}{X}")


def render(out: dict) -> None:
    for t in out["trace"]:
        src = t["source"]
        col = G if src.startswith("live") else C if src.startswith("curtain") else D
        err = f" {R}ERR {t['error']}{X}" if t["error"] else ""
        print(f"  {D}->{X} {t['tool']:22} {col}{src:18}{X} {D}{t['ms']:>5} ms{X}{err}")
    for e in out["events"]:
        _screen(e)
    if out["speech"]:
        print(f"\n{B}Cartis:{X} {out['speech']}\n")


def _screen(e: dict) -> None:
    t, d = e["type"], e["data"]
    if t == "products":
        c = d["counts"]
        print(f"  {C}[screen] top 3{X} {D}({c['in']} found, {c['dropped']} dropped by rules){X}")
        for i, p in enumerate(d["top"], 1):
            s = p.get("shares") or {}
            pct = lambda k: f"{(s.get(k) or 0) * 100:.0f}%" if s.get(k) is not None else "n/a"
            name = p['title'] if str(p['title']).lower().startswith(str(p['brand']).lower()) else f"{p['brand']} {p['title']}"
            rc = p.get("review_counts") or {}
            eta = p.get("eta") or "date unknown"
            print(f"   {i}. {name[:60]}  Rs{p['price']}  {p.get('fabric')}  {p.get('colour') or ''}  arrives {B}{eta}{X}  "
                  f"score {B}{p['review_score']}{X} {D}(1*:{pct('one_star_share')} 2-3*:{pct('two_three_star_share')} "
                  f"no-comment:{pct('no_comment_share')} | {rc.get('two_three_star')} two-three-star, "
                  f"{rc.get('no_comment')} with no words, of {p['rating_count']}){X}")
        if d.get("nothing_in_time"):
            print(f"   {Y}nothing arrives by {d['prefs_used'].get('need_by')}; if you can wait: {d['if_you_can_wait']}{X}")
        if d.get("dropped_by_reason"):
            print(f"   {D}dropped: " + "; ".join(f"{k} x{v}" for k, v in d["dropped_by_reason"].items()) + X)
    elif t == "compare_shops":
        print(f"  {C}[screen] other shops{X}")
        for l in d["listings"]:
            print(f"   - {l['shop']:12} {l['price_text']:>9}  {D}{(l['title'] or '')[:50]}{X}")
    elif t == "confirm_required":
        print(f"  {C}[screen] button{X} {B}[ {d['button']} ]{X} {d.get('title')} size {d['size']} on {d['merchant']}")
    elif t == "checkout":
        m = d["mandate"]["response"]
        print(f"  {C}[screen] checkout{X} mandate {json.dumps(m.get('data', m))[:140]}  pay on: {d.get('pay_url')}")
    elif t in ("order_confirmed", "order_pending"):
        print(f"  {C}[screen] {t}{X} {d.get('shop')} order {d.get('order_id')} Rs{d.get('amount')}")
    elif t == "question_check":
        print(f"  {C}[screen] ask?{X} {d['should_ask']} (worst overlap {d['worst_overlap']})")
    else:
        print(f"  {C}[screen] {t}{X} {D}{json.dumps(d, default=str)[:160]}{X}")


def _speak(text: str) -> None:
    from .tools import gnani

    try:
        wav = gnani.tts(text)
    except Exception as e:
        print(f"  {R}TTS failed: {e}{X}")
        return
    path = AUDIO / f"reply_{int(time.time())}.wav"
    path.write_bytes(wav)
    player = "afplay" if platform.system() == "Darwin" else next((p for p in ("aplay", "paplay", "ffplay") if shutil.which(p)), None)
    if player:
        subprocess.run([player, *(["-nodisp", "-autoexit"] if player == "ffplay" else []), str(path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"  {D}audio saved {path}{X}")


def chat(lines: list[str] | None = None, voice: bool = False) -> None:
    from .agent import Session

    status()
    s = Session()
    feed = iter(lines) if lines else None
    while not s.done:
        if feed:
            try:
                text = next(feed)
            except StopIteration:
                break
            if text == "<TAP>":
                if s.pending:
                    print(f"{B}[tap]{X} Confirm Rs{s.pending['amount_inr']}")
                    render(s.tap())
                continue
            print(f"{B}Shopper:{X} {text}")
        else:
            try:
                text = input(f"{B}Shopper:{X} ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
        out = s.turn(text)
        render(out)
        if voice and out["speech"]:
            _speak(out["speech"])
        if not feed and s.pending:   # interactive: the tap is a separate action, not a spoken yes
            if input(f"{B}[screen]{X} tap Confirm Rs{s.pending['amount_inr']}? (y/N) ").strip().lower() == "y":
                out = s.tap()
                render(out)
                if voice and out["speech"]:
                    _speak(out["speech"])
    if s.done:
        print(f"{G}Session ended: order confirmation email read.{X}")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].startswith("--") else "chat"
    voice = "--voice" in args
    if cmd == "status":
        status()
    elif cmd == "script":
        chat(DEMO_SCRIPT, voice)
    elif cmd == "auth":
        from pathlib import Path as _P

        from .google_auth import SCOPES, credentials, granted, manual_flow
        tok = _P(config.GOOGLE_TOKEN)
        if tok.exists():
            tok.unlink()   # always a fresh sign-in, so Google shows every permission again
        print("A Google sign-in page will open. TICK ALL THE BOXES, including 'Send email on your behalf'.")
        creds = manual_flow() if "--manual" in args else credentials()
        missing = [s_.rsplit("/", 1)[-1] for s_ in SCOPES if s_ not in granted(creds)]
        if missing:
            print("Google did NOT grant: " + ", ".join(missing) + ". Run `python -m cartis.cli auth` again and tick every box.")
        else:
            print("Google token saved with all permissions (read orders, send receipt, Sheets).")
            print("Next: python -m cartis.cli mailtest   (sends you a test receipt)")
    elif cmd == "mailtest":
        from .tools import gmail

        r = gmail.send_receipt({"order_id": "TEST-0001", "amount": 999, "shop": "Myntra", "title": "Cartis test receipt",
                                "size": "M", "method": "UPI ReservePay", "payment_id": "v1-pay-test",
                                "mandate_id": "v1-mdt-test", "name": "Arun", "eta_text": "", "address": "",
                                "check_back_on": ""})
        print("Sent to", r.get("to"), "- check your inbox for 'Cartis · Order confirmed'.")
    elif cmd == "reset":
        from .store import reset_profile

        reset_profile()
    elif cmd == "setup-sheet":
        from .store import setup_sheet

        setup_sheet()
    elif cmd == "orders":
        from .tools import gmail

        nums = [a for a in args[1:] if a.isdigit()]
        days = int(nums[0]) if nums else 365
        if "--text" in args:
            print(gmail.latest_text(days)[:3000])
        elif "--import" in args:
            print(json.dumps(gmail.past_orders(days), indent=2, default=str))
        elif "--list" in args:
            for e in gmail.recent_shop_emails(days):
                print(f"{e['date'][:16]:16}  {e['from'][:28]:28}  {e['subject']}")
        else:
            print(json.dumps(gmail.latest_order_confirmation(None, days), indent=2, default=str))
    elif cmd == "speak":
        _speak(" ".join(a for a in args[1:] if a != "--voice") or "Hi Arun, this is Cartis.")
    elif cmd == "hear":
        from .tools import gnani

        path = args[1]
        print(gnani.stt(open(path, "rb").read(), path.split("/")[-1]))
    else:
        chat(voice=voice)


if __name__ == "__main__":
    main()
