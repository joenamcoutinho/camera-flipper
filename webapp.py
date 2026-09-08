"""
Phase 1 web app - the swipe interface. Same DB, same scoring.py formula,
same camera_knowledge as the CLI and the (now legacy) Telegram bot.
Still no LLM anywhere in here.

    pip install -r requirements.txt
    python webapp.py
    -> open http://localhost:8000

On the VM this runs as a systemd service (camera-flipper.service), so it
starts on boot and restarts itself if it dies:
    sudo systemctl restart camera-flipper
    journalctl -u camera-flipper -f      # live logs
"""
import json
import os
import secrets
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # before importing ebay_client - see the note in ebay_client.py

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List

from datetime import datetime, timezone

from db import (
    get_conn, init_db, save_listing, save_decision, listing_seen,
    get_api_calls, set_api_calls,
    get_camera_knowledge, record_your_decision, add_decision_note,
    set_reject_reasons, block_model, unblock_model, set_bundle_exception,
    list_blocked_models, decision_history, last_decided, undo_decision,
    update_listing_details, pending_listing_ids,
    get_settings, set_settings,
    set_camera_resale, models_seen,
    mark_listing_status, touch_checked,
)
from signals import extract_features
from scoring import score_listing, max_bid
from cli import guess_seller_type, guess_camera_model, all_models_in_title
import ebay_client

# --- settings ------------------------------------------------------------
#
# Everything here is editable from the Settings screen at runtime. The
# resolution order is: what you saved in the app  >  .env  >  the default
# below. Nothing needs a redeploy or a restart; the scanner re-reads these
# at the top of every run.

SETTING_DEFS = {
    "search_queries": {
        "type": "lines", "env": "SEARCH_QUERIES",
        "default": "sony cybershot\nsony cyber-shot camera\nsony dsc camera",
        "label": "Search phrases",
        "help": "One eBay search per line. More phrases = more listings, but each one costs API calls.",
    },
    "max_price": {
        "type": "number", "env": "EBAY_MAX_PRICE", "default": "60",
        "label": "Maximum price (£)",
        "help": "Listings above this are never fetched. Job lots usually need £100+.",
    },
    "min_price": {
        "type": "number", "env": "EBAY_MIN_PRICE", "default": "0",
        "label": "Minimum price (£)",
        "help": "Filters out £1 junk listings. 0 = no minimum.",
    },
    "pages_per_query": {
        "type": "number", "env": "EBAY_PAGES_PER_QUERY", "default": "3",
        "label": "Result pages per search",
        "help": "100 listings per page. More pages = deeper search, more API calls.",
    },
    "poll_minutes": {
        "type": "number", "env": "POLL_INTERVAL_MINUTES", "default": "30",
        "label": "Scan every (minutes)",
        "help": "How often to check eBay automatically. Lower = fresher, but uses the daily quota faster.",
    },
    "max_new_per_scan": {
        "type": "number", "env": "MAX_NEW_PER_SCAN", "default": "40",
        "label": "Max new listings per scan",
        "help": "Each new listing costs one extra API call for its photos. Spreads a big sweep over several scans.",
    },
    "daily_call_budget": {
        "type": "number", "env": "EBAY_DAILY_BUDGET", "default": "4000",
        "label": "Daily eBay API budget",
        "help": "eBay's hard limit is 5000/day. Scanning stops at this number so you never get cut off mid-day.",
    },
    "default_resale": {
        "type": "number", "env": None, "default": "40",
        "label": "Assumed resale for unknown models (£)",
        "help": "Used when a model has no resale figures below. This single number decides "
                "whether unfamiliar models look profitable - if good listings keep scoring PASS, raise it.",
    },
    "hide_accessories": {
        "type": "bool", "env": None, "default": "1",
        "label": "Hide accessory-only listings",
        "help": "Chargers, batteries, cases and screen protectors that merely mention a camera model. "
                "A camera whose description says 'comes with screen protector' is NOT affected.",
    },
    "verify_before_showing": {
        "type": "bool", "env": None, "default": "1",
        "label": "Check it's still for sale before showing it",
        "help": "Re-checks with eBay before putting an older card in front of you, so you don't "
                "swipe right on something already sold. Costs one API call per stale card.",
    },
    "verify_stale_hours": {
        "type": "number", "env": None, "default": "3",
        "label": "Only re-check cards older than (hours)",
        "help": "Freshly scanned listings are skipped - no point checking something found minutes ago.",
    },
    "include_ai_pass": {
        "type": "bool", "env": None, "default": "0",
        "label": "Show listings the algorithm would pass",
        "help": "Off = you only see BUY and UNSURE cards. On = you see everything found.",
    },
    "min_expected_profit": {
        "type": "number", "env": None, "default": "-999",
        "label": "Hide cards below expected profit (£)",
        "help": "-999 shows everything. Set to 0 to only see listings expected to make money.",
    },
    "theme_light": {
        "type": "bool", "env": None, "default": "0",
        "label": "Light theme",
        "help": "Off = dark theme, On = light theme.",
    },
}


