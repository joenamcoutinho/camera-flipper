# Flick Yeah — Camera Flipping AI

Context for Claude Code. Read this first; the `docs/` folder has the detail.

## What this is

A personal tool for Joe's side business: buying cheap, often untested Sony
Cyber-shot compact cameras on eBay, testing/repairing them, and reselling on
Vinted. The app scans eBay, scores each listing for expected profit, and
presents them as a Tinder-style swipe deck ("Flick Yeah") so he can approve or
reject each one on his phone.

It is **live in production** on a free Oracle Cloud VM at
`http://145.241.226.235:8000` (HTTP Basic Auth). It runs as a systemd service
and scans eBay automatically every 30 minutes.

## Golden rules — do not break these

1. **Phase 1 is algorithm-only. No LLM anywhere in this codebase.** Not an API,
   not a local model. This is Joe's explicit decision: get the rule-based
   system fully working first. A local LLM on his RTX 3080 (llama.cpp) is
   Phase 2, and only starts when he says so.
2. **Never buy, bid, or list automatically.** The app recommends; Joe decides.
   No code path may spend money or post publicly.
3. **Vinted has no public API.** Any automation of Vinted breaches their ToS.
   Listings there are created by hand. Do not build a Vinted poster.
4. **Respect the eBay API budget.** 5,000 calls/day (hard eBay limit). The app
   self-caps at a configurable budget (default 4,000) and stops scanning
   rather than getting cut off mid-day. Before changing scan frequency,
   pages-per-query, or adding per-listing calls, do the arithmetic — this has
   already been got wrong once (see docs/GOTCHAS.md).
5. **Settings live in the database, not in code.** Anything tunable belongs in
   `SETTING_DEFS` in `webapp.py` so it appears in the app's Settings screen.
   Joe should never have to edit code or redeploy to change behaviour.

## Repo layout

```
webapp.py            FastAPI app: scanning loop, all HTTP endpoints, settings layer
db.py                SQLite wrapper. Plain SQL, no ORM. Migrations live here.
schema.sql           Table definitions (CREATE TABLE IF NOT EXISTS only)
signals.py           Rule-based text analysis: condition, bundle, accessory detection
scoring.py           Cost -> outcome probabilities -> expected value -> BUY/UNSURE/PASS
cli.py               Model-name detection + a sample-data demo runner
camera_knowledge.py  Seed resale figures per model, seeded on first startup
ebay_client.py       eBay Browse API client (search, item detail, availability)
rescore.py           Re-analyse + re-score the queue from stored text (no API calls)
telegram_bot.py      LEGACY. Superseded by the web app. Kept working, not developed.
static/index.html    The entire front end: one self-contained file, vanilla JS
update_env.py        Patches non-secret settings in .env on the VM during deploy
deploy_finish.ps1    Windows deploy script (scp -> restart service -> rescore)
historical_data/     Joe's old resale spreadsheet + its importer
sample_data/         Fake listings so cli.py runs with no credentials
```

## Running it

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in eBay keys; leave WEBAPP_* blank locally
python webapp.py              # -> http://localhost:8000
python cli.py                 # scoring demo on sample data, no credentials needed
```

Leaving `WEBAPP_USERNAME`/`WEBAPP_PASSWORD` blank disables auth — intended for
local work only. They must be set on the VM.

## Deploying

Joe double-clicks `DEPLOY - double click me.bat` on Windows. It scp's the code
to the VM, patches settings, installs deps, restarts the systemd service, and
re-scores the queue. Output goes to `deploy_log.txt`. Details and the manual
equivalent are in `docs/DEPLOYMENT.md`.

**The VM runs Python 3.9.** Do not use 3.10+ syntax or stdlib arguments. This
has broken deploys twice: `str | None` union types, and `Path.write_text(newline=)`.

## Conventions

- Plain SQL and plain dicts. No ORM, no dataclass layers over the database.
- Comments explain **why**, especially where something non-obvious prevents a
  bug that actually happened. Keep those comments — they are load-bearing.
- New database columns go in the `MIGRATIONS` list in `db.py`, never only in
  `schema.sql`. `CREATE TABLE IF NOT EXISTS` does nothing to an existing table,
  so a schema-only change never reaches the live database.
- The front end is one file with no build step and no dependencies. Keep it
  that way. Escape all eBay-supplied text with `esc()` before inserting it.
- Test before shipping. There are throwaway test scripts in the conversation
  history; write similar ones. Deliberately feed in malformed listings,
  failing API calls, and negated phrases ("not in working condition").

## Current state (as of 2026-09-08)

Working and deployed: scanning, scoring, swipe UI with photo strip and full
descriptions, reject reasons, per-model blocking (with a bundle exception),
undo, history, kept list, auctions tab with countdowns, blocked-model manager,
settings screen, per-model resale editor, a "why isn't this showing up?"
diagnostic, API budget tracking, sold-listing checks, and accessory filtering.

Database holds ~1,000 scanned listings and Joe's swipe decisions.

## What to work on next

See `docs/ROADMAP.md`. The highest-value near-term work is filling in real
resale values per model (the single biggest lever on scoring quality), then
seller analysis. Phase 2 (local LLM) only on Joe's say-so.

## Working with Joe

He is an ML engineer, so technical depth is fine, but he strongly prefers
doing over reading: give him concrete commands and changes rather than long
explanations or reading lists. He iterates fast and tests on his phone.
