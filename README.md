# Flick Yeah

A swipe-style tool for finding profitable secondhand Sony Cyber-shot cameras
on eBay. It scans listings, scores each one for expected profit using a
transparent rule-based model, and presents them as cards to accept or reject
from a phone.

No LLM. The scoring is an explicit expected-value calculation over three
outcomes — works, needs a minor repair, dead — with probabilities driven by
what the seller's description actually says.

![tabs: Swipe, Auctions, Kept, History, Blocked, Setup]

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # add eBay Browse API credentials
python webapp.py          # http://localhost:8000
```

No credentials to hand? `python cli.py` runs the scoring against sample
listings and prints the reasoning.

## What it does

- Scans configurable eBay searches on a timer, within a self-enforced daily
  API budget
- Reads each listing's text for condition signals, bundles, and accessories
- Scores expected profit, ROI and confidence, with a written rationale
- Swipe deck with every seller photo, the full cleaned description, and a
  maximum sensible bid for auctions
- Records why you rejected something, and can permanently hide models you
  don't want — while still showing them inside job lots
- Checks a listing is still for sale before showing it, so you don't swipe
  right on something already sold
- Everything tunable from a Settings screen: search phrases, price range, scan
  frequency, API budget, per-model resale values

## Documentation

| | |
|---|---|
| `CLAUDE.md` | Start here — project rules and conventions |
| `docs/ARCHITECTURE.md` | How the pipeline and scoring model work |
| `docs/DEPLOYMENT.md` | The server, deploy process, API limits |
| `docs/DOMAIN-RULES.md` | The business, and what makes a listing good |
| `docs/GOTCHAS.md` | Bugs that already shipped once — read before changing signals or the schema |
| `docs/API.md` | HTTP endpoints |
| `docs/ROADMAP.md` | What's next |

## Status

Phase 1 (rule-based, no LLM) is complete and running in production.
