"""
Rule-based signal extraction from a listing's title + description.
No LLM. Straight keyword matching against your own §4 phrase lists,
plus a couple of derived flags. This is the whole "understand the
listing" step for Phase 1 - tune the lists as you see phrasing that's
missed or over-triggered.
"""
import re

# Explains "untested" without implying damage.
BENIGN_UNTESTED_PHRASES = [
    "no battery", "no charger", "don't have a charger", "dont have a charger",
    "unable to test", "cannot test", "can't test", "don't know if it works",
    "dont know if it works", "found in storage", "untested, sold as seen",
    "untested sold as seen", "no way to test",
    # More ways of saying "untested, but for an innocent reason"
    "no way of testing", "nothing to test it with", "unable to test it",
    "not been able to test", "haven't been able to test", "havent tested",
    "no battery to test", "without a battery", "missing battery",
    "no leads", "no cable to test", "house clearance", "car boot",
    "from a house clearance", "clearing out", "loft find", "attic find",
]

# A direct claim the camera currently works - stronger than the "soft"
# positive signals below, and decides the starting point in scoring.py.
CONFIRMED_WORKING_PHRASES = [
    # Original set
    "turns on", "screen works", "takes photos", "fully working",
    "working order", "in working order", "tested and working",
    # How sellers actually phrase it. The original list missed extremely
    # common wordings like "works fine" and "tested working" (no "and"),
    # so genuinely working cameras fell through to the pessimistic
    # "condition not stated" base rate and got scored PASS.
    "tested working", "tested and works", "tested, working", "works fine",
    "works well", "works great", "works perfectly", "working perfectly",
    "perfect working", "good working", "full working", "fully functional",
    "fully works", "everything works", "all functions work", "all working",
    "in working condition", "working condition", "confirmed working",
    "powers on", "powers up", "switches on", "switched on", "turned on",
    "functions correctly", "works as it should", "works as intended",
    "no faults", "no issues", "in perfect working",
]

# Softer positive context - doesn't confirm it works right now, but makes
# an "untested" claim more believable.
POSITIVE_PHRASES = [
    "worked last time i used it", "just needs a battery",
    "was my old camera", "stored for years",
]

# A direct claim the camera does NOT work. This has to outrank a plain
# "untested" reading, not just add a penalty on top of a good base rate -
# that was the bug: a listing saying "doesn't turn on" with no separate
# use of the word "untested" was scoring as if the seller confirmed it works.
CONFIRMED_BROKEN_PHRASES = [
    "doesn't turn on", "does not turn on", "won't turn on", "wont turn on",
    "not working", "for parts", "spares or repair", "spares/repair",
    # Same problem in reverse - plenty of ways to say "it's broken".
    "doesn't work", "does not work", "dont work", "doesn't power",
    "does not power on", "won't power on", "wont power on",
    "will not power", "no signs of life", "completely dead",
    "faulty", "not functioning", "doesn't function", "spares repairs",
    "spares and repairs", "as spares", "parts only", "for spares",
    "not tested working", "sold as faulty", "broken",
    # negated-positive forms: "not in working condition" doesn't contain
    # the substring "not working", so it needs its own entries
    "not in working", "isn't working", "isnt working", "no longer works",
    "no longer working", "stopped working", "has stopped", "not powering",
]

# Physical damage - a real risk factor, but doesn't necessarily mean dead.
DAMAGE_PHRASES = [
    "water damage", "dropped", "cracked", "missing parts", "lens damaged",
    "lens error", "battery compartment damaged", "corrosion", "liquid damage",
]

# The single most important red flag per your own §4: an opened/tampered
# camera is worse than an honestly untested one.
TAMPER_PHRASES = [
    "opened", "disassembled", "tampered", "tamper", "repair attempt",
    "attempted repair", "screws missing", "missing screws", "been inside",
    "open it up", "opened it up", "took it apart", "took apart", "pulled apart",
]


