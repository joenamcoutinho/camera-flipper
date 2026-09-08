# Architecture

## The pipeline

```
eBay Browse API
      |  search_items()  - keyword search, paged, price-filtered
      v
  raw item summaries
      |  get_item_details() - one call per NEW listing: full description + all photos
      v
  to_internal_listing()  - normalise into the shape db.py expects
      |
      v
  signals.extract_features()   pure text analysis, no API, no model
      |   is it untested / working / broken?  damaged?  opened?
      |   is it a bundle?   is it an accessory rather than a camera?
      v
  scoring.score_listing()      cost -> outcome probabilities -> expected value
      |
      v
  SQLite: listings + decisions rows
      |
      v
  /api/next  - filtered by your Settings, availability-checked, served to the UI
      |
      v
  You swipe.  decisions.your_decision + reject_reasons + your_notes recorded.
```

Every stage is deterministic and inspectable. There is no model, no embedding,
no LLM. "AI score" in the UI means `scoring.py`'s formula.

## Scoring model

Three outcomes, each with a probability that depends on what the listing text
says, and a dedicated cost model:

| Outcome | Payoff |
|---|---|
| Works | resale price − total cost |
| Needs minor repair | resale − total cost − repair cost (default £8) |
| Dead | salvage (default £5) − total cost |

`total cost = purchase + battery + charger + shipping`. Expected value is the
probability-weighted sum. ROI is EV / total cost. The recommendation is BUY /
UNSURE / PASS off EV and ROI thresholds.

Base probabilities come from what the seller claims, in this priority order
(the order matters — getting it wrong caused a real bug):

| Priority | Case | works / minor / dead |
|---|---|---|
| 1 | **Confirmed broken** — "faulty", "doesn't power on", "spares or repairs" | 0.05 / 0.15 / 0.80 |
| 2 | **Confirmed working**, and not also flagged untested | 0.90 / 0.08 / 0.02 |
| 3 | **Untested with a benign reason** — "no battery", "loft find" | 0.55 / 0.30 / 0.15 |
| 4 | **Untested, unexplained** | 0.40 / 0.30 / 0.30 |
| 5 | **Nothing stated at all** | 0.35 / 0.30 / 0.35 |

Priority 1 must stay first. A listing that says it doesn't work must never be
read as working — that ordering bug once scored an obviously dead camera
("doesn't turn on, for parts, dropped, opened it up") as **BUY at 295% ROI**.

Nothing-stated is deliberately pessimistic: silence is not good news.

Then adjustments for physical damage, tampering (the biggest red flag per
Joe's own rules), and reseller-looking sellers.

`resale` comes from `camera_knowledge` for that model. **If the model has no
row, it falls back to the single `default_resale` setting.** That one number
therefore decides the fate of every unfamiliar model, which is why the
per-model resale editor in Settings is the highest-value thing to fill in.

## Settings resolution

`settings_now()` resolves each key as: **saved in database > `.env` > default
in `SETTING_DEFS`**. Adding an entry to `SETTING_DEFS` in `webapp.py` is all
that's needed — the Settings screen renders itself from `/api/settings`, so no
front-end change is required for a new setting.

The scanner re-reads settings at the top of every run and the background loop
re-reads its interval between runs, so changes take effect without a restart.

## Database

SQLite, WAL mode, 15s busy timeout (the background scanner and a manual "scan
now" can overlap — without this they collided and threw "database is locked").

Core tables:

- `listings` — one row per eBay item ever seen. Includes `extracted_features`
  (JSON), `is_bundle`, `is_accessory`, `listing_status` (active/ended),
  `checked_at`.
- `decisions` — the AI's score and your swipe. `reject_reasons` (JSON list),
  `your_notes`. One row per listing.
- `camera_knowledge` — resale range, battery/charger cost, demand score per model.
- `blocked_models` — models you never want to see, with `allow_in_bundle`.
- `settings` — key/value, everything editable from the app.
- `api_usage` — calls per day, so restarting the service can't reset the budget.
- `personal_rules`, `historical_outcomes` — derived from Joe's old spreadsheet.
- `purchases`, `inventory`, `vinted_listings`, `sales` — the post-purchase
  pipeline. **Defined but not yet used by any code.** They exist for Phase 3,
  when logged outcomes become training data.

`decisions` + `listings` together are the eventual training set: listing text,
the algorithm's call, Joe's call, and his stated reason for disagreeing.

## Front end

`static/index.html` — one file, vanilla JS, no build step, no libraries.
Six tabs: Swipe, Auctions, Kept, History, Blocked, Setup.

The swipe card: photo (collapses as you scroll so the description gets nearly
the full screen), tappable thumbnail strip of every seller photo, title, price,
metrics, full description, scoring rationale, eBay link. Horizontal-dominant
drags swipe; vertical drags scroll — decided once per gesture so the two never
fight.

Swiping left opens a non-blocking sheet with reason chips and a "never show
this model again" switch. Nothing there is ever required.
