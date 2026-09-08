"""
Seed data for the camera_knowledge table.

DSC-W1 and DSC-W170 below are now real numbers pulled from your old
tracking sheet (historical_data/original_sheet.csv) via
historical_data/import_historical.py - actual eBay/Vinted prices, not
guesses. DSC-W5 stays a placeholder (only 1-2 thin data points in the
sheet); DSC-T7 stays a full placeholder (no rows for it at all in that
sheet, despite being in your original brief).

The sheet has no dates, so treat these as "what things actually sold
for at some point", not necessarily today's market - re-check current
Vinted asking prices before trusting the top end. Run
`python camera_knowledge.py` to (re)load these into the DB.
"""
from db import get_conn, init_db, upsert_camera_knowledge, add_personal_rule

SEED_CAMERAS = [
    {
        # Real data: 5 historical sales, 3 wins / 2 losses.
        "model": "Sony Cyber-shot DSC-W1",
        "resale_low": 28,
        "resale_high": 55,
        "battery_type": "NP-FC11 (compatible)",
        "battery_cost": 4,
        "charger_type": "Universal micro-USB charger",
        "charger_cost": 3,
        "purchase_low": 12,
        "purchase_high": 24,
        "known_problems": "battery door / battery cover damage (seen in your own sales), LCD fade, lens mechanism sticking",
        "demand_score": 0.6,
        "notes": (
            "Real historical record: 5 sales, 3 wins (+15.02, +40.05, +31.02) / 2 losses (-12, -14.49). "
            "Sale prices ranged £27.90 (had a broken battery cover) to £55 (good condition) - condition "
            "clearly moves the price a lot for this model. NOTE: your original brief said typical resale "
            "£75-90 for this model - that's well above anything in the historical sheet. Could be the sheet "
            "is old and prices have risen since, or £75-90 was a best-case/bundled figure. Worth checking "
            "current Vinted asking prices to reconcile before trusting either number fully."
        ),
    },
    {
        # Real data: 4 historical sales, 4/4 wins - your best performer in the sheet.
        "model": "Sony Cyber-shot DSC-W170",
        "resale_low": 80,
        "resale_high": 95,
        "battery_type": "NP-BG1 (compatible)",
        "battery_cost": 4,
        "charger_type": "Universal micro-USB charger",
        "charger_cost": 3,
        "purchase_low": 10,
        "purchase_high": 24,
        "known_problems": "none noted in your historical sales - all 4 sold as working with no issues logged",
        "demand_score": 0.9,
        "notes": (
            "Real historical record: 4 sales, 4/4 wins, profit £56-76 each (avg £67). Sold on Vinted for "
            "£80-95 each time. This was your strongest model in the whole sheet - wasn't in your original "
            "brief's example list, but the data says it should be a priority search query."
        ),
    },
    {
        # Thin data (2 sales) - directional only, not solid enough to fully trust yet.
        "model": "Sony Cyber-shot DSC-W5",
        "resale_low": 50,
        "resale_high": 52,
        "battery_type": "NP-FR1 (compatible)",
        "battery_cost": 4,
        "charger_type": "Universal micro-USB charger",
        "charger_cost": 3,
        "purchase_low": 12,
        "purchase_high": 20,
        "known_problems": "PLACEHOLDER - only 2 clear historical sales, not enough to know common faults yet",
        "demand_score": 0.55,
        "notes": "Thin real data: 2 sales, both sold ~£50-52. A 3rd historical row (refunded, lost parcel) excluded as unclear.",
    },
    {
        # Still a full placeholder - your brief named this model but the sheet has zero rows for it.
        "model": "Sony Cyber-shot DSC-T7",
        "resale_low": 70,
        "resale_high": 95,
        "battery_type": "NP-FT1 (compatible)",
        "battery_cost": 5,
        "charger_type": "Universal micro-USB charger",
        "charger_cost": 3,
        "purchase_low": 20,
        "purchase_high": 35,
        "known_problems": "PLACEHOLDER - no historical rows for this model at all, fill in from your own experience",
        "demand_score": 0.75,
        "notes": "PLACEHOLDER row - not in the historical sheet despite being in your original brief. Verify with real sales.",
    },
    {
        # Real data: 0/3 wins - a clear "avoid" signal, kept in the table so the scorer can flag it.
        "model": "Sony Cyber-shot DSC-S600",
        "resale_low": 0,
        "resale_high": 0,
        "battery_type": "unknown",
        "battery_cost": 4,
        "charger_type": "Universal micro-USB charger",
        "charger_cost": 3,
        "purchase_low": 10,
        "purchase_high": 12,
        "known_problems": "historically 0/3 sales profitable in your own data - total losses each time, cause not recorded",
        "demand_score": 0.1,
        "notes": "Real historical record: 3 purchases, 0 wins, all total losses (-9.94 to -11.90). Treat as an avoid-list model until you know why these failed.",
    },
]

PERSONAL_RULES_FROM_HISTORY = [
    ("Camera is DSC S600", "avoid entirely, or only at a very low price - 0/3 historical wins"),
    ("Camera is a non-Sony brand (Samsung, Kodak, Canon, etc.)", "extra caution - historically 1 win / 5 losses across Samsung/Kodak/Canon in your own sheet; your edge seems to be specifically in Sony Cyber-shot models"),
    ("Listing mentions OIS issue / image stabilisation fault", "treat as a near-dead-weight red flag - caused a full loss on a DSC T300 touch"),
    ("Listing mentions dead flash", "treat as a real fault, not cosmetic - caused a full loss on a DSC WX50"),
]


def load_seed_data(only_if_empty: bool = False):
    """Seed the models and personal rules worked out from your old
    spreadsheet. Called on app startup with only_if_empty=True, so a fresh
    database gets real resale figures instead of every model falling back
    to the generic assumed-resale number - and so it never overwrites
    values you've since edited in the app.
    """
    init_db()
    conn = get_conn()
    if only_if_empty:
        n = conn.execute("SELECT COUNT(*) AS n FROM camera_knowledge").fetchone()["n"]
        if n:
            conn.close()
            return 0
    for camera in SEED_CAMERAS:
        upsert_camera_knowledge(conn, camera)
    for condition, adjustment in PERSONAL_RULES_FROM_HISTORY:
        add_personal_rule(conn, condition, adjustment)
    conn.commit()
    conn.close()
    print(f"Seeded {len(SEED_CAMERAS)} camera_knowledge rows and "
          f"{len(PERSONAL_RULES_FROM_HISTORY)} personal_rules.")
    return len(SEED_CAMERAS)


if __name__ == "__main__":
    load_seed_data()
