# HTTP API

All routes sit behind HTTP Basic Auth when `WEBAPP_USERNAME` and
`WEBAPP_PASSWORD` are set. Leave them blank locally and auth is skipped.

Everything the front end does goes through these — `static/index.html` has no
other data source.

## Swiping

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/next` | Next card, filtered by Settings. Verifies the listing is still for sale if it's stale. Returns `{card, remaining, skipped_sold}` |
| POST | `/api/decisions/{id}/decide` | Record a swipe. Body: `{decision, notes?, reasons?, block_model?, block_everywhere?}` |
| POST | `/api/undo` | Clear the most recent swipe so the card returns to the queue |

`decision` is `BUY` / `PASS` / `UNSURE`. `reasons` is a list of quick-tap reason
tags. `block_model` adds the card's model to the blocked list;
`block_everywhere` makes that block apply inside bundles too.

## Lists

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/history?only=BUY&limit=100` | Past decisions, newest first. `only` filters by your decision |
| GET | `/api/auctions` | `{wishlist, ending_soon}` — auctions you kept, and undecided ones ending within 2 days. Soonest first |
| GET | `/api/stats` | Counts, agreement rate, most passed/kept models, API usage |

## Blocked models

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/blocked` | Blocked models with how many listings each is currently hiding |
| POST | `/api/blocked` | `{model, reason?, allow_in_bundle?}` |
| DELETE | `/api/blocked/{model}` | Unblock. Its listings reappear immediately — no re-scan needed |
| PATCH | `/api/blocked/{model}` | `{allow_in_bundle}` — toggle the bundle exception |

## Settings and camera values

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/settings` | `{values, defs}` — the Settings screen renders itself from `defs` |
| POST | `/api/settings` | `{values: {...}}`. Unknown keys ignored |
| GET | `/api/cameras` | Every model seen in listings, with its resale range if set |
| POST | `/api/cameras` | `{model, resale_low, resale_high}` |

## Maintenance

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/scan-now` | Scan immediately. Returns `{new, failed, errors, api_calls_today, budget}` |
| POST | `/api/rescore` | Re-analyse stored titles and re-score the queue. No eBay calls. Returns `{rescored, accessories_found, bundles_found}` |
| POST | `/api/refresh-details` | Re-fetch photos and descriptions for pending listings. **Costs one eBay call per listing** |
| GET | `/api/lookup?q=DSC-W120` | "Why isn't this showing up?" — every matching listing with `in_queue` and `hidden_because` |

`/api/lookup` is the debugging tool. It answers with the actual reason a
listing isn't visible: already swiped, model blocked, listing ended, flagged as
an accessory, scored PASS with algorithm-passes hidden, or below the minimum
expected profit. No matches at all means it was never scanned — no search
phrase matched, or it fell outside the price range.

## Settings keys

`search_queries`, `max_price`, `min_price`, `pages_per_query`, `poll_minutes`,
`max_new_per_scan`, `daily_call_budget`, `default_resale`, `hide_accessories`,
`verify_before_showing`, `verify_stale_hours`, `include_ai_pass`,
`min_expected_profit`.

Defined in `SETTING_DEFS` in `webapp.py`, each with a type (`lines`, `number`,
`bool`), a label and help text. Adding one there makes it appear in the app
automatically — no front-end change needed.
