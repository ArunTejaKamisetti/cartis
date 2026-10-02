"""Gnani Vachana speech. Endpoints/fields mirror the official `gnani-vachana` SDK (v0.7.9).

STT : POST https://api.vachana.ai/stt/v3  multipart(audio_file, language_code, format) <= 60 s
TTS : POST https://api.vachana.ai/api/v1/tts/inference  json -> raw audio bytes
"""
from __future__ import annotations

import uuid

import requests

from .. import config

BASE = "https://api.vachana.ai"
TTS_VOICE = config.env("GNANI_VOICE", "Kaveri")      # timbre-v2.5 voices, e.g. Kaveri (English), Deepak, Nalini (Hindi)
TTS_MODEL = config.env("GNANI_TTS_MODEL", "timbre-v2.5")
if TTS_MODEL == "timbre-v2.0":                       # Gnani retired v2.0 (Oct 2026): only timbre-v2.5 is accepted
    TTS_MODEL = "timbre-v2.5"
TTS_LANG = config.env("GNANI_TTS_LANG", "auto")      # auto | en-IN | hi-en (Hinglish) | hi-IN | ta-IN ...
STT_LANG = config.env("GNANI_STT_LANG", "en-IN")     # Chatur speaks Hinglish/English


def _headers() -> dict[str, str]:
    return {"X-API-Key-ID": config.GNANI_API_KEY or "", "X-API-Request-ID": str(uuid.uuid4())}


def stt(audio: bytes, filename: str = "clip.wav", language_code: str | None = None) -> str:
    if config.mode("gnani") != "live":
        raise RuntimeError("Gnani key missing: STT unavailable (type instead)")
    r = requests.post(
        f"{BASE}/stt/v3",
        headers=_headers(),
        files={"audio_file": (filename, audio)},
        data={"language_code": language_code or STT_LANG, "format": "transcribe"},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"[gnani] STT {r.status_code}: {r.text[:200]}")
        raise RuntimeError(f"Gnani STT {r.status_code}: {r.text[:200]}")
    return r.json().get("transcript", "")


def tts(text: str, voice: str | None = None) -> bytes:
    """Body follows docs.gnani.ai/api/TTS/tts-inference (timbre-v2.5: text, model, audio_config required).
    If Gnani rejects it, one simpler body is tried, and every failure is printed in the server terminal."""
    if config.mode("gnani") != "live":
        raise RuntimeError("Gnani key missing: TTS unavailable")
    text = text.replace("₹", "rupees ").replace("[[", "").replace("]]", "")   # say the currency, never markers
    cfg = {"sample_rate": 24000, "num_channels": 1, "sample_width": 2, "encoding": "linear_pcm", "container": "wav"}
    must = {"text": text[:1500], "model": TTS_MODEL, "audio_config": cfg}     # the 3 required fields
    v = voice or TTS_VOICE
    # Gnani answers 400 for anything it doesn't like; each retry drops one optional field.
    bodies = [{**must, "voice": v, "language": TTS_LANG, "speed": 1.0}, {**must, "voice": v}, must]
    errors = []
    for body in bodies:
        try:
            r = requests.post(f"{BASE}/api/v1/tts/inference", headers={**_headers(), "Content-Type": "application/json"},
                              json=body, timeout=60)
        except requests.RequestException as e:
            errors.append(f"network: {e}")
            break
        if r.status_code == 200 and (r.content[:4] == b"RIFF" or
                                     r.headers.get("content-type", "").startswith(("audio", "application/octet-stream"))):
            return r.content
        errors.append(f"{r.status_code} {r.headers.get('content-type', '')}: {r.text[:200]}")
        if r.status_code in (401, 403, 429) or r.status_code >= 500:   # key / quota / outage: retrying won't help
            break
    msg = "Gnani TTS failed: " + " | ".join(errors)
    print("[gnani]", msg)
    raise RuntimeError(msg)
