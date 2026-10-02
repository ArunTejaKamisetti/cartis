"""One OAuth token for Gmail (read-only) + Sheets. Run `python -m cartis.cli auth` once on the laptop."""
from __future__ import annotations

from pathlib import Path

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",      # the order receipt Cartis emails to the shopper
    "https://www.googleapis.com/auth/spreadsheets",
]


def token_has_all_scopes() -> bool:
    import json
    tok = Path(config.GOOGLE_TOKEN)
    raw = tok.read_text() if tok.exists() else (config.env("GOOGLE_TOKEN_JSON") or "")
    try:
        return set(SCOPES) <= set(json.loads(raw).get("scopes") or [])
    except ValueError:
        return False


def granted(creds) -> set[str]:
    return set(getattr(creds, "granted_scopes", None) or creds.scopes or [])


def can_send() -> bool:
    """True only if Google granted gmail.send (checked before trying, so the order card can say why)."""
    import json
    tok = Path(config.GOOGLE_TOKEN)
    raw = tok.read_text() if tok.exists() else (config.env("GOOGLE_TOKEN_JSON") or "{}")
    return "https://www.googleapis.com/auth/gmail.send" in (json.loads(raw).get("scopes") or [])


def manual_flow():
    """For shells without a browser: open the URL anywhere, then paste back the localhost URL you land on."""
    import os

    from google_auth_oauthlib.flow import Flow

    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_secrets_file(config.GOOGLE_CLIENT_SECRET, SCOPES, redirect_uri="http://localhost:8765/")
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    print("1) Open this URL, sign in, allow access:\n" + url)
    print("2) The browser lands on a localhost page that fails to load. Copy that whole URL.")
    resp = input("Paste it here: ").strip()
    flow.fetch_token(authorization_response=resp)
    _save(Path(config.GOOGLE_TOKEN), flow.credentials)
    return flow.credentials


def _save(tok: Path, creds) -> None:
    import json
    data = json.loads(creds.to_json())
    data["scopes"] = sorted(granted(creds))      # record what Google really granted, never what we asked for
    try:
        tok.write_text(json.dumps(data))
    except OSError:   # read-only secret file on the host: the refreshed token just lives in memory
        pass


def credentials():
    import json

    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    tok = Path(config.GOOGLE_TOKEN)
    if not tok.exists() and config.env("GOOGLE_TOKEN_JSON"):   # hosting: token passed as an env var
        tok = config.DATA_DIR / "token.json"
        tok.write_text(config.env("GOOGLE_TOKEN_JSON"))
    info = json.loads(tok.read_text()) if tok.exists() else None
    creds = Credentials.from_authorized_user_info(info) if info else None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            if "invalid_scope" not in str(e):
                raise
            # the file lists a scope Google never granted (e.g. gmail.send): refresh without asking for
            # specific scopes, so Google hands back exactly what the user did allow
            info.pop("scopes", None)
            creds = Credentials.from_authorized_user_info(info)
            creds.refresh(Request())
            print("[google] token refreshed with the permissions Google actually granted:",
                  ", ".join(sorted(x.rsplit("/", 1)[-1] for x in granted(creds))) or "unknown")
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(config.GOOGLE_CLIENT_SECRET, SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    _save(tok, creds)
    return creds


def service(name: str, version: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=credentials(), cache_discovery=False)