def settings_now() -> dict:
    """Current effective settings: saved value > .env > default."""
    conn = get_conn()
    try:
        saved = get_settings(conn)
    finally:
        conn.close()
    out = {}
    for key, spec in SETTING_DEFS.items():
        if key in saved and saved[key] != "":
            out[key] = saved[key]
        elif spec["env"] and os.environ.get(spec["env"]):
            raw = os.environ[spec["env"]]
            # .env keeps search phrases comma-separated; the UI uses one per line
            out[key] = raw.replace(",", "\n") if spec["type"] == "lines" else raw
        else:
            out[key] = spec["default"]
    return out


def _num(settings, key, cast=float):
    try:
        return cast(float(settings.get(key, SETTING_DEFS[key]["default"])))
    except (TypeError, ValueError):
        return cast(float(SETTING_DEFS[key]["default"]))


def _queries(settings) -> list:
    raw = settings.get("search_queries", "")
    parts = [p.strip() for chunk in raw.split("\n") for p in chunk.split(",")]
    return [p for p in parts if p]
_api = {"day": None, "count": 0}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")  # eBay resets at midnight UTC


def _note_api_call(kind: str):
    day = _today()
    if _api["day"] != day:
        _api["day"], _api["count"] = day, 0
    _api["count"] += 1


def _load_api_usage():
    """Pick today's count back up after a restart, so restarting the
    service isn't a way to accidentally reset the budget."""
    conn = get_conn()
    _api["day"] = _today()
    _api["count"] = get_api_calls(conn, _api["day"])
    conn.close()


def _flush_api_usage():
    conn = get_conn()
    set_api_calls(conn, _api["day"] or _today(), _api["count"])
    conn.commit()
    conn.close()


def api_budget_left(budget: int) -> int:
    if _api["day"] != _today():
        return budget
    return max(0, budget - _api["count"])


ebay_client.set_call_hook(_note_api_call)

app = FastAPI()

# Password-protect everything once this is reachable from the internet.
# Locally, if you never set these two in .env, auth is skipped entirely -
# no friction for running it on your own PC.
WEBAPP_USERNAME = os.environ.get("WEBAPP_USERNAME")
WEBAPP_PASSWORD = os.environ.get("WEBAPP_PASSWORD")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if not WEBAPP_USERNAME or not WEBAPP_PASSWORD:
        return await call_next(request)  # auth not configured - local use

    header = request.headers.get("authorization", "")
    ok = False
    if header.startswith("Basic "):
        import base64
        try:
            user, pwd = base64.b64decode(header[6:]).decode().split(":", 1)
            ok = secrets.compare_digest(user, WEBAPP_USERNAME) and secrets.compare_digest(pwd, WEBAPP_PASSWORD)
        except Exception:
            ok = False
    if not ok:
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="camera-flipper"'})
    return await call_next(request)


# --- scanning -------------------------------------------------------------

