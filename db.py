"""
Thin SQLite wrapper. No ORM - plain SQL, plain dicts. Easy to read,
easy to poke at with the sqlite3 CLI if something looks wrong.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "camera_flipper.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn():
    # timeout=15: wait up to 15s for a lock instead of failing immediately -
    # the background scanner and a manual "scan now" can overlap.
    # WAL mode lets readers and a writer work concurrently instead of
    # blocking each other outright, which is the actual fix for that overlap.
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after the first version shipped. "CREATE TABLE IF NOT
# EXISTS" silently does nothing when the table already exists, so a schema
# change alone never reaches a database that's already been created - which
# is exactly how the live VM ended up with a listings table missing
# ebay_url. Every new column goes here so existing databases get patched
# on startup instead of having to be deleted.
MIGRATIONS = [
    ("listings", "ebay_url", "TEXT"),
    ("listings", "is_bundle", "INTEGER DEFAULT 0"),
    ("decisions", "your_notes", "TEXT"),
    ("decisions", "reject_reasons", "TEXT"),   # JSON list of quick-tap reason tags
    ("listings", "is_accessory", "INTEGER DEFAULT 0"),
    ("listings", "listing_status", "TEXT DEFAULT 'active'"),   # active | ended
    ("listings", "status_reason", "TEXT"),
    ("listings", "checked_at", "TEXT"),
]


def migrate(conn):
    for table, column, coltype in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table doesn't exist yet; the schema script will make it
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA_PATH.read_text())
    migrate(conn)
    conn.commit()
    conn.close()


def upsert_camera_knowledge(conn, camera: dict):
    conn.execute(
        """
        INSERT INTO camera_knowledge
            (model, resale_low, resale_high, battery_type, battery_cost,
             charger_type, charger_cost, purchase_low, purchase_high,
             known_problems, demand_score, notes, updated_at)
        VALUES (:model, :resale_low, :resale_high, :battery_type, :battery_cost,
                :charger_type, :charger_cost, :purchase_low, :purchase_high,
                :known_problems, :demand_score, :notes, datetime('now'))
        ON CONFLICT(model) DO UPDATE SET
            resale_low=excluded.resale_low, resale_high=excluded.resale_high,
            battery_type=excluded.battery_type, battery_cost=excluded.battery_cost,
            charger_type=excluded.charger_type, charger_cost=excluded.charger_cost,
            purchase_low=excluded.purchase_low, purchase_high=excluded.purchase_high,
            known_problems=excluded.known_problems, demand_score=excluded.demand_score,
            notes=excluded.notes, updated_at=datetime('now')
        """,
        camera,
    )


def get_camera_knowledge(conn, model: str):
    row = conn.execute(
        "SELECT * FROM camera_knowledge WHERE model = ?", (model,)
    ).fetchone()
    return dict(row) if row else None


def save_listing(conn, listing: dict, features: dict):
    # Listings reference a seller row; make sure a stub exists so the
    # foreign key doesn't reject listings from sellers we haven't
    # analysed yet (real seller analysis is Phase 1.1).
    conn.execute(
        "INSERT OR IGNORE INTO sellers (ebay_username, inferred_type) VALUES (?, 'unknown')",
        (listing["seller_username"],),
    )
    conn.execute(
        """
        INSERT INTO listings
            (ebay_item_id, ebay_url, title, description, price, listing_type, current_bid,
             end_time, seller_username, camera_model_guess, image_urls, extracted_features,
             is_bundle, is_accessory)
        VALUES (:ebay_item_id, :ebay_url, :title, :description, :price, :listing_type, :current_bid,
                :end_time, :seller_username, :camera_model_guess, :image_urls, :extracted_features,
                :is_bundle, :is_accessory)
        ON CONFLICT(ebay_item_id) DO NOTHING
        """,
        {
            # Explicit defaults for every bound field. A caller omitting one
            # used to raise and abort the entire scan; a listing with a
            # missing optional field should just save with that field empty.
            "ebay_item_id": listing["ebay_item_id"],
            "ebay_url": listing.get("ebay_url"),
            "title": listing.get("title", ""),
            "description": listing.get("description", ""),
            "price": listing.get("price"),
            "listing_type": listing.get("listing_type", "FIXED_PRICE"),
            "current_bid": listing.get("current_bid"),
            "end_time": listing.get("end_time"),
            "seller_username": listing.get("seller_username", "unknown"),
            "camera_model_guess": listing.get("camera_model_guess"),
            "image_urls": json.dumps(listing.get("image_urls") or []),
            "extracted_features": json.dumps(features),
            "is_bundle": 1 if features.get("is_bundle") else 0,
            "is_accessory": 1 if features.get("is_accessory") else 0,
        },
    )


def mark_listing_status(conn, ebay_item_id: str, status: str, reason: str = None):
    """Record whether a listing is still buyable. Called after an
    availability check so a sold item stops being offered to swipe on."""
    conn.execute(
        """
        UPDATE listings
        SET listing_status = ?, status_reason = ?, checked_at = datetime('now')
        WHERE ebay_item_id = ?
        """,
        (status, reason, ebay_item_id),
    )


def touch_checked(conn, ebay_item_id: str):
    conn.execute(
        "UPDATE listings SET checked_at = datetime('now') WHERE ebay_item_id = ?",
        (ebay_item_id,),
    )


def update_listing_details(conn, ebay_item_id: str, description: str, images: list):
    """Re-write a listing's description and photo set from a fresh detail
    fetch. Used by the refresh pass that repairs listings saved before the
    HTML cleanup and multi-image support existed."""
    conn.execute(
        "UPDATE listings SET description = ?, image_urls = ? WHERE ebay_item_id = ?",
        (description, json.dumps(images), ebay_item_id),
    )


def pending_listing_ids(conn, limit: int = 200) -> list:
    return [r["ebay_item_id"] for r in conn.execute(
        """
        SELECT l.ebay_item_id
        FROM decisions d JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE d.your_decision IS NULL
        ORDER BY d.id DESC LIMIT ?
        """,
        (limit,),
    )]


def listing_seen(conn, ebay_item_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM listings WHERE ebay_item_id = ?", (ebay_item_id,)
    ).fetchone()
    return row is not None


def save_decision(conn, ebay_item_id: str, result: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO decisions
            (ebay_item_id, ai_score, ai_roi, ai_confidence, ai_recommendation, reasoning)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ebay_item_id,
            result["expected_value"],
            result["roi"],
            result["confidence"],
            result["recommendation"],
            result["reasoning"],
        ),
    )
    return cur.lastrowid


def record_your_decision(conn, decision_id: int, your_decision: str):
    conn.execute(
        "UPDATE decisions SET your_decision = ? WHERE id = ?",
        (your_decision, decision_id),
    )


def add_decision_note(conn, decision_id: int, note: str):
    """Optional free-text comment on a decision - why you swiped that way.
    Never required; just a place to write it down on the times you feel
    like it, per your own instruction."""
    conn.execute(
        "UPDATE decisions SET your_notes = ? WHERE id = ?",
        (note, decision_id),
    )


def save_historical_outcome(conn, row: dict):
    conn.execute(
        """
        INSERT INTO historical_outcomes
            (model_raw, model_normalized, condition_note, items_included,
             ebay_price, vinted_price, profit)
        VALUES (:model_raw, :model_normalized, :condition_note, :items_included,
                :ebay_price, :vinted_price, :profit)
        """,
        row,
    )


def add_personal_rule(conn, condition: str, adjustment: str):
    conn.execute(
        "INSERT INTO personal_rules (condition, adjustment) VALUES (?, ?)",
        (condition, adjustment),
    )


def set_camera_resale(conn, model: str, low: float, high: float, notes: str = None):
    """Set just the resale range for a model, creating the row if needed.
    This is what the Settings screen edits - the other columns keep their
    defaults until there's a reason to fill them in."""
    conn.execute(
        """
        INSERT INTO camera_knowledge (model, resale_low, resale_high, battery_cost,
                                      charger_cost, notes, updated_at)
        VALUES (?, ?, ?, 4, 3, ?, datetime('now'))
        ON CONFLICT(model) DO UPDATE SET
            resale_low = excluded.resale_low,
            resale_high = excluded.resale_high,
            notes = COALESCE(excluded.notes, camera_knowledge.notes),
            updated_at = datetime('now')
        """,
        (model, low, high, notes),
    )


