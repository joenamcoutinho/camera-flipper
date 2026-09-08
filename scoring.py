"""
The one part of this system that must never be "the AI decides."
Deterministic cost/EV/ROI math, plus a hand-tuned scorecard that turns
signals.py's output into three outcome probabilities. Tune the numbers
in OUTCOME_PROBABILITIES and the adjustment weights below as you see
real results - this scorecard IS the model until decisions.py has
enough logged outcomes to train something better (Phase 3).
"""
from dataclasses import dataclass, field

# --- 1. Starting point: base rates before reading a single word of the listing ---
# (works, minor_repair, dead)
BASE_RATES = {
    "seller_confirms_working": (0.90, 0.08, 0.02),
    "untested_benign": (0.55, 0.30, 0.15),   # "no battery", "can't test" etc.
    "untested_unexplained": (0.40, 0.30, 0.30),  # says untested, gives no reason
    "confirmed_broken": (0.05, 0.15, 0.80),  # seller states it doesn't work / for parts
    "no_condition_stated": (0.35, 0.30, 0.35),  # neither working nor untested nor broken mentioned
}

# --- 2. Adjustments, applied multiplicatively to the "dead" bucket then renormalised ---
POSITIVE_SIGNAL_BONUS = 0.85   # each positive signal shrinks P(dead) a bit
TAMPER_PENALTY = 2.2           # opened/tampered is the single worst signal you flagged
RED_FLAG_PENALTY = 1.35        # per red-flag phrase beyond the first

# Cheap accessories/repairs assumption if camera_knowledge doesn't specify one
DEFAULT_MINOR_REPAIR_COST = 8
DEFAULT_SALVAGE_VALUE = 5


@dataclass
class CostBreakdown:
    purchase_price: float
    battery_cost: float = 0
    charger_cost: float = 0
    shipping_cost: float = 0
    expected_repair_cost: float = 0

    @property
    def total(self) -> float:
        return (
            self.purchase_price
            + self.battery_cost
            + self.charger_cost
            + self.shipping_cost
            + self.expected_repair_cost
        )


def estimate_outcome_probabilities(features: dict, seller_type: str = "unknown") -> tuple[float, float, float, list[str]]:
    """
    Turns extracted signals into (p_works, p_minor_repair, p_dead) plus
    a list of human-readable reasoning lines. Pure rule-based - swap
    this function's body out later for a trained model without
    touching anything downstream.
    """
    notes = []

    # Order matters: a direct "doesn't turn on" / "for parts" claim has to
    # win over everything else, even if the listing never uses the word
    # "untested" - that mismatch was the original bug in this function.
    if features["confirmed_broken"]:
        p_works, p_minor, p_dead = BASE_RATES["confirmed_broken"]
        notes.append(f"- Seller states it doesn't work ({', '.join(features['confirmed_broken_signals'])})")
    elif features["confirmed_working"] and not features["is_untested"]:
        p_works, p_minor, p_dead = BASE_RATES["seller_confirms_working"]
        notes.append("+ Seller states it currently works")
    elif features["is_untested"] and features["benign_untested_reasons"]:
        p_works, p_minor, p_dead = BASE_RATES["untested_benign"]
        notes.append(f"+ \"Untested\" is explained ({features['benign_untested_reasons'][0]}) - understandable, not a red flag on its own")
    elif features["is_untested"]:
        p_works, p_minor, p_dead = BASE_RATES["untested_unexplained"]
        notes.append("- Untested with no stated reason why")
    else:
        p_works, p_minor, p_dead = BASE_RATES["no_condition_stated"]
        notes.append("- Listing doesn't say whether it works, is untested, or is broken")

    # Positive signals shrink the dead bucket, redistributed to "works"
    n_pos = features["positive_signal_count"]
    if n_pos:
        shrink = min(0.9, 1 - (POSITIVE_SIGNAL_BONUS ** n_pos))
        moved = p_dead * shrink
        p_dead -= moved
        p_works += moved
        notes.append(f"+ {n_pos} positive signal(s) found: {', '.join(features['positive_signals'][:3]) or 'benign untested reasons'}")

    # Physical damage mentions (already-broken listings are handled by the
    # base rate above, so this only fires for damage on top of another
    # starting point - e.g. an untested camera that was also "dropped").
    n_damage = len(features["damage_signals"])
    if n_damage > 0:
        grow = min(0.6, 1 - (1 / RED_FLAG_PENALTY) ** n_damage)
        moved = (p_works + p_minor) * grow * 0.5
        p_dead += moved
        p_works -= moved * 0.6
        p_minor -= moved * 0.4
        notes.append(f"- {n_damage} physical damage mention(s): {', '.join(features['damage_signals'][:3])}")

    # Opened/tampered: the single biggest red flag per your own rules
    if features["opened_or_tampered"]:
        grow = 1 - (1 / TAMPER_PENALTY)
        moved = (p_works + p_minor) * grow * 0.5
        p_dead += moved
        p_minor += moved * 0.5   # if it's not dead, assume it now needs real repair
        p_works -= moved
        notes.append(f"- Listing suggests it's been opened/tampered with ({', '.join(features['tamper_signals'])}) - your own rule: worse than a plain untested camera")

    # Reseller sellers: "no battery" is weaker evidence either way - pull
    # slightly back toward the unexplained-untested base rate.
    if seller_type == "reseller" and features["benign_untested_reasons"]:
        p_works = (p_works + BASE_RATES["untested_unexplained"][0]) / 2
        p_dead = (p_dead + BASE_RATES["untested_unexplained"][2]) / 2
        notes.append("~ Seller lists multiple similar cameras - \"no battery\" is weaker evidence here; treated more cautiously")

    # Clip + renormalise
    p_works, p_minor, p_dead = (max(0.01, v) for v in (p_works, p_minor, p_dead))
    total = p_works + p_minor + p_dead
    return p_works / total, p_minor / total, p_dead / total, notes


