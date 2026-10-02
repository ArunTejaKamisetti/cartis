# Cartis v1: backend

Claude agent + tools, tested in the terminal. The dashboard comes next and calls `cartis/server.py`.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env              # fill in the keys you have
python -m cartis.cli status       # which tools are live / fixture / curtain
python -m cartis.cli script       # scripted Chatur run, end to end
python -m cartis.cli              # chat as Chatur   (--voice to hear Gnani TTS)
python -m pytest -q tests         # rules tests
```
Any tool without a key runs on `fixtures/` (synthetic rehearsal data), so the loop works before every key is in.

## Map (flow → code)
| Flow step | Tool | Where |
|---|---|---|
| Memory, rules, lists, orders | Google Sheets (Memory, ReviewRules, Wishlist, Cart, Orders) | `store.py` |
| Search + detail | Apify `sian.agency/myntra-product-scraper` (`queries`, `scrapeMode: detail`) | `tools/myntra.py` |
| Filters + review score + ask rule | Python, deterministic | `rules.py` |
| Stock recheck | Apify `khadinakbar/myntra-product-scraper` | `tools/myntra.py` |
| Cross-shop | Serper Shopping | `tools/serper.py` |
| Delhivery serviceability, Pine Labs createMandate + P3P check | Curtain, docs response | `curtain/*.json` |
| Delivery promise, return alerts, P3P via Pine checkout | Curtain, imagined | `curtain/*.json` |
| End of recording | Gmail, order emails only | `tools/gmail.py` |
| Voice | Gnani STT `/stt/v3`, TTS `/api/v1/tts/inference` | `tools/gnani.py` |

## Rules (in `rules.py`)
- Drop products that fail any of: size in stock, price ≤ budget, fabric not avoided, ≥30 ratings.
- Review score = 100 × (1 − weighted risk). Default weights: 1★ share 0.5, 2–3★ share 0.3, no-comment share 0.2. Edit them in the ReviewRules tab, or just tell Cartis.
- Ask a question only if some plausible answer leaves < 50% of the current top 3 (`check_question_value`).

## To do before recording
1. Paste the exact docs responses into `curtain/delhivery_serviceability.json` and `curtain/pine_labs_create_mandate.json`.
2. After the first live Apify run, check `data/raw/*.json` and tighten `normalize()` if any field comes out empty.
3. Google: download the OAuth client as `credentials.json`, run `python -m cartis.cli auth` with your own Google account (added as a test user), then `setup-sheet`.
