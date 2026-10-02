# Cartis: test cases

Run from `Documents\cartis`:
```
python -m pytest -q tests                              # automated: 30 tests, about 1 second, never touches real services
python -m uvicorn cartis.server:app --port 8000        # then open http://localhost:8000 and press Ctrl+Shift+R
```
Before each manual run, click **Reset** at the top right (profile and rules go back to defaults; orders are kept).

## A. What I already ran (2 Oct, sample product data + live Claude)
| # | Workflow | Result |
|---|---|---|
| Unit + workflow | 30 tests: rules, light listings, sorting, estimates, address, Grantex allow/deny, mandate balance, P3P names, wishlist, history import, search failure isolation, real receipt email, address + payments endpoints | 30/30 pass |
| W1 | Multi-shop search: Myntra + Fabindia, AJIO, Amazon, Flipkart on one screen; all passing cards shown; every card links to its shop | Pass |
| W2 | "Arriving early matters more than reviews" → re-sorted by arrival | Pass |
| W3 | ♡ on a card → appears in the Wishlist panel and the Sheet | Pass |
| W4 | Hinglish: "mujhe sabse sasta wala dikhao" → sorted by price, reply in Hinglish | Pass |
| W5 | Buy → Cartis asks for address → Delhivery validates it → Pay with Pine Labs → Grantex ALLOW → mandate → getMandateBalance ACTIVE → PROCESSED → order email read by itself | Pass |
| UI (2 Oct) | 27 headless checks at 1440×900, 1366×640 and phone 390×844: side panel zones never overlap; Wishlist / Address / Payments buttons at the top; wishlist add → badge → sheet → remove; address INCOMPLETE → VALID from the sheet; Pay from the Payments sheet → checkout → order card; phone has no sideways scroll, 2-column cards, Hood opens as a full screen | 27/27 pass |

## B. Manual test cases (please run these on your laptop)
Tick each one. "Hood" = the *Under the hood* panel on the right.

### 1. Setup and live mode
| ID | Do | Expect |
|---|---|---|
| 1.1 | Open the page | The *For demo purposes* footer (bottom of the right panel) shows a green dot for Myntra, Other shops, Voice, Gmail + Sheets, and **Order email · real email**. It never overlaps *Under the hood* |
| 1.2 | Look at *What Cartis knows* | Your name, budget and pincode, read from the Sheet |

### 2. Learns you without a form (Round 2 S0, cap 21)
| ID | Do | Expect |
|---|---|---|
| 2.1 | Type: *I need a white cotton kurta under 1500* | Hood shows `import_order_history` (first time only); a toast reads "Learned from N order emails"; the panel shows Top size **M**, Waist **32**, Orders read, Usual spend, Brands, Buys |
| 2.2 | Ask again | Cartis does not read your emails a second time |

### 3. Search beyond Myntra (cap 22) + browse to the shop page
| ID | Do | Expect |
|---|---|---|
| 3.1 | Same request | Hood shows `search_shops` **live**; a toast reads "Found N: X on Myntra + Y from Z other shops" |
| 3.2 | Look at the cards | Shop chips besides Myntra (Amazon.in, AJIO, Flipkart…); the header says "All N that passed · from …" |
| 3.3 | Count the cards | Every passing product is shown (the number matches "N of M made it"); scrolling shows them all |
| 3.4 | Click **View on … ↗** on any card | That product's real page opens on Myntra, Amazon or the other shop, in a new tab |
| 3.5 | Look at a non-Myntra card | "★ 4.x average · rating only" and "Size not listed by <shop>: check it on their page" |
| 3.6 | Look at a Myntra card | 1★ %, **five-star count**, 2–3★ count, "N of M with no words" |

### 4. Rules and filters before showing (S5, caps 9, 11, 12)
| ID | Do | Expect |
|---|---|---|
| 4.1 | *…for a wedding in three days* | A deadline chip "arrives by …"; drops include "can't arrive in time · N" |
| 4.2 | Look at the dates | "Arrives Sat 3 Oct" = Delhivery promise; "Est. 4 – 6 Oct" = estimate (hover: *estimate, not a promise*); "Date unknown" sits last |
| 4.3 | Hood | `rank_products` · rules |

### 5. Voice-screen sync and voice in/out (caps 1–4)
| ID | Do | Expect |
|---|---|---|
| 5.1 | Listen to the reply | You **hear** the reply. If Gnani fails: a toast + a `gnani tts · error` row in the Hood, and the browser voice speaks instead. Send me that error text |
| 5.2 | Watch while it talks | The phrase being spoken highlights in the caption and the matching card element glows amber |
| 5.3 | Tap **Speak**, say *"Which one arrives first?"*, tap again | It transcribes and answers |
| 5.4 | Tap Speak while Cartis is talking | Cartis stops at once (barge-in) |
| 5.5 | Switch the language to **हिन्दी / Hinglish**, say *"sabse sasta wala dikhao"* | List re-sorted by price; reply in Hinglish |

