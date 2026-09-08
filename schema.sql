-- Camera Flipping AI - Phase 1 schema (SQLite)
-- Matches the design in the architecture doc: one row per listing seen,
-- one row per decision, and a chain from purchase -> inventory -> sale
-- that eventually becomes a training set (Phase 3+).

CREATE TABLE IF NOT EXISTS camera_knowledge (
    model               TEXT PRIMARY KEY,       -- e.g. "Sony Cyber-shot DSC-W1"
    resale_low          REAL,
    resale_high         REAL,
    battery_type        TEXT,
    battery_cost        REAL,
    charger_type        TEXT,
    charger_cost        REAL,
    purchase_low        REAL,
    purchase_high       REAL,
    known_problems      TEXT,                   -- comma-separated, free text
    demand_score        REAL DEFAULT 0.5,        -- 0-1, your own sense of how easy it sells
    notes               TEXT,
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sellers (
    ebay_username           TEXT PRIMARY KEY,
    feedback_score          INTEGER,
    n_electronics_listings  INTEGER,
    n_camera_listings       INTEGER,
    inferred_type           TEXT,               -- 'private' | 'reseller' | 'unknown'
    checked_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS listings (
    ebay_item_id        TEXT PRIMARY KEY,
    ebay_url             TEXT,
    title                TEXT,
    description          TEXT,
    price                REAL,
    listing_type         TEXT,                  -- 'FIXED_PRICE' | 'AUCTION'
    current_bid          REAL,
    end_time             TEXT,
    seller_username       TEXT REFERENCES sellers(ebay_username),
    camera_model_guess    TEXT,
    image_urls            TEXT,                 -- JSON list
    extracted_features    TEXT,                 -- JSON blob from signals.py
    scraped_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ebay_item_id          TEXT REFERENCES listings(ebay_item_id),
    ai_score              REAL,                 -- expected value in GBP
    ai_roi                REAL,
    ai_confidence         REAL,
    ai_recommendation     TEXT,                 -- 'BUY' | 'UNSURE' | 'PASS'
    reasoning             TEXT,
    your_decision         TEXT,                 -- 'BUY' | 'PASS' | 'UNSURE' | NULL until you respond
    your_notes            TEXT,                 -- optional free text: why you swiped that way. Nothing
                                                  -- forces you to fill this in - it's there so that when
                                                  -- you DO feel like explaining a call, it's captured for
                                                  -- later (tuning the formula, or training data in Phase 3).
    decided_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchases (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id           INTEGER REFERENCES decisions(id),
    price_paid            REAL,
    shipping_cost         REAL DEFAULT 0,
    accessories_cost       REAL DEFAULT 0,
    purchased_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inventory (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id           INTEGER REFERENCES purchases(id),
    camera_model          TEXT REFERENCES camera_knowledge(model),
    status                TEXT DEFAULT 'purchased',
    -- purchased -> received -> tested -> repair_needed -> working -> listed -> sold -> shipped -> completed
    test_notes            TEXT,
    repair_cost           REAL DEFAULT 0,
    repair_notes          TEXT,
    updated_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vinted_listings (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_id           INTEGER REFERENCES inventory(id),
    title                  TEXT,
    description            TEXT,
    price                  REAL,
    photo_order            TEXT,                -- JSON list of filenames/urls
    posted_at              TEXT,
    vinted_url             TEXT
);

CREATE TABLE IF NOT EXISTS sales (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    vinted_listing_id      INTEGER REFERENCES vinted_listings(id),
    sale_price             REAL,
    sale_date              TEXT,
    profit                 REAL
);

-- Raw rows imported from your old tracking spreadsheet. No listing text
-- exists for these (the sheet never captured it), so they can't join
-- into decisions/listings like live data will - this table exists purely
-- to preserve real purchase/resale numbers for camera_knowledge and for
-- a rough empirical base rate, until fresh logged data (with descriptions)
-- takes over as the training signal in Phase 3.
CREATE TABLE IF NOT EXISTS historical_outcomes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    model_raw            TEXT,
    model_normalized     TEXT,
    condition_note       TEXT,
    items_included       TEXT,
    ebay_price           REAL,
    vinted_price         REAL,
    profit               REAL,
    source               TEXT DEFAULT 'Digicam B Sony.csv (undated)'
);

CREATE TABLE IF NOT EXISTS personal_rules (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    condition              TEXT,                -- e.g. "already own the replacement part"
    adjustment             TEXT,                 -- e.g. "+1 risk tier"
    active                 INTEGER DEFAULT 1
);

-- How many eBay API calls we've made today. eBay's Browse API allows
-- 5,000 per day by default and resets at midnight UTC; a wide scan can
-- burn that fast, so the app tracks its own usage and stops scanning
-- before it gets cut off mid-day.
CREATE TABLE IF NOT EXISTS api_usage (
    day                    TEXT PRIMARY KEY,     -- YYYY-MM-DD, UTC
    calls                  INTEGER DEFAULT 0
);

-- Models you've decided you just don't want. Blocking one hides every
-- future listing for it from the swipe queue instead of making you pass
-- on the same bad model over and over.
--
-- allow_in_bundle defaults to 1 on purpose: a model you don't want on its
-- own is still worth seeing when it's part of a job lot, because the other
-- items in the lot can carry the deal. Set it to 0 to hide it everywhere.
CREATE TABLE IF NOT EXISTS blocked_models (
    model                  TEXT PRIMARY KEY,
    reason                 TEXT,
    allow_in_bundle        INTEGER DEFAULT 1,
    blocked_at             TEXT DEFAULT (datetime('now'))
);

-- Everything you can change from the Settings screen. Stored here rather
-- than in .env so tuning search behaviour never needs a redeploy or a
-- service restart - the scanner re-reads these at the start of every run.
CREATE TABLE IF NOT EXISTS settings (
    key                    TEXT PRIMARY KEY,
    value                  TEXT,
    updated_at             TEXT DEFAULT (datetime('now'))
);
