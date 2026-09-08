# Gotchas and past bugs

Every entry here is a bug that actually shipped. They are documented because
most are quiet — they produce plausible-looking wrong output rather than a
crash — and several are easy to reintroduce.

## The VM runs Python 3.9

Broke deploys twice:

- `notes: str | None = None` — PEP 604 unions need 3.10. Use `Optional[str]`.
- `Path.write_text(..., newline="\n")` — the `newline` argument needs 3.10.
  Use `open(path, "w", newline="\n")`.

Local Windows runs 3.12, so neither shows up until it hits the VM.

## Schema changes don't reach an existing database

`CREATE TABLE IF NOT EXISTS` silently does nothing when the table exists. A
column added only to `schema.sql` never appears in the live database, and every
insert then fails with "table listings has no column named X".

**Always add new columns to the `MIGRATIONS` list in `db.py`.**

## Features are computed once, at save time

`is_accessory`, `is_bundle` and the condition flags are worked out when a
listing is first saved. Improving `signals.py` therefore only affects listings
found *afterwards* — existing rows keep their old flags.

This is why screen protectors kept appearing after the accessory filter
shipped. `rescore.py` / `POST /api/rescore` now re-runs `extract_features()`
over stored titles and rewrites those flags. **Any change to `signals.py`
should be followed by a re-score**, and the deploy script does this automatically.

## eBay `buyingOptions` is a list, and its order is not guaranteed

An auction that also has Buy It Now comes back as `["FIXED_PRICE","AUCTION"]`.
Reading `buyingOptions[0]` mislabelled those as fixed-price, so they got no
auction badge, no countdown, no max-bid and never appeared in the Auctions tab.

Use `"AUCTION" in buying_options`.

## Search results only carry one thumbnail

The full photo set is `additionalImages` on the **item detail** endpoint, which
is already being called for the description. Condition is mostly judged from
photos, so use `get_item_details()`, not the search summary, for images.

## eBay descriptions are whole HTML documents

Stripping tags alone leaves everything *inside* `<style>` and `<script>` as
visible text, so the reader gets a wall of CSS before the actual description.
`strip_html()` deletes those blocks wholesale, plus `<head>`, comments, and
loose CSS rules that template listings leave in the body.

## Accessory detection must read the title only

Chargers, batteries and screen protectors turn up in camera searches. But a
genuine camera whose description says "comes with screen protector" must not
be filtered out.

`detect_accessory()` therefore reads **only the title**, and decides by word
order: whatever the title leads with is what's being sold. Plus an explicit
"for sony" / "compatible with" / "fits" check. Never extend it to read the
description.

## Condition phrases: cover real phrasings, and handle negation

The original list had `"tested and working"` but not `"tested working"` or
`"works fine"`, so genuinely working cameras fell through to the pessimistic
"nothing stated" base rate and scored PASS. Sellers write in many ways;
the lists in `signals.py` are long on purpose.

Negation is handled by `_is_negated()`: "not in working condition" must not
read as working, and "nothing broken" must not read as broken. Phrases that
carry their own negation ("no faults", "not working") are exempt from the check.

When adding a phrase, test both the plain and negated forms.

## SQLite locking

The background scanner and a manual "scan now" can overlap and did throw
"database is locked". Fixed with WAL mode and a 15-second busy timeout in
`get_conn()`. Keep both.

## One bad listing used to kill an entire scan

Per-item processing is wrapped in try/except so a malformed listing is counted
and skipped rather than aborting the run. `save_listing()` also supplies
explicit defaults for every bound field — a missing optional field must leave
a column empty, not raise.

## Listings sell while they sit in the queue

Scans run every 30 minutes, so a card can be hours old by the time it's seen.
`/api/next` re-checks anything older than `verify_stale_hours` (default 3) via
`check_active()` before serving it, marks sold ones `ended`, and skips to the
next. Capped at `MAX_CHECKS_PER_REQUEST` (5) so a queue full of dead listings
can't burn the API budget on one tap.

`check_active()` distinguishes "definitely gone" (HTTP 404/410) from "the check
itself failed" — a network blip must never discard a live listing.

## camera_knowledge was never being loaded

`load_seed_data()` was defined **twice** in `camera_knowledge.py`; the second
definition silently overrode the first, dropping the personal rules. And
nothing ever called it, so on the live VM the table was empty and *every*
model — including the best historical performer, the DSC-W170 at £80–95 — was
scored against the generic £40 fallback.

Now seeded on startup with `only_if_empty=True`, so it never overwrites values
edited in the app.

## The stale nested duplicate folder

`Documents/camera-flipper/camera-flipper/` is an old snapshot of the project
sitting inside the project. Deploying from it once shipped outdated files and
removed a required field (`ebay_url`), breaking every listing save.

It is git-ignored. Delete it when convenient. Always work from the top-level
folder.