def scan_once() -> int:
    """Search, score and store anything new. Blocked models are still
    scanned and saved - they're filtered at display time instead, so that
    unblocking a model brings its listings back rather than needing a
    re-scan, and so the data stays complete for later analysis."""
    st = settings_now()
    budget = _num(st, "daily_call_budget", int)
    max_new = _num(st, "max_new_per_scan", int)
    max_price = _num(st, "max_price")
    min_price = _num(st, "min_price")
    pages = _num(st, "pages_per_query", int)
    queries = _queries(st)
    default_resale = _num(st, "default_resale")

    _load_api_usage()
    if api_budget_left(budget) <= 0:
        print(f"Skipping scan: today's eBay call budget ({budget}) is used up.")
        return {"new": 0, "failed": 0, "errors": ["eBay daily call budget used up - resets midnight UTC"],
                "api_calls_today": _api["count"], "budget": budget}

    conn = get_conn()
    new_count, failed, errors = 0, 0, []
    for query in queries:
        if new_count >= max_new or api_budget_left(budget) <= 0:
            break
        try:
            items = ebay_client.search_items(
                query, price_max=max_price, price_min=min_price, max_pages=pages
            )
        except Exception as e:
            msg = f"Search failed for '{query}': {e}"
            print(msg)
            errors.append(msg[:300])
            continue

        for item in items:
            if new_count >= max_new or api_budget_left(budget) <= 0:
                break
            # One malformed listing must not abort the whole scan - that's
            # what turned a single bad item into a 500 on /api/scan-now.
            try:
                listing = ebay_client.to_internal_listing(item)
                if listing_seen(conn, listing["ebay_item_id"]):
                    continue
                try:
                    # one detail call gets both the real description and every
                    # photo the seller uploaded (search only returns a thumbnail)
                    detail = ebay_client.get_item_details(listing["ebay_item_id"])
                    if detail.get("description"):
                        listing["description"] = detail["description"]
                    if detail.get("images"):
                        listing["image_urls"] = detail["images"]
                except Exception:
                    pass  # fall back to the search summary we already have

                models_in_title = all_models_in_title(listing["title"])
                features = extract_features(
                    listing["title"], listing["description"],
                    distinct_models=len(models_in_title),
                    price=listing.get("price"),
                )
                seller_type = guess_seller_type(listing)
                model = guess_camera_model(listing["title"])
                camera = get_camera_knowledge(conn, model) if model else None
                result = score_listing(listing["price"], camera, features, seller_type,
                                       default_resale=default_resale)
                save_listing(conn, {**listing, "camera_model_guess": model}, features)
                save_decision(conn, listing["ebay_item_id"], result)
                new_count += 1
            except Exception as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(f"{item.get('itemId', '?')}: {type(e).__name__}: {e}"[:300])

    conn.commit()
    conn.close()
    _flush_api_usage()
    if failed:
        print(f"Scan: {new_count} added, {failed} listing(s) failed. First errors: {errors[:3]}")
    return {"new": new_count, "failed": failed, "errors": errors,
            "api_calls_today": _api["count"], "budget": budget}


def background_scanner():
    while True:
        try:
            r = scan_once()
            if r["new"] or r["failed"]:
                print(f"Scan: {r['new']} new, {r['failed']} failed, "
                      f"{r['api_calls_today']}/{r['budget']} eBay calls used today.")
        except Exception as e:
            print("Scan failed:", e)
        # re-read each cycle so changing it in Settings takes effect
        # without restarting the service
        try:
            minutes = _num(settings_now(), "poll_minutes", int)
        except Exception:
            minutes = 30
        time.sleep(max(5, minutes) * 60)


# --- request models -------------------------------------------------------

class Decision(BaseModel):
    decision: str                              # 'BUY' | 'PASS' | 'UNSURE'
    notes: Optional[str] = None
    reasons: Optional[List[str]] = None        # quick-tap reason tags
    block_model: Optional[bool] = False        # "never show me this model again"
    block_everywhere: Optional[bool] = False   # block even inside bundles


class BlockRequest(BaseModel):
    model: str
    reason: Optional[str] = None
    allow_in_bundle: Optional[bool] = True


class BundleException(BaseModel):
    allow_in_bundle: bool


# --- the swipe queue ------------------------------------------------------

CARD_COLUMNS = """
    d.id as decision_id, d.ai_score, d.ai_roi, d.ai_confidence,
    d.ai_recommendation, d.reasoning,
    l.ebay_item_id, l.ebay_url, l.title, l.description, l.price, l.listing_type,
    l.current_bid, l.end_time, l.camera_model_guess, l.image_urls,
    l.is_bundle, l.extracted_features, l.seller_username
"""

