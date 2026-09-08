"""
Phase 1 demo entry point. Runs entirely on sample_data/listings_sample.json
- no eBay account, no Telegram bot, no credentials of any kind needed.
This is how you sanity-check the scoring logic before wiring up real data.

    python camera_knowledge.py   # once, to seed the DB
    python cli.py
"""
import json
import re
from pathlib import Path

from db import get_conn, init_db, save_listing, save_decision, listing_seen, get_camera_knowledge
from signals import extract_features
from scoring import score_listing, max_bid

SAMPLE_FILE = Path(__file__).parent / "sample_data" / "listings_sample.json"

# crude stand-in for real seller analysis (Phase 1.1) - flags obvious
# bulk-clearance sellers by name/title until the eBay seller lookup is wired up.
RESELLER_HINTS = ["liquidat", "job lot", "bulk", "wholesale", "clearance"]


def guess_seller_type(listing: dict) -> str:
    text = f"{listing['seller_username']} {listing['title']}".lower()
    return "reseller" if any(h in text for h in RESELLER_HINTS) else "unknown"


# Sony model codes are consistently "DSC" + a letter or two + digits
# (DSC-W1, DSC-W170, DSC-T7, DSC-P200, DSC-HX9V...). Matching the pattern
# rather than a hardcoded list means every Sony compact gets identified,
# which is what makes "never show me this model again" actually work -
# a model that comes back as None can't be blocked.
SONY_DSC_RE = re.compile(r"\bdsc[\s\-_]?([a-z]{1,2})[\s\-_]?(\d{1,4})\s*([a-z]{0,2})\b", re.I)

# Other brands, so non-Sony stuff is identifiable (and blockable) too -
# your own history says these have generally been weak for you.
OTHER_BRAND_RES = [
    (re.compile(r"\b(?:canon\s+)?ixus\s*(\d{1,4})\b", re.I), "Canon IXUS {}"),
    (re.compile(r"\bpowershot\s+([a-z]{1,3}\d{1,4})\b", re.I), "Canon PowerShot {}"),
    (re.compile(r"\bcoolpix\s+([a-z]?\d{1,4})\b", re.I), "Nikon Coolpix {}"),
    (re.compile(r"\bfinepix\s+([a-z]{0,2}\d{1,4})\b", re.I), "Fujifilm FinePix {}"),
    (re.compile(r"\blumix\s+(dmc[\s\-]?[a-z]{1,3}\d{1,3})\b", re.I), "Panasonic Lumix {}"),
    (re.compile(r"\bexilim\s+([a-z]{0,3}[\s\-]?\d{1,4})\b", re.I), "Casio Exilim {}"),
]

# Bare brand fallback - if we can see the brand but not a model number, it's
# still worth naming so it can be blocked as a whole brand.
BRAND_FALLBACKS = [
    ("sony", "Sony (unknown model)"),
    ("canon", "Canon (unknown model)"),
    ("nikon", "Nikon (unknown model)"),
    ("samsung", "Samsung (unknown model)"),
    ("kodak", "Kodak (unknown model)"),
    ("olympus", "Olympus (unknown model)"),
    ("panasonic", "Panasonic (unknown model)"),
    ("fujifilm", "Fujifilm (unknown model)"),
    ("fuji", "Fujifilm (unknown model)"),
    ("casio", "Casio (unknown model)"),
]


def guess_camera_model(title: str) -> str:
    """Best-effort model name from a listing title. Returns a normalised
    string like "Sony Cyber-shot DSC-W170", or None if nothing matches."""
    if not title:
        return None

    m = SONY_DSC_RE.search(title)
    if m:
        letters, digits, suffix = m.group(1).upper(), m.group(2), (m.group(3) or "").upper()
        return f"Sony Cyber-shot DSC-{letters}{digits}{suffix}"

    for pattern, template in OTHER_BRAND_RES:
        m = pattern.search(title)
        if m:
            return template.format(m.group(1).upper().replace(" ", "-"))

    lowered = title.lower()
    for brand, label in BRAND_FALLBACKS:
        if brand in lowered:
            return label
    return None


def all_models_in_title(title: str) -> list:
    """Every distinct Sony model code mentioned - two or more usually means
    a bundle/job lot rather than a single camera."""
    if not title:
        return []
    found = []
    for m in SONY_DSC_RE.finditer(title):
        name = f"Sony Cyber-shot DSC-{m.group(1).upper()}{m.group(2)}{(m.group(3) or '').upper()}"
        if name not in found:
            found.append(name)
    return found


def main():
    init_db()
    conn = get_conn()
    listings = json.loads(SAMPLE_FILE.read_text())

    print(f"{'ITEM':<10} {'MODEL':<26} {'PRICE':>7} {'EV':>8} {'ROI':>6} {'CONF':>6}  RECOMMENDATION")
    print("-" * 90)

    for listing in listings:
        if listing_seen(conn, listing["ebay_item_id"]):
            continue  # already scored this one in a previous run

        features = extract_features(listing["title"], listing["description"])
        seller_type = guess_seller_type(listing)
        model = guess_camera_model(listing["title"])
        camera = get_camera_knowledge(conn, model) if model else None

        result = score_listing(
            purchase_price=listing["price"],
            camera=camera,
            features=features,
            seller_type=seller_type,
        )

        save_listing(conn, {**listing, "camera_model_guess": model}, features)
        decision_id = save_decision(conn, listing["ebay_item_id"], result)

        print(
            f"{listing['ebay_item_id']:<10} {(model or '?'):<26} "
            f"£{listing['price']:>5.0f}  £{result['expected_value']:>6.2f} "
            f"{result['roi']*100:>5.0f}% {result['confidence']*100:>5.0f}%  "
            f"{result['recommendation']}"
        )
        print("           " + result["reasoning"].replace("\n", "\n           "))

        if listing["listing_type"] == "AUCTION" and camera:
            bid = max_bid(camera, features, seller_type)
            print(f"           -> auction: max bid £{bid:.2f}")
        print(f"           (decision id {decision_id})\n")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
