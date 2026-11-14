# Cartis: Agentic Shopping Assistant for Indian Shoppers

> **"Say what you need once. Get the right item, with the reasons."**
> Built for *The Ken's Great Rewiring Case Competition 2026*, Round 3 (Opening #11: *Finding the exact right thing*).
> Team: Arun Teja Kamisetti, Gudipati Sanjana Reddy, Naveena Kota (IIM Kozhikode)

---

## 1. Executive summary

Cartis (*cart + Jarvis*) is a **voice-first, buyer-side AI shopping agent**. A shopper speaks one request, e.g. *"brown formal shirt chahiye, Saturday tak"* ("I need a brown formal shirt by Saturday"). Cartis then autonomously **searches, filters, scores, shortlists and explains** the best options. It learns from what the shopper actually **keeps or returns**, not from what they tick in a form.

The v1 simulation runs end to end on **real data sources** using a **Wizard-of-Oz** method, as the competition requires:

- **Live:** voice, catalogue, ratings, email and memory.
- **Played by a team member (the "curtain"), using documented API responses:** Pine Labs and Delhivery.

---

## 2. Problem statement

Indian online fashion shopping has a **discovery problem, not a supply problem**:

- **Choice overload.** Hundreds of near-identical listings across apps, with star averages bunched between 4.2★ and 4.6★.
- **Misleading signals.** The star average hides *who* was unhappy. Most ratings carry no written comment at all.
- **Fit and return friction.** Shoppers buy the wrong size or fabric, return it, and start over. The platform forgets why.
- **Cold-start forms.** Apps ask users to fill in preference quizzes, and users skip them.

**Primary research:** user interviews (e.g. *Rupesh*, *Chatur*) showed that people **won't describe their taste, but they will describe their method.** For example: "I read the 2–3★ reviews; I ignore 5★ ratings with no comment."

---

## 3. Jobs-to-be-done (JTBD)

> *"When I need something specific by a deadline, help me find the one item that fits me and arrives on time, and show me why, so I can buy with confidence without scrolling for an hour."*

| Persona | Pain | Cartis job |
|---|---|---|
| Deadline shopper (Chatur, 24) | Wedding on Saturday, no time to compare | Filter by delivery date, shortlist 3 |
| Method shopper (Rupesh) | Distrusts star averages | Score reviews *his* way, transparently |
| Repeat buyer | Re-enters size and preferences every time | Learn size and fit from order history |

---

## 4. Value proposition

| For the shopper | For the ecosystem |
|---|---|
| One sentence in, three explained picks out | Higher **conversion** and lower **return rates** (fit-aware recommendations) |
| Reasons spoken aloud, evidence on screen | **Trust layer**: every number spoken is also displayed |
| Customisable review criteria | Structured demand signals for merchants |
| Zero-form onboarding (learns from Gmail) | Ready to run on agentic rails (UPI P3P, logistics APIs) |

**North Star Metric:** *Kept-item rate* = requests that ended in a kept item ÷ total requests.
**Guardrail metrics:** return rate, questions asked per request (target ≤ 1), time-to-shortlist.

---

## 5. Product principles

1. **Do, then ask.** Ask a question *only* if the answer changes the top-3 shortlist (overlap < 50%).
2. **Trust behaviour over stated preference.** Kept = strong signal. One return changes nothing; only a pattern does.
3. **Explainability by default.** Every card shows why it ranks where it does.
4. **The human holds the wallet.** A spoken "yes" never moves money; payment needs an explicit tap.
5. **Never fake data.** If a field doesn't exist (e.g. review photos), Cartis says so.
6. **Privacy-scoped.** Cartis reads **order emails only**, never personal mail.

---

## 6. Customer journey (end to end)