# Hide a blocked model unless this particular listing is a bundle and the
# block was set to allow bundles - a model you don't want on its own can
# still be worth having as part of a job lot.
NOT_BLOCKED = """
    NOT EXISTS (
        SELECT 1 FROM blocked_models b
        WHERE b.model = l.camera_model_guess
          AND (b.allow_in_bundle = 0 OR COALESCE(l.is_bundle, 0) = 0)
    )
"""


def _hydrate_card(conn, row) -> dict:
    card = dict(row)
    images = json.loads(card.get("image_urls") or "[]")
    card["images"] = images
    card["image_url"] = images[0] if images else None

    features = json.loads(card.get("extracted_features") or "{}")
    card["features"] = features
    card["bundle_signals"] = features.get("bundle_signals", [])

    # Auctions: what's the most it's worth bidding? Only meaningful when we
    # actually know this model's resale numbers.
    card["max_bid"] = None
    if card.get("listing_type") == "AUCTION" and card.get("camera_model_guess"):
        camera = get_camera_knowledge(conn, card["camera_model_guess"])
        if camera:
            try:
                card["max_bid"] = round(
                    max_bid(camera, features, "unknown",
                            default_resale=_num(settings_now(), "default_resale")), 2
                )
            except Exception:
                pass

    card["model_blocked"] = False
    if card.get("camera_model_guess"):
        blocked = conn.execute(
            "SELECT 1 FROM blocked_models WHERE model = ?",
            (card["camera_model_guess"],),
        ).fetchone()
        card["model_blocked"] = blocked is not None

    card.pop("extracted_features", None)
    card.pop("image_urls", None)
    return card


def _queue_filter():
    """The WHERE clause fragment for what's eligible to be swiped, built
    from your Settings. Kept in one place so the card, the count and the
    diagnostics all agree on what 'in the queue' means."""
    st = settings_now()
    clauses = [
        "d.your_decision IS NULL",
        NOT_BLOCKED,
        # a listing we've since found to be sold/ended is never offered again
        "COALESCE(l.listing_status, 'active') = 'active'",
    ]
    if st.get("hide_accessories", "1") in ("1", "true", "True"):
        clauses.append("COALESCE(l.is_accessory, 0) = 0")
    if st.get("include_ai_pass", "0") not in ("1", "true", "True"):
        clauses.append("d.ai_recommendation != 'PASS'")
    min_ev = _num(st, "min_expected_profit")
    params = []
    if min_ev > -900:
        clauses.append("d.ai_score >= ?")
        params.append(min_ev)
    return " AND ".join(clauses), params


# How many stale cards one request will re-check before giving up and
# showing what it has. Without a cap, a queue full of sold listings could
# fire dozens of eBay calls on a single tap.
MAX_CHECKS_PER_REQUEST = 5