def models_seen(conn) -> list:
    """Every model that's actually turned up in listings, with counts -
    so the Settings screen can show which ones still have no resale data."""
    return [dict(r) for r in conn.execute(
        """
        SELECT l.camera_model_guess AS model,
               COUNT(*) AS listings,
               MIN(l.price) AS cheapest,
               MAX(l.price) AS dearest,
               ck.resale_low, ck.resale_high
        FROM listings l
        LEFT JOIN camera_knowledge ck ON ck.model = l.camera_model_guess
        WHERE l.camera_model_guess IS NOT NULL
        GROUP BY l.camera_model_guess
        ORDER BY COUNT(*) DESC
        """
    )]


# --- settings -------------------------------------------------------------

def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def set_settings(conn, values: dict):
    for key, value in values.items():
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = datetime('now')
            """,
            (key, str(value)),
        )


# --- eBay API usage -------------------------------------------------------

def get_api_calls(conn, day: str) -> int:
    row = conn.execute("SELECT calls FROM api_usage WHERE day = ?", (day,)).fetchone()
    return row["calls"] if row else 0


def set_api_calls(conn, day: str, calls: int):
    conn.execute(
        """
        INSERT INTO api_usage (day, calls) VALUES (?, ?)
        ON CONFLICT(day) DO UPDATE SET calls = excluded.calls
        """,
        (day, calls),
    )


# --- blocked models -------------------------------------------------------

def block_model(conn, model: str, reason: str = None, allow_in_bundle: bool = True):
    conn.execute(
        """
        INSERT INTO blocked_models (model, reason, allow_in_bundle)
        VALUES (?, ?, ?)
        ON CONFLICT(model) DO UPDATE SET
            reason = COALESCE(excluded.reason, blocked_models.reason),
            allow_in_bundle = excluded.allow_in_bundle
        """,
        (model, reason, 1 if allow_in_bundle else 0),
    )


def unblock_model(conn, model: str):
    conn.execute("DELETE FROM blocked_models WHERE model = ?", (model,))


def set_bundle_exception(conn, model: str, allow_in_bundle: bool):
    conn.execute(
        "UPDATE blocked_models SET allow_in_bundle = ? WHERE model = ?",
        (1 if allow_in_bundle else 0, model),
    )


def list_blocked_models(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM blocked_models ORDER BY blocked_at DESC"
    )]


# --- decisions ------------------------------------------------------------

def set_reject_reasons(conn, decision_id: int, reasons: list):
    conn.execute(
        "UPDATE decisions SET reject_reasons = ? WHERE id = ?",
        (json.dumps(reasons), decision_id),
    )


def get_decision(conn, decision_id: int):
    row = conn.execute(
        """
        SELECT d.*, l.title, l.camera_model_guess, l.price, l.ebay_url
        FROM decisions d
        JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE d.id = ?
        """,
        (decision_id,),
    ).fetchone()
    return dict(row) if row else None


def last_decided(conn):
    """Most recently swiped card - what an 'undo' would put back."""
    row = conn.execute(
        """
        SELECT d.id
        FROM decisions d
        WHERE d.your_decision IS NOT NULL
        ORDER BY d.id DESC
        LIMIT 1
        """
    ).fetchone()
    return row["id"] if row else None


def undo_decision(conn, decision_id: int):
    """Clear your swipe so the card comes back to the top of the queue.
    Leaves the AI's own scoring untouched - only your input is reset."""
    conn.execute(
        """
        UPDATE decisions
        SET your_decision = NULL, your_notes = NULL, reject_reasons = NULL
        WHERE id = ?
        """,
        (decision_id,),
    )


def decision_history(conn, limit: int = 100, only: str = None) -> list[dict]:
    where = "d.your_decision IS NOT NULL"
    params = []
    if only:
        where += " AND d.your_decision = ?"
        params.append(only)
    params.append(limit)
    return [dict(r) for r in conn.execute(
        f"""
        SELECT d.id as decision_id, d.your_decision, d.your_notes, d.reject_reasons,
               d.ai_recommendation, d.ai_score, d.ai_roi, d.decided_at,
               l.title, l.camera_model_guess, l.price, l.ebay_url, l.image_urls,
               l.listing_type, l.is_bundle, l.end_time
        FROM decisions d
        JOIN listings l ON l.ebay_item_id = d.ebay_item_id
        WHERE {where}
        ORDER BY d.id DESC
        LIMIT ?
        """,
        params,
    )]
