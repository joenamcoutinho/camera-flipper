# Roadmap

## Now — sharpen what exists

1. **Fill in per-model resale values.** Settings → resale editor. Models with
   no figures are scored against one flat number, which is the main reason
   good listings get passed. Biggest single win available.
2. **Resolve the DSC-W1 discrepancy** — spreadsheet says £28–55, Joe's brief
   said £75–90. That gap changes scoring outcomes.
3. **Watch the agreement rate** in Settings. It compares Joe's swipes to the
   algorithm's calls. A persistent gap means the scoring rules need tuning —
   the reject-reason chips are the evidence for which direction.
4. **Raise `max_price` if bundles matter.** At £60, most job lots never appear.

## Phase 1.1 — seller analysis

`ebay_client.search_by_seller()` exists and is unused. The intent: pull a
seller's other listings to spot bulk resellers (many similar cameras,
batteries and chargers listed separately). `guess_seller_type()` in `cli.py`
is currently a crude keyword guess, and `scoring.py` already accepts
`seller_type` and adjusts for it — so the plumbing is in place.

## Phase 2 — local LLM (only on Joe's say-so)

Once Phase 1 is judged complete. llama.cpp on his RTX 3080, no paid API. The
natural first job is reading listing descriptions where keyword rules are
weak: ambiguous condition claims, seller evasiveness, unusual phrasings that
`signals.py` misses.

Keep it as an *additional* signal feeding `scoring.py`, not a replacement —
the rule-based path must keep working without it.

## Phase 3 — learn from logged decisions

`decisions` already stores listing text, the algorithm's call, Joe's call, his
reason chips and his notes. Once there are enough rows, that's a supervised
dataset for learning his actual preferences.

Guardrails when this starts: proper time-based splits (listings are not
i.i.d. and prices drift), an honest baseline of the current rule-based scorer,
and no leakage of the algorithm's own score into the features.

## Post-purchase pipeline (tables exist, unused)

`purchases`, `inventory`, `vinted_listings`, `sales` are defined but no code
touches them. Completing the loop — logging what was actually paid, what it
cost to fix, and what it sold for — turns predictions into measurable outcomes
and would let the EV model be calibrated against reality rather than estimates.

## Known deferred items

- **HTTPS.** Currently plain HTTP with Basic Auth. Proper fix is a domain plus
  a named Cloudflare Tunnel.
- **Rotate the credentials** shared during development (webapp password, SSH key).
- **Delete the stale nested `camera-flipper/` folder.**
- **Telegram bot** is legacy. Either keep it working or retire it deliberately.
- **Example photos for Vinted listings.** Originally intended via Instagram
  hashtag search; that API no longer exists. Needs a different source.