@app.get("/api/next")
def next_card():
    """The next card to swipe on, filtered by your Settings.

    Listings are scanned at most twice an hour, so by the time you look at
    one it may already have sold - and swiping right on a sold listing is
    wasted effort. Before serving a card that was last checked a while ago,
    this asks eBay whether it's still buyable and quietly skips it if not.
    Fresh cards are served as-is, and the whole check can be turned off in
    Settings if you'd rather save the API calls.
    """
    st = settings_now()
    verify = st.get("verify_before_showing", "1") in ("1", "true", "True")
    stale_hours = _num(st, "verify_stale_hours")
    budget = _num(st, "daily_call_budget", int)

    where, params = _queue_filter()
    conn = get_conn()

    row = None
    skipped_sold = []
    for _ in range(MAX_CHECKS_PER_REQUEST + 1):
        candidate = conn.execute(
            f"""
            SELECT {CARD_COLUMNS}
            FROM decisions d
            JOIN listings l ON l.ebay_item_id = d.ebay_item_id
            WHERE {where}
            ORDER BY d.ai_score DESC, d.id ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if not candidate:
            break

        stale = conn.execute(
            """
            SELECT COALESCE(checked_at, scraped_at) AS since,
                   (julianday('now') - julianday(COALESCE(checked_at, scraped_at))) * 24 AS hours
            FROM listings WHERE ebay_item_id = ?
            """,
            (candidate["ebay_item_id"],),
        ).fetchone()
        age_hours = (stale["hours"] if stale and stale["hours"] is not None else 999)

        if not verify or age_hours < stale_hours or api_budget_left(budget) <= 0:
            row = candidate
            break

        result = ebay_client.check_active(candidate["ebay_item_id"])
        if result["active"] is False:
            mark_listing_status(conn, candidate["ebay_item_id"], "ended", result["reason"])
            skipped_sold.append(candidate["title"])
            conn.commit()
            continue          # try the next best card
        # active, or the check itself failed - either way, show it
        touch_checked(conn, candidate["ebay_item_id"])
        conn.commit()
        row = candidate
        break

    _flush_api_usage()

    if not row:
        conn.close()
        return {"card": None, "remaining": 0, "skipped_sold": skipped_sold}

    card = _hydrate_card(conn, row)
    remaining = conn.execute(
        f"""
        SELECT COUNT(*) as n
        FROM decisions d
        JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE {where}
        """,
        params,
    ).fetchone()["n"]
    conn.close()
    return {"card": card, "remaining": remaining, "skipped_sold": skipped_sold}


@app.post("/api/decisions/{decision_id}/decide")
def decide(decision_id: int, body: Decision):
    conn = get_conn()
    record_your_decision(conn, decision_id, body.decision.upper())
    if body.notes:
        add_decision_note(conn, decision_id, body.notes)
    if body.reasons:
        set_reject_reasons(conn, decision_id, body.reasons)

    blocked_model = None
    if body.block_model:
        row = conn.execute(
            """
            SELECT l.camera_model_guess
            FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
            WHERE d.id = ?
            """,
            (decision_id,),
        ).fetchone()
        if row and row["camera_model_guess"]:
            blocked_model = row["camera_model_guess"]
            reason = body.notes or (", ".join(body.reasons) if body.reasons else None)
            block_model(
                conn, blocked_model, reason,
                allow_in_bundle=not body.block_everywhere,
            )
    conn.commit()
    conn.close()
    return {"ok": True, "blocked_model": blocked_model}


@app.post("/api/undo")
def undo():
    """Put the last swiped card back at the top of the queue."""
    conn = get_conn()
    decision_id = last_decided(conn)
    if decision_id is None:
        conn.close()
        return {"ok": False, "reason": "nothing to undo"}
    undo_decision(conn, decision_id)
    conn.commit()
    conn.close()
    return {"ok": True, "decision_id": decision_id}


# --- history / buys -------------------------------------------------------

@app.get("/api/history")
def history(only: str = None, limit: int = 100):
    conn = get_conn()
    rows = decision_history(conn, limit=limit, only=only.upper() if only else None)
    conn.close()
    for r in rows:
        images = json.loads(r.get("image_urls") or "[]")
        r["image_url"] = images[0] if images else None
        r.pop("image_urls", None)
        r["reject_reasons"] = json.loads(r.get("reject_reasons") or "[]")
    return {"items": rows}


# --- blocked models -------------------------------------------------------

@app.get("/api/auctions")
def auctions():
    """Auctions split into the ones you've kept (your wishlist, soonest
    ending first - these are the ones you actually need to go bid on)
    and undecided auctions that are about to end, so a good one doesn't
    quietly expire while it's sitting in the swipe queue."""
    conn = get_conn()

    def rows(where, params=()):
        out = []
        for r in conn.execute(
            f"""
            SELECT d.id as decision_id, d.your_decision, d.your_notes,
                   d.ai_score, d.ai_roi, d.ai_recommendation,
                   l.title, l.camera_model_guess, l.price, l.current_bid,
                   l.end_time, l.ebay_url, l.image_urls, l.is_bundle,
                   l.extracted_features
            FROM decisions d
            JOIN listings l ON l.ebay_item_id = d.ebay_item_id
            WHERE l.listing_type = 'AUCTION'
              AND l.end_time IS NOT NULL
              AND datetime(l.end_time) > datetime('now')
              AND {where}
            ORDER BY datetime(l.end_time) ASC
            """,
            params,
        ):
            row = dict(r)
            images = json.loads(row.get("image_urls") or "[]")
            row["image_url"] = images[0] if images else None
            features = json.loads(row.get("extracted_features") or "{}")
            row["max_bid"] = None
            if row.get("camera_model_guess"):
                camera = get_camera_knowledge(conn, row["camera_model_guess"])
                if camera:
                    try:
                        row["max_bid"] = round(max_bid(camera, features, "unknown",
                                                       default_resale=_num(settings_now(), "default_resale")), 2)
                    except Exception:
                        pass
            row.pop("image_urls", None)
            row.pop("extracted_features", None)
            out.append(row)
        return out

    wishlist = rows("d.your_decision = 'BUY'")
    ending_soon = rows(
        """d.your_decision IS NULL
           AND datetime(l.end_time) < datetime('now', '+2 days')
           AND """ + NOT_BLOCKED
    )
    conn.close()
    return {"wishlist": wishlist, "ending_soon": ending_soon}