# A listing that's several items together rather than one camera. This
# matters for more than description: a model you've blocked because you
# don't want it on its own is still worth seeing inside a job lot, since
# the rest of the lot can carry the deal.
BUNDLE_PHRASES = [
    "job lot", "joblot", "job-lot", "bundle", "bulk lot", "lot of",
    "collection of", "set of", "pair of", "both cameras", "two cameras",
    "three cameras", "multiple cameras", "x2 cameras", "camera lot",
    "spares bundle", "wholesale",
]

# "x2", "2x", "3 x" etc - a count of units in the title.
_MULTI_COUNT_RE = re.compile(r"\b(?:x\s?([2-9])|([2-9])\s?x)\b", re.I)
# "2 cameras", "3 sony" - a number immediately before a plural noun.
_PLURAL_COUNT_RE = re.compile(r"\b([2-9])\s+(?:sony\s+)?(?:digital\s+)?cameras?\b", re.I)


# Words that flip a phrase's meaning when they appear just before it:
# "not in working condition" must not read as working, and "nothing
# broken" must not read as broken.
_NEGATORS = ("not ", "n't ", "no ", "never ", "isnt ", "isn't ", "nothing ",
             "without ", "aside from ", "apart from ")


def _is_negated(text: str, at: int) -> bool:
    """Is the match at `at` preceded by a negator within a short window?"""
    window = text[max(0, at - 18):at]
    return any(neg in window for neg in _NEGATORS)


# --- accessory-only listings ---------------------------------------------
#
# Searching for "sony cybershot" turns up plenty of chargers, batteries,
# screen protectors and cases that merely mention a camera model. They are
# not cameras and should never reach the swipe queue.
#
# The hard part is the opposite case: a real camera whose seller says
# "comes with screen protector and charger" must NOT be filtered out. So
# this only ever reads the TITLE (never the description, where extras get
# listed), and it decides on word ORDER - whatever the title leads with is
# what's being sold.

ACCESSORY_WORDS = [
    "charger", "battery", "batteries", "screen protector", "protector",
    "protective film", "case", "pouch", "bag", "strap", "cable", "lead",
    "usb cord", "memory card", "memory stick", "adapter", "adaptor",
    "remote", "lens cap", "tripod", "dock", "cradle", "power supply",
    "manual", "instruction book", "software cd", "sd card", "filter kit",
    "cleaning kit", "hand grip", "wrist strap", "carry case", "flash gun",
]

CAMERA_WORDS = [
    "camera", "cybershot", "cyber-shot", "cyber shot", "dsc-", "dsc ",
    "compact camera", "digital camera", "camcorder",
]

# "for sony", "fits", "compatible with", "replacement" - the giveaway that
# something is made to go WITH a camera rather than being one.
FOR_MARKERS = [
    "for sony", "for cyber-shot", "for cybershot", "for dsc", "for canon",
    "for nikon", "compatible with", "compatible for", "fits sony", "fits ",
    "replacement for", "replacement battery", "replacement charger",
    "suitable for", "to fit",
]


def detect_accessory(title: str, price: float = None) -> dict:
    """Is this listing an accessory rather than a camera?

    Title only, deliberately - "camera, comes with screen protector" is a
    camera, and reading the description would wrongly bin it.
    """
    t = (title or "").lower()
    if not t:
        return {"is_accessory": False, "accessory_signals": []}

    acc_hits = [w for w in ACCESSORY_WORDS if w in t]
    if not acc_hits:
        return {"is_accessory": False, "accessory_signals": []}

    cam_hits = [w for w in CAMERA_WORDS if w in t]
    for_hits = [m for m in FOR_MARKERS if m in t]
    signals = []

    # 1. an accessory word and nothing that says "camera" at all
    if not cam_hits:
        signals.append(f"title is about a {acc_hits[0]}, no camera mentioned")

    # 2. "<accessory> for <camera>" - made to fit a camera, isn't one
    elif for_hits:
        signals.append(f"'{acc_hits[0]}' + '{for_hits[0]}' - sold to fit a camera")

    # 3. the title leads with the accessory, e.g. "Battery Charger Sony
    #    Cyber-shot DSC-W120". What comes first is what's being sold.
    else:
        first_acc = min(t.find(w) for w in acc_hits)
        first_cam = min(t.find(w) for w in cam_hits)
        if first_acc < first_cam:
            signals.append(f"title leads with '{acc_hits[0]}', camera named after it")

    # 4. corroboration only - never decides on its own
    if signals and price is not None and price <= 8:
        signals.append(f"£{price:g} is accessory money")

    return {"is_accessory": bool(signals), "accessory_signals": signals}