### 6. Priorities and questions (S6, S8, cap 23)
| ID | Do | Expect |
|---|---|---|
| 6.1 | *Arriving early matters more than reviews* | Chip "sorted by arrival"; the order changes if needed |
| 6.2 | *Make the one-star weight 0.6* | Panel review rule shows 1★ ×0.6; scores update |
| 6.3 | *No polyester ever* | Saved in the panel (Avoids) |

### 7. Wishlist and history (Q1 / S2)
| ID | Do | Expect |
|---|---|---|
| 7.1 | Click ♡ on card 3 | Heart turns pink; toast "Saved to wishlist"; the **♥ Wishlist** button at the top shows a count; Hood `wishlist add` |
| 7.2 | Ask *What's on my wishlist?* | Cartis names it |
| 7.3 | Card from a brand you've bought | Green "You've bought <brand>" tag |
| 7.4 | Open **♥ Wishlist** at the top → **Remove** | Gone from the list, the count drops, and the card's heart empties |

### 8. Buy: address → Pine Labs (caps 5, 6–8, 15–17)
| ID | Do | Expect |
|---|---|---|
| 8.1 | *Let's buy the first one* | Cartis asks you to **say your address** (first time only) |
| 8.2 | Say or type an address **without a pincode** | Hood `check_address`; panel shows **INCOMPLETE** "missing: pincode"; Cartis asks only for the pincode |
| 8.3 | Give the pincode | **VALID**, "Delhivery has delivered here"; the address is saved in the panel |
| 8.4 | Say *yes* out loud | Nothing is paid (a spoken yes never pays) |
| 8.5 | Tap **Pay ₹X with Pine Labs** | Checkout opens (labelled *sandbox · demo*): UPI ReservePay or Card |
| 8.6 | Tap **Authorise** | Steps tick: Grantex → mandate → UPI approval → getMandateBalance → capture → **Payment successful**; receipt shows Agent check ALLOW, Mandate status ACTIVE, PROCESSED |
| 8.7 | After the receipt | Without you saying anything, Cartis sends a **real email** to your Gmail, reads it back and confirms the order ID + amount. The order card says "Real email sent to <you>". Check your inbox for *Cartis · Order confirmed* |
| 8.8 | Hood | Grantex decidePayment · docs, createMandate · docs, getMandateBalance · docs, payment capture · simulated, check_order_email · simulated |
| 8.9 | Panel | Orders: new order · *watching* |

### 9. Top buttons and phone
| ID | Do | Expect |
|---|---|---|
| 9.1 | Look at the top bar | ♥ Wishlist, 📍 Address, ▭ Payments, then Voice and Reset. Address has an amber dot until an address is saved |
| 9.2 | **Address** → fill without pincode → *Check with Delhivery & save* | INCOMPLETE, the pincode box turns amber; add it → VALID + "Delhivery has delivered here"; the panel shows it |
| 9.3 | After Cartis puts up a Pay button, open **Payments** | "Waiting for you" with **Pay ₹X**; tapping it opens the Pine Labs checkout. After paying, it's listed under "Paid through Cartis" with ALLOW + ACTIVE + PROCESSED |
| 9.4 | Open the page on your phone (same Wi-Fi: `http://<laptop-ip>:8000`, start uvicorn with `--host 0.0.0.0`) | Icon-only top bar, 2 cards per row (typing works; the mic needs https, so test voice on the Render link), no sideways scrolling; the ≡ button opens *What Cartis knows / Under the hood / For demo purposes* full screen; the sheets slide up from the bottom |

### 10. Things that should go wrong gracefully (Q3 unhappy flow)
| ID | Do | Expect |
|---|---|---|
| 10.1 | *I need it by tomorrow* | Nothing in time → Cartis says so and offers the "if you can wait" options; it never loosens a limit silently |
| 10.2 | Ask for something absurd (*a gold kurta under ₹100*) | Says plainly nothing passed; no invented products |
| 10.3 | Buy from a shop not on Pine Labs (e.g. Meesho, if one shows) | Cartis says it can't take Pine Labs and points to **View on shop** |
| 10.4 | Turn off Wi-Fi and send a message | "Couldn't reach Cartis"; nothing crashes |

## C. Known limits (say these if judges ask)
- Other shops come from Google Shopping, so they have no size list or star breakdown. Their scores are "rating only".
- Delhivery promise, return alerts and P3P via Pine Labs checkout are **imagined** capabilities. Address, distance, mandate, balance and Grantex responses are **docs-shaped**. Payment capture and the order email are **simulated**. The Hood labels each one.
- Gifting, the share sheet and photo input are designed in Round 2 but outside this simulation.