@app.get("/api/blocked")
def blocked():
    conn = get_conn()
    rows = list_blocked_models(conn)
    # How many listings each block is actually hiding right now. This has
    # to respect the bundle exception, otherwise it counts listings that
    # are still perfectly visible and the number means nothing.
    for r in rows:
        r["hidden_count"] = conn.execute(
            """
            SELECT COUNT(*) as n
            FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
            WHERE d.your_decision IS NULL
              AND l.camera_model_guess = ?
              AND (? = 0 OR COALESCE(l.is_bundle, 0) = 0)
            """,
            (r["model"], r["allow_in_bundle"]),
        ).fetchone()["n"]
    conn.close()
    return {"items": rows}


@app.post("/api/blocked")
def add_block(body: BlockRequest):
    conn = get_conn()
    block_model(conn, body.model, body.reason, allow_in_bundle=body.allow_in_bundle)
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/blocked/{model:path}")
def remove_block(model: str):
    conn = get_conn()
    unblock_model(conn, model)
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/blocked/{model:path}")
def patch_block(model: str, body: BundleException):
    conn = get_conn()
    set_bundle_exception(conn, model, body.allow_in_bundle)
    conn.commit()
    conn.close()
    return {"ok": True}


# --- stats ----------------------------------------------------------------

@app.get("/api/stats")
def stats():
    conn = get_conn()
    row = conn.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE date(decided_at) = date('now')) as seen_today,
          COUNT(*) FILTER (WHERE your_decision = 'BUY' AND date(decided_at) = date('now')) as buys_today,
          COUNT(*) FILTER (WHERE your_decision = 'BUY') as buys_total,
          COUNT(*) FILTER (WHERE your_decision = 'PASS') as passes_total,
          COUNT(*) FILTER (WHERE your_decision = 'UNSURE') as unsure_total,
          COUNT(*) FILTER (WHERE your_decision IS NULL) as pending_total,
          COUNT(*) as decisions_total
        FROM decisions
        """
    ).fetchone()
    result = dict(row)

    _where, _params = _queue_filter()
    result["pending_visible"] = conn.execute(
        f"""
        SELECT COUNT(*) as n
        FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE {_where}
        """,
        _params,
    ).fetchone()["n"]

    # Which models you pass on most - the shortlist of things worth blocking.
    result["top_passed_models"] = [dict(r) for r in conn.execute(
        """
        SELECT l.camera_model_guess as model, COUNT(*) as n
        FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE d.your_decision = 'PASS' AND l.camera_model_guess IS NOT NULL
        GROUP BY l.camera_model_guess
        ORDER BY n DESC
        LIMIT 8
        """
    )]

    result["top_bought_models"] = [dict(r) for r in conn.execute(
        """
        SELECT l.camera_model_guess as model, COUNT(*) as n
        FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE d.your_decision = 'BUY' AND l.camera_model_guess IS NOT NULL
        GROUP BY l.camera_model_guess
        ORDER BY n DESC
        LIMIT 8
        """
    )]

    # Do you actually agree with the scoring formula? If these diverge a
    # lot, the formula needs tuning (or your instinct is picking up
    # something the keyword rules can't see yet).
    agree = conn.execute(
        """
        SELECT
          COUNT(*) as n,
          SUM(CASE WHEN your_decision = ai_recommendation THEN 1 ELSE 0 END) as agreed
        FROM decisions
        WHERE your_decision IS NOT NULL
        """
    ).fetchone()
    result["agreement_rate"] = (
        round(agree["agreed"] / agree["n"], 3) if agree["n"] else None
    )

    st = settings_now()
    result["api_calls_today"] = get_api_calls(conn, _today())
    result["api_budget"] = _num(st, "daily_call_budget", int)
    result["api_hard_limit"] = ebay_client.DAILY_CALL_LIMIT
    result["scan_every_minutes"] = _num(st, "poll_minutes", int)
    conn.close()
    return result


# --- settings & diagnostics ----------------------------------------------

class SettingsUpdate(BaseModel):
    values: dict


@app.get("/api/settings")
def read_settings():
    """Current values plus the metadata the Settings screen renders from,
    so adding a setting in SETTING_DEFS makes it appear in the UI with no
    front-end change."""
    current = settings_now()
    return {
        "values": current,
        "defs": [
            {"key": k, "label": v["label"], "help": v["help"], "type": v["type"],
             "default": v["default"]}
            for k, v in SETTING_DEFS.items()
        ],
    }


@app.post("/api/settings")
def write_settings(body: SettingsUpdate):
    clean = {k: v for k, v in body.values.items() if k in SETTING_DEFS}
    conn = get_conn()
    set_settings(conn, clean)
    conn.commit()
    conn.close()
    return {"ok": True, "saved": sorted(clean), "values": settings_now()}


@app.get("/api/lookup")
def lookup(q: str = "", limit: int = 25):
    """Answers 'why haven't I seen listing X?'.

    Searches everything ever scanned by title or model and, for each hit,
    says exactly why it is or isn't in your swipe queue - decided already,
    model blocked, scored below your threshold, or filtered as an
    algorithm PASS. If nothing comes back at all, it was never scanned:
    either no search phrase matched it, or it sat outside your price range.
    """
    if not q.strip():
        return {"items": [], "note": "Type a model or keyword."}

    st = settings_now()
    include_pass = st.get("include_ai_pass", "0") in ("1", "true", "True")
    min_ev = _num(st, "min_expected_profit")

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT d.id as decision_id, d.your_decision, d.ai_recommendation, d.ai_score,
               l.title, l.camera_model_guess, l.price, l.ebay_url, l.is_bundle,
               l.scraped_at, l.image_urls, l.is_accessory, l.listing_status,
               l.status_reason, l.listing_type
        FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE l.title LIKE ? OR IFNULL(l.camera_model_guess, '') LIKE ?
        ORDER BY d.id DESC LIMIT ?
        """,
        (f"%{q}%", f"%{q}%", limit),
    ).fetchall()

    blocked = {r["model"]: r for r in conn.execute("SELECT * FROM blocked_models")}
    out = []
    for r in rows:
        row = dict(r)
        images = json.loads(row.pop("image_urls", None) or "[]")
        row["image_url"] = images[0] if images else None

        reasons = []
        if row["your_decision"]:
            reasons.append(f"you already swiped {row['your_decision']}")
        if (row.get("listing_status") or "active") != "active":
            reasons.append(f"listing is gone ({row.get('status_reason') or 'ended'})")
        if row.get("is_accessory") and st.get("hide_accessories", "1") in ("1", "true", "True"):
            reasons.append("looks like an accessory, not a camera")
        model = row["camera_model_guess"]
        if model in blocked:
            b = blocked[model]
            if not b["allow_in_bundle"] or not row["is_bundle"]:
                reasons.append(f"{model} is on your blocked list")
        if not include_pass and row["ai_recommendation"] == "PASS":
            reasons.append("scored PASS and 'show algorithm passes' is off")
        if min_ev > -900 and (row["ai_score"] or 0) < min_ev:
            reasons.append(f"expected profit £{row['ai_score']:.2f} is below your £{min_ev:.0f} minimum")

        row["in_queue"] = not reasons
        row["hidden_because"] = reasons
        out.append(row)
    conn.close()

    note = None
    if not out:
        note = ("Never scanned. Either no search phrase matches its title, or its "
                f"price is outside your £{_num(st, 'min_price'):.0f}-£{_num(st, 'max_price'):.0f} range.")
    return {"items": out, "note": note}


