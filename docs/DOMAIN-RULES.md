# The business, and the rules the code encodes

Context for why the scoring works the way it does. All of this comes from Joe.

## The business

Buy older Sony Cyber-shot compacts cheaply on eBay — CCD-sensor models wanted
for the Y2K/2000s look — clean and test them, pair them with a cheap battery
and charger bought in bulk from AliExpress, and resell on Vinted.

- **Buy price**: typically £15–30 per camera; up to ~£50 for a more desirable
  model that already includes battery/charger/accessories.
- **Sell price**: £60+ typical, usually bundled as camera + battery + charger.
  Memory card not included.
- Buys via both Buy-It-Now and auctions.
- Vinted listings are written by hand. **There is no Vinted API.**

## What makes a listing attractive

The edge is buying listings priced low because the seller can't confirm the
camera works, while reading the description carefully enough to tell an
innocent "untested" from a real problem.

- "Untested" with a benign reason — no battery, no charger, house/loft
  clearance, "can't test it" — is the sweet spot: cheap, usually fine.
- "Untested" with no explanation is more cautious.
- **Opened / disassembled / repair attempted is the single biggest red flag**,
  worse than an honestly untested camera. Someone has already been inside.
- Physical damage (water, dropped, cracked, lens error, corrosion) is a real
  risk but not automatically fatal.
- Explicitly broken ("faulty", "spares or repairs") must always outrank any
  other reading of the text.

## What his own history says

From an old tracking spreadsheet of past purchases and resales
(`historical_data/original_sheet.csv`, imported into `historical_outcomes`):

- **DSC-W170 is the standout performer** — consistently profitable, and not a
  model he'd originally named. Seeded at £80–95 resale.
- **DSC-S600 has been a loser** (0 wins from 3). Seeded at £0 and flagged as
  one to avoid.
- **Non-Sony brands** (Samsung, Kodak, Canon) have historically been weak for
  him. Model detection covers them anyway so they can be blocked explicitly.
- DSC-W1 historical resale (£28–55) is **noticeably lower** than the £75–90 he
  quoted from memory in the original brief. Unresolved — worth checking which
  is right, since it directly changes scoring.

Only five models have real resale figures. Everything else is scored against
the single `default_resale` setting, so **filling in the per-model resale
editor is the highest-leverage improvement available**.

## Bundles and job lots

A model Joe doesn't want on its own is still worth seeing inside a job lot —
the other items can carry the deal. Hence `blocked_models.allow_in_bundle`,
which defaults to true: blocking a model hides its solo listings but not job
lots containing it. He can override per model.

Note the tension: job lots usually cost more than the current £60 price cap,
so raising `max_price` is required for bundles to actually come through.

## Human in the loop

Explicitly required: the AI recommends, Joe approves. No automatic purchasing
and no automatic public listing, now or later. Every swipe, its optional
reason chips and free-text note are logged — that dataset is what a Phase 3
model would eventually learn his taste from.

The free-text note is **always optional**. It exists for the times he feels
like explaining a call, and must never become a required field.