def score_listing(
    purchase_price: float,
    camera: dict,
    features: dict,
    seller_type: str = "unknown",
    shipping_cost: float = 0,
    min_acceptable_roi: float = 0.5,
    default_resale: float = 40,
) -> dict:
    """
    Runs the full cost -> EV -> recommendation calc for a fixed-price
    (or current-bid) listing. `camera` is a row from camera_knowledge.
    """
    battery_cost = camera["battery_cost"] if camera else 4
    charger_cost = camera["charger_cost"] if camera else 3
    # When a model has no resale data of its own, everything is judged
    # against this one assumption - so it decides the fate of every model
    # not yet in camera_knowledge. Configurable from Settings.
    resale_mid = ((camera["resale_low"] + camera["resale_high"]) / 2) if camera else default_resale

    p_works, p_minor, p_dead, notes = estimate_outcome_probabilities(features, seller_type)

    cost = CostBreakdown(purchase_price, battery_cost, charger_cost, shipping_cost)

    profit_works = resale_mid - cost.total
    profit_minor = resale_mid - cost.total - DEFAULT_MINOR_REPAIR_COST
    profit_dead = DEFAULT_SALVAGE_VALUE - cost.total

    ev = p_works * profit_works + p_minor * profit_minor + p_dead * profit_dead
    roi = ev / cost.total if cost.total else 0
    confidence = p_works + p_minor

    if ev > 0 and roi >= min_acceptable_roi and not (features["opened_or_tampered"] and roi < 1.0):
        recommendation = "BUY"
    elif ev > 0 and roi > 0:
        recommendation = "UNSURE"
    else:
        recommendation = "PASS"

    return {
        "total_cost": round(cost.total, 2),
        "resale_estimate": (camera["resale_low"], camera["resale_high"]) if camera else (None, None),
        "expected_value": round(ev, 2),
        "roi": round(roi, 2),
        "confidence": round(confidence, 2),
        "p_works": round(p_works, 2),
        "p_minor_repair": round(p_minor, 2),
        "p_dead": round(p_dead, 2),
        "recommendation": recommendation,
        "reasoning": "\n".join(notes),
    }


def max_bid(
    camera: dict,
    features: dict,
    seller_type: str = "unknown",
    shipping_cost: float = 0,
    min_acceptable_profit: float = 15,
    default_resale: float = 40,
) -> float:
    """
    For auctions: what's the most you should bid? Same model, solved
    for the purchase price that hits your minimum acceptable *expected*
    profit exactly - not "is the current price good".
    """
    battery_cost = camera["battery_cost"] if camera else 4
    charger_cost = camera["charger_cost"] if camera else 3
    resale_mid = ((camera["resale_low"] + camera["resale_high"]) / 2) if camera else default_resale

    p_works, p_minor, p_dead, _ = estimate_outcome_probabilities(features, seller_type)

    expected_gross_return = (
        p_works * resale_mid
        + p_minor * (resale_mid - DEFAULT_MINOR_REPAIR_COST)
        + p_dead * DEFAULT_SALVAGE_VALUE
    )
    fixed_costs = battery_cost + charger_cost + shipping_cost
    bid = expected_gross_return - fixed_costs - min_acceptable_profit
    return round(max(0, bid), 2)