| # | Stage | What the shopper sees | What Cartis does | Rail / source |
|---|---|---|---|---|
| 0 | Onboard | Signs in with Google | Reads past order emails → sizes, brands, spend, returns | Gmail API |
| 1 | Ask | Taps the mic, speaks in Hinglish | Speech-to-text | **Gnani STT** |
| 2 | Understand | — | Extracts item, deadline, budget; loads memory | Claude + Google Sheets |
| 3 | Search | — | Live multi-shop search with rating breakdowns | Apify (Myntra) + Serper |
| 4 | Filter | Drop reasons shown | Size, budget, avoided fabrics, < 30 ratings, serviceability, arrival date | Rules engine + **Delhivery** |
| 5 | Clarify | One question (only if needed) | `check_question_value` (top-3 overlap test) | Rules engine |
| 6 | Shortlist | 3 cards + spoken reasons | Review risk score, voice explanation | **Gnani TTS** |
| 7 | Adjust | "No polyester", "cheaper" | Re-sorts; saves lasting rules | Sheets (Memory) |
| 8 | Buy | Taps Confirm | Stock recheck → address validation → payment flow | Apify + **Delhivery Maps** + **Pine Labs** |
| 9 | Learn | — | Reads the order confirmation; tracks kept or returned | Gmail API → Sheets (Orders) |

---

## 7. Solution architecture

```
 Shopper (voice / chat)                           Dashboard (web)
        │                                               ▲
        ▼                                               │ SSE stream
 ┌──────────────┐  STT   ┌────────────────────────────────────────┐
 │ Gnani Vachana│──────▶ │  Cartis agent (Claude, tool use)       │
 │  STT / TTS   │◀────── │  System prompt = numbered rules        │
 └──────────────┘  TTS   └────────────────────────────────────────┘
                              │          │           │          │
              ┌───────────────┘          │           │          └──────────────┐
              ▼                          ▼           ▼                         ▼
     Catalogue & reviews          Rules engine   Memory store              Rails (curtain)
     Apify: Myntra detail         rules.py       Google Sheets             Delhivery: serviceability,
     Apify: stock recheck         (deterministic)  Memory, ReviewRules,      Maps address APIs,
     Serper: Google Shopping                       Wishlist, Cart, Orders   delivery promise*
                                                                            Pine Labs: Grantex,
                                                  Gmail API                 createMandate, P3P*
                                                  (order emails only)       (* = imagined)
```

**Design choice:** the **LLM decides** (intent, which tool to call, what to ask, how to explain). **Deterministic Python enforces** the hard rules (filters, scoring, the ask threshold). That makes decisions auditable and testable.

---

## 8. Technology stack

| Layer | Technology |
|---|---|
| Agent / LLM | **Claude (Anthropic)**, `claude-sonnet-5-5`, with native tool calling |
| Backend | **Python, FastAPI**, Server-Sent Events (SSE) streaming, timeouts and heartbeats |
| Voice in (STT) | **Gnani.ai Vachana STT**: `POST https://api.vachana.ai/stt/v3` (clips ≤ 60s); 10 Indian languages, Hinglish via `hi-IN` |
| Voice out (TTS) | **Gnani.ai Vachana TTS**: `/api/v1/tts/inference`; speaks the top-3 reasons; barge-in (interruption) supported |
| Catalogue + reviews | **Apify** `sian.agency/myntra-product-scraper` (detail mode: `rating_breakdown`, `rating_count`, `reviews_count`, sizes, fabric, fit) |
| Stock recheck | **Apify** `khadinakbar/myntra-product-scraper` (per-size availability) |
| Cross-shop search | **Serper.dev** Google Shopping API (Ajio, Amazon.in, Flipkart, brand D2C sites) |
| Memory / state | **Google Sheets API**: tabs `Memory`, `ReviewRules`, `Wishlist`, `Cart`, `Orders` |
| Behavioural signals | **Gmail API** (OAuth, testing mode; order emails only) |
| Logistics rail | **Delhivery**: Pincode Serviceability API; Delhivery Maps `standardize_address`, `validate_address`, `verify_address`, `compute_distance_matrix` |
| Payments rail | **Pine Labs**: P3P (agentic UPI payments), **Grantex** delegated identity (`decidePayment`, scope `mpp:payment:initiate`), `createMandate` → `POST /mpp/v1/pre-authorize` (UPI ReservePay), mandate balance, capture |
| Hosting | **Render** (Blueprint `render.yaml`), passcode-gated demo |
| Testing | `pytest` unit and workflow tests + headless UI checks |

---

## 9. Review intelligence (the core differentiator)

