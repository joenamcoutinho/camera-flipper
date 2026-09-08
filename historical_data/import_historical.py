"""
One-off importer for your old tracking spreadsheet (original_sheet.csv).
You said it's old and not very clear - this doesn't try to fix that, it
just pulls out what's usable: real purchase/resale prices and win/loss
per model, loaded into `historical_outcomes` for reference, plus a
printed summary of what's worth acting on.

    python historical_data/import_historical.py

The sheet has no dates, so treat the absolute £ numbers as "what things
sold for at some point in the past", not current market price - but the
*pattern* of which models/brands won or lost is still real signal.
"""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, init_db, save_historical_outcome  # noqa: E402

CSV_PATH = Path(__file__).parent / "original_sheet.csv"


def normalize_model(raw: str) -> str:
    m = raw.lower()
    m = m.replace("cybershot", "").replace("cyber-shot", "")
    m = re.sub(r"\bdsc-?\s*", "dsc ", m)
    m = re.sub(r"\b(silver|black|blue|pink|red|touch|joblot.*|\(.*\))\b", "", m)
    m = re.sub(r"\s+", " ", m).strip()
    # a couple of one-off typo fixes seen in this specific sheet
    if m == "dsc 170":
        m = "dsc w170"
    return m


def parse_float(val):
    if not val:
        return None
    try:
        return float(re.sub(r"[^\d.\-]", "", val))
    except ValueError:
        return None


def load_rows():
    rows = []
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            model = (r.get("Camera") or "").strip()
            if not model or model.upper() == "DSC":
                continue
            rows.append({
                "model_raw": model,
                "model_normalized": normalize_model(model),
                "condition_note": (r.get("Condition") or "").strip(),
                "items_included": (r.get("Listing Stuff included") or "").strip(),
                "ebay_price": parse_float(r.get("Ebay Price")),
                "vinted_price": parse_float(r.get("Vinted Listing")),
                "profit": parse_float(r.get("Profit")),
            })
    return rows


def summarize(rows):
    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model_normalized"]].append(r)

    wins = sum(1 for r in rows if r["profit"] and r["profit"] > 0)
    losses = sum(1 for r in rows if r["profit"] and r["profit"] < 0)
    print(f"{len(rows)} historical rows: {wins} wins, {losses} losses, "
          f"{len(rows) - wins - losses} breakeven/unclear\n")

    print(f"{'MODEL':<14}{'N':>3}  {'WIN RATE':>9}  {'AVG PROFIT':>11}  REAL VINTED SALE PRICES SEEN")
    for key, items in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        if len(items) < 2:
            continue  # single data points aren't worth acting on
        n = len(items)
        w = sum(1 for i in items if i["profit"] and i["profit"] > 0)
        profits = [i["profit"] for i in items if i["profit"] is not None]
        avg = sum(profits) / len(profits) if profits else 0
        vinted = sorted(i["vinted_price"] for i in items if i["vinted_price"])
        print(f"{key:<14}{n:>3}  {w}/{n:>7}  {avg:>11.2f}  {vinted}")


def main():
    init_db()
    rows = load_rows()
    conn = get_conn()
    for row in rows:
        save_historical_outcome(conn, row)
    conn.commit()
    conn.close()
    print(f"Loaded {len(rows)} rows into historical_outcomes.\n")
    summarize(rows)


if __name__ == "__main__":
    main()