class CameraValue(BaseModel):
    model: str
    resale_low: float
    resale_high: float


@app.get("/api/cameras")
def cameras():
    """Every model that has turned up in listings, with its resale range
    if you've set one. Models with no range are scored against the single
    'assumed resale' setting, which is usually why an unfamiliar model
    gets passed on."""
    conn = get_conn()
    rows = models_seen(conn)
    conn.close()
    return {"items": rows, "default_resale": _num(settings_now(), "default_resale")}


@app.post("/api/cameras")
def save_camera(body: CameraValue):
    conn = get_conn()
    set_camera_resale(conn, body.model, body.resale_low, body.resale_high,
                      notes="set from the app")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/rescore")
def rescore():
    """Re-analyse and re-score everything you haven't swiped yet.

    Two things happen here, and both matter:

    1. The listing's TEXT is read again from scratch - is it an accessory,
       is it a bundle, does it say the camera works. Those flags are
       normally worked out once, when a listing is first saved, so any
       improvement to the phrase lists or the accessory detector would
       otherwise only ever apply to listings found afterwards. That's why
       screen protectors saved before the accessory filter existed kept
       showing up in the queue.
    2. The score is recalculated from those fresh flags plus your current
       resale figures.

    All of it is done from text already in the database - no eBay calls.
    """
    st = settings_now()
    default_resale = _num(st, "default_resale")
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT d.id AS decision_id, l.ebay_item_id, l.title, l.description,
               l.price, l.camera_model_guess, l.seller_username
        FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE d.your_decision IS NULL
        """
    ).fetchall()

    changed, accessories, bundles = 0, 0, 0
    for r in rows:
        try:
            title = r["title"] or ""
            features = extract_features(
                title, r["description"] or "",
                distinct_models=len(all_models_in_title(title)),
                price=r["price"],
            )
            if features.get("is_accessory"):
                accessories += 1
            if features.get("is_bundle"):
                bundles += 1

            conn.execute(
                """
                UPDATE listings
                SET extracted_features = ?, is_accessory = ?, is_bundle = ?
                WHERE ebay_item_id = ?
                """,
                (json.dumps(features),
                 1 if features.get("is_accessory") else 0,
                 1 if features.get("is_bundle") else 0,
                 r["ebay_item_id"]),
            )

            camera = get_camera_knowledge(conn, r["camera_model_guess"]) if r["camera_model_guess"] else None
            result = score_listing(r["price"] or 0, camera, features, "unknown",
                                   default_resale=default_resale)
            conn.execute(
                """
                UPDATE decisions
                SET ai_score = ?, ai_roi = ?, ai_confidence = ?,
                    ai_recommendation = ?, reasoning = ?
                WHERE id = ?
                """,
                (result["expected_value"], result["roi"], result["confidence"],
                 result["recommendation"], result["reasoning"], r["decision_id"]),
            )
            changed += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    return {"rescored": changed, "accessories_found": accessories,
            "bundles_found": bundles}


@app.post("/api/scan-now")
def scan_now():
    return scan_once()


@app.post("/api/refresh-details")
def refresh_details():
    """Re-fetch description and photos for everything still waiting to be
    swiped. Repairs listings that were saved before the HTML cleanup and
    the full photo set existed - one eBay call per listing, so it's a
    button rather than something that runs on every scan."""
    conn = get_conn()
    ids = pending_listing_ids(conn)
    fixed, failed = 0, 0
    for item_id in ids:
        try:
            detail = ebay_client.get_item_details(item_id)
        except Exception:
            failed += 1
            continue
        if detail.get("description") or detail.get("images"):
            update_listing_details(
                conn, item_id, detail.get("description", ""), detail.get("images", [])
            )
            fixed += 1
    conn.commit()
    conn.close()
    return {"checked": len(ids), "updated": fixed, "failed": failed}


# Serve the frontend last, so it doesn't swallow the /api/* routes above.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


def main():
    init_db()
    # A fresh database has no resale figures at all, which makes every
    # model fall back to the generic assumed-resale number and score far
    # too harshly. Seed the known ones once; never overwrite your edits.
    try:
        from camera_knowledge import load_seed_data
        seeded = load_seed_data(only_if_empty=True)
        if seeded:
            print(f"Seeded camera_knowledge with {seeded} models.")
    except Exception as e:
        print("Camera knowledge seeding skipped:", e)
    _load_api_usage()
    threading.Thread(target=background_scanner, daemon=True).start()
    import uvicorn
    print("Open http://localhost:8000 in your browser.")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