Star averages hide risk. Cartis computes a **Review Risk Score** from the full rating distribution:

```
Review score = 100 × (1 − weighted risk)

weighted risk = 0.5 × (1★ share)          # how many were angry
              + 0.3 × (2–3★ share)        # honest "it's okay, but…" buyers
              + 0.2 × (no-comment share)  # ratings with no words behind them
```

*Example (live data, men's brown formal shirts):* every option averaged 4.2–4.6★, but the 1★ share ranged from **2.4% (Arrow)** to **~10% (Van Heusen IQ)**. A normal app shows these as equal; Cartis shows the difference.

Weights are **user-customisable** by voice ("at least 100 ratings", "1-stars matter most") and saved in the `ReviewRules` tab.

---

## 10. Wizard-of-Oz simulation design

| Category | Components | How it runs |
|---|---|---|
| **Live (real source)** | Gnani STT/TTS, Gmail, Apify, Serper, Google Sheets, Claude | Real API calls, shown in the trace |
| **Curtain (documented response)** | Delhivery serviceability and address APIs; Pine Labs Grantex, `createMandate`, capture | A team member pastes the exact documented response (`curtain/*.json`) |
| **Imagined capabilities (max 3)** | ① Delhivery **delivery promise** (buyer-side arrival date)  ② Delhivery **return alerts** to buyers  ③ Pine Labs **P3P at merchants already on Pine Labs checkout** | Documented as proposed endpoints, with the data each partner already holds |

**Rule followed:** the person behind the curtain only plays the world. They never help the agent think.

---

## 11. Cold-start strategy (three-layer memory)

| Layer | When | Source | Example |
|---|---|---|---|
| L1: knows for everyone | Day 0 | Team, from user research → system prompt | Review-reading method, "never ask up front" |
| L2: learns at sign-up | Minute 1 | Gmail order history | Size 40 at Arrow, usual spend ~₹1,500 |
| L3: learns while used | Each session | What the shopper says, picks, keeps | "No polyester", prefers regular fit |

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Gmail restricted-scope verification (CASA) needed at scale | Testing mode for the pilot; long-term: an order-data rail via ONDC / partner APIs |
| No buyer-side catalogue API in India | Apify + Serper for the simulation; ONDC shared catalogue as the production rail |
| Agent payments can't reach marketplaces yet | Hand-off to the shop's checkout; P3P where merchants support it |
| LLM hallucinating prices or numbers | Voice may only say numbers that are on the card; deterministic rules engine |
| Platform incentive conflict (buyer-side agent) | Position Cartis around return reduction, a shared win for merchants |

---

## 13. Roadmap

- **v1 (now):** voice + dashboard, Myntra + cross-shop search, review intelligence, Gmail learning, Pine Labs / Delhivery flows.
- **v2:** gifting with masked recipient preferences (consent via Grantex scopes), share-to-Cartis, photo input.
- **v3:** autonomous repeat buys within UPI mandate limits (P3P), proactive "back in stock" / price-drop alerts.

---

# Cartis v1: backend

Claude agent + tools, tested in the terminal. The dashboard calls `cartis/server.py`.

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
- Drop products that fail any of: size in stock, price ≤ budget, fabric not avoided, ≥ 30 ratings.
- Review score = 100 × (1 − weighted risk). Default weights: 1★ share 0.5, 2–3★ share 0.3, no-comment share 0.2. Edit them in the ReviewRules tab, or just tell Cartis.
- Ask a question only if some plausible answer leaves < 50% of the current top 3 (`check_question_value`).

## To do before recording
1. Paste the exact docs responses into `curtain/delhivery_serviceability.json` and `curtain/pine_labs_create_mandate.json`.
2. After the first live Apify run, check `data/raw/*.json` and tighten `normalize()` if any field comes out empty.
3. Google: download the OAuth client as `credentials.json`, run `python -m cartis.cli auth` with your own Google account (added as a test user), then `setup-sheet`.

---

## Security note
API keys live only in `.env` / Render environment variables and are **never committed**. Rotate all keys after the competition.

## Acknowledgements
Built on the rails provided by **Gnani.ai**, **Pine Labs** and **Delhivery** for The Ken's Great Rewiring Case Competition 2026.