def _find_matches(text: str, phrases: list[str]) -> list[str]:
    text = text.lower()
    hits = []
    for phrase in phrases:
        start = text.find(phrase)
        while start != -1:
            # a phrase that itself begins with a negator ("no faults",
            # "not working") is meant to be read as-is, so only check the
            # text before phrases that don't carry their own negation
            if phrase.startswith(("no ", "not ", "n't", "doesn", "dont", "won", "wont", "will not")) \
               or not _is_negated(text, start):
                hits.append(phrase)
                break
            start = text.find(phrase, start + 1)
    return hits


def detect_bundle(title: str, description: str, distinct_models: int = 0) -> dict:
    """Is this several items rather than one camera? Title is weighted far
    more heavily than description, because sellers routinely mention
    accessories or other cameras they own in the body text without the
    listing itself being a bundle."""
    title = title or ""
    description = description or ""
    signals = []

    signals += [f"title: {p}" for p in _find_matches(title, BUNDLE_PHRASES)]
    if _MULTI_COUNT_RE.search(title):
        signals.append("title: multiple-unit count (e.g. x2)")
    if _PLURAL_COUNT_RE.search(title):
        signals.append("title: numbered plural (e.g. '3 cameras')")
    if distinct_models >= 2:
        signals.append(f"title: {distinct_models} different models named")

    # Description-only mentions are weaker; require an explicit job-lot
    # style phrase rather than any of the looser ones.
    for phrase in ("job lot", "joblot", "job-lot", "bundle of"):
        if phrase in description.lower():
            signals.append(f"description: {phrase}")

    return {"is_bundle": bool(signals), "bundle_signals": signals}


def extract_features(title: str, description: str, distinct_models: int = 0,
                     price: float = None) -> dict:
    """
    Returns a plain dict of booleans/lists - this is the "structured
    signals" that feed scoring.py. Nothing here is a probability yet;
    that conversion happens in scoring.py so the two concerns (what
    does the text say / what does that imply about risk) stay separate.
    """
    text = f"{title}\n{description}"

    benign = _find_matches(text, BENIGN_UNTESTED_PHRASES)
    positive = _find_matches(text, POSITIVE_PHRASES)
    confirmed_working = _find_matches(text, CONFIRMED_WORKING_PHRASES)
    confirmed_broken = _find_matches(text, CONFIRMED_BROKEN_PHRASES)
    damage = _find_matches(text, DAMAGE_PHRASES)
    tamper = _find_matches(text, TAMPER_PHRASES)

    literal_untested = bool(re.search(r"\buntested\b|\bnot tested\b", text.lower()))
    # "no battery" / "can't test" etc only make sense as reasons *for* being
    # untested, so treat them as implying it even without the literal word.
    is_untested = literal_untested or bool(benign)

    bundle = detect_bundle(title, description, distinct_models)
    accessory = detect_accessory(title, price)

    return {
        **bundle,
        **accessory,
        "is_untested": is_untested,
        "benign_untested_reasons": benign,
        "positive_signals": positive,
        "confirmed_working": bool(confirmed_working),
        "confirmed_broken": bool(confirmed_broken),
        "confirmed_broken_signals": confirmed_broken,
        "damage_signals": damage,
        "tamper_signals": tamper,
        "opened_or_tampered": len(tamper) > 0,
        "red_flag_count": len(damage) + len(tamper) + len(confirmed_broken),
        "positive_signal_count": len(positive) + len(benign),
    }
