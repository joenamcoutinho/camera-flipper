"""
Minimal eBay Browse API client. Uses the client-credentials OAuth flow
("Application access token") since searching public listings doesn't
need a signed-in user - only real money actions (bidding, buying) would.

Setup: developer.ebay.com -> sign up -> create a "Keyset" -> copy the
Client ID / Client Secret into .env. Sandbox works immediately;
production search needs your app approved (usually quick, sometimes a
short review) - see developer.ebay.com/api-docs/buy/browse/overview.html.

This file is unused by cli.py (which runs on sample data). Wire it in
once you have real credentials and want to stop hand-testing.
"""
import base64
import html as html_module
import os
import re
import time

import requests

def _base_url() -> str:
    # Read fresh every call, not once at import time - the old version froze
    # this before .env had even been loaded, silently sending production
    # keys to the sandbox host. Cheap enough to just not cache it.
    env = os.getenv("EBAY_ENV", "SANDBOX")
    host = "api.sandbox.ebay.com" if env == "SANDBOX" else "api.ebay.com"
    return f"https://{host}"

_token_cache = {"token": None, "expires_at": 0}

# eBay's Browse API allows 5,000 calls per day by default (per app, resets
# at midnight UTC). Every search page is one call and every listing's photo
# /description fetch is another, so a wide scan can eat the day's budget
# fast. This hook lets webapp.py record and cap usage; without it the
# client behaves exactly as before.
DAILY_CALL_LIMIT = 5000
_call_hook = None


def set_call_hook(fn):
    """fn(kind:str) is called once per outbound eBay API request."""
    global _call_hook
    _call_hook = fn


def _count(kind: str):
    if _call_hook:
        try:
            _call_hook(kind)
        except Exception:
            pass  # accounting must never break a working scan


def _get_app_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    client_id = os.environ["EBAY_CLIENT_ID"]
    client_secret = os.environ["EBAY_CLIENT_SECRET"]
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    _count("token")
    resp = requests.post(
        f"{_base_url()}/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data["expires_in"]
    return _token_cache["token"]


def search_items(query: str, price_max: float = None, price_min: float = 0,
                 limit: int = 100, max_pages: int = 3) -> list[dict]:
    """Keyword search, paged. Returns raw Browse API item summaries.

    The old version fetched a single page of 50, which is why a search
    with hundreds of matches only ever produced a handful of cards.
    Browse caps `limit` at 200 per page; we walk `offset` until either
    max_pages or the reported total runs out.
    """
    filters = []
    if price_max or price_min:
        lo = f"{price_min:g}" if price_min else ""
        hi = f"{price_max:g}" if price_max else ""
        filters.append(f"price:[{lo}..{hi}],priceCurrency:GBP")

    out = []
    for page in range(max_pages):
        _count("search")
        resp = requests.get(
            f"{_base_url()}/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {_get_app_token()}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
            },
            params={
                "q": query,
                "filter": ",".join(filters) if filters else None,
                "limit": limit,
                "offset": page * limit,
                "sort": "newlyListed",   # freshest first - stale listings are usually gone
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("itemSummaries", []) or []
        out.extend(batch)
        total = data.get("total", 0) or 0
        if len(batch) < limit or (page + 1) * limit >= total:
            break
    return out


def get_item(item_id: str) -> dict:
    """Full item detail (longer description, all images) for one listing."""
    _count("detail")
    resp = requests.get(
        f"{_base_url()}/buy/browse/v1/item/{item_id}",
        headers={
            "Authorization": f"Bearer {_get_app_token()}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def search_by_seller(username: str, limit: int = 100) -> list[dict]:
    """
    Phase 1.1 seller-analysis hook: pull a seller's other listings to
    spot bulk resellers (many similar cameras, batteries/chargers listed
    separately, etc). Not called from cli.py yet.
    """
    _count("search")
    resp = requests.get(
        f"{_base_url()}/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {_get_app_token()}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        },
        params={"filter": f"sellers:{{{username}}}", "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("itemSummaries", [])


def strip_html(raw: str) -> str:
    """eBay item descriptions are seller-authored HTML, often a whole
    document with embedded stylesheets and scripts.

    Stripping tags alone is not enough: that leaves everything *inside*
    <style> and <script> as text, so the reader gets a wall of CSS before
    the actual description. Those blocks have to be deleted wholesale,
    along with the <head>, comments, and any stray bare CSS rules that
    survive in the body of template-built listings.
    """
    if not raw:
        return ""

    text = raw
    # 1. kill whole blocks whose *contents* are not prose
    text = re.sub(r"(?is)<script\b.*?</script\s*>", " ", text)
    text = re.sub(r"(?is)<style\b.*?</style\s*>", " ", text)
    text = re.sub(r"(?is)<head\b.*?</head\s*>", " ", text)
    text = re.sub(r"(?is)<noscript\b.*?</noscript\s*>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<!\[CDATA\[.*?\]\]>", " ", text)
    # unclosed <style>/<script> (malformed listings) - drop to end of blob
    text = re.sub(r"(?is)<(?:style|script)\b.*", " ", text)

    # 2. block-level tags become line breaks so paragraphs survive
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6]|table|section)\s*>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n• ", text)

    # 3. remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)

    # 4. bare CSS that template listings leave loose in the body, e.g.
    #    ".itemDesc { font-size:12px; }" or "@media only screen {"
    text = re.sub(r"(?m)^\s*[.#@][\w\-\[\]()., >:]+\s*\{[^}]*\}\s*$", "", text)
    text = re.sub(r"(?m)^\s*[\w\-]+\s*:\s*[^;{}\n]+;\s*$", "", text)
    text = re.sub(r"(?m)^\s*[{}]\s*$", "", text)

    # 5. tidy whitespace
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_full_description(item_id: str) -> str:
    """One extra call per new listing to get the real description instead
    of the truncated snippet search results give you."""
    detail = get_item(item_id)
    return strip_html(detail.get("description", ""))


def get_item_details(item_id: str) -> dict:
    """Description AND the full photo set, in a single API call.

    Search results only carry one small thumbnail per item, which is why
    cards were showing a single picture. The item detail response has
    `additionalImages` - every photo the seller uploaded, full size.
    Condition is mostly judged from photos, so these matter.
    """
    detail = get_item(item_id)

    images = []
    main = (detail.get("image") or {}).get("imageUrl")
    if main:
        images.append(main)
    for img in (detail.get("additionalImages") or []):
        url = img.get("imageUrl")
        if url and url not in images:
            images.append(url)

    return {
        "description": strip_html(detail.get("description", "")),
        "images": images,
        "condition": detail.get("condition"),
        "seller_feedback": (detail.get("seller") or {}).get("feedbackPercentage"),
        "seller_score": (detail.get("seller") or {}).get("feedbackScore"),
    }


def check_active(item_id: str) -> dict:
    """Is this listing still buyable?

    Browse only serves live listings: once an item sells or ends, the item
    endpoint answers 404 (errorId 11001). Anything else that goes wrong is
    reported as "unknown" rather than "ended", so a network blip never
    makes us throw away a listing that is actually fine.
    """
    try:
        detail = get_item(item_id)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (404, 410):
            return {"active": False, "reason": "sold or ended"}
        return {"active": None, "reason": f"check failed (HTTP {status})"}
    except Exception as e:
        return {"active": None, "reason": f"check failed ({type(e).__name__})"}

    end = detail.get("itemEndDate")
    if end:
        try:
            from datetime import datetime, timezone
            ends = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if ends <= datetime.now(timezone.utc):
                return {"active": False, "reason": "auction ended"}
        except Exception:
            pass

    avail = (detail.get("estimatedAvailabilities") or [{}])[0]
    if avail.get("estimatedAvailabilityStatus") == "OUT_OF_STOCK":
        return {"active": False, "reason": "out of stock"}

    return {"active": True, "reason": "still listed",
            "price": (detail.get("price") or {}).get("value"),
            "end_time": end}


def to_internal_listing(item: dict) -> dict:
    """Maps a Browse API item into the shape cli.py / db.py expect."""
    # buyingOptions is a LIST and its order is not guaranteed: an auction
    # that also has Buy It Now comes back as ["FIXED_PRICE","AUCTION"].
    # Reading only [0] mislabelled those as fixed-price, which is why
    # auctions never got an auction badge, a countdown, or a max bid.
    buying = item.get("buyingOptions") or ["FIXED_PRICE"]
    is_auction = "AUCTION" in buying
    return {
        "ebay_item_id": item["itemId"],
        # the link through to the actual listing - db.save_listing requires
        # this, so leaving it out breaks every save
        "ebay_url": item.get("itemWebUrl") or item.get("itemAffiliateWebUrl"),
        "title": item.get("title", ""),
        "description": item.get("shortDescription", ""),  # call get_item() for the full description
        "price": float((item.get("price") or {}).get("value", 0) or 0),
        "listing_type": "AUCTION" if is_auction else "FIXED_PRICE",
        "buying_options": ",".join(buying),
        "current_bid": float(item["currentBidPrice"]["value"]) if item.get("currentBidPrice") else None,
        "end_time": item.get("itemEndDate"),
        "seller_username": item.get("seller", {}).get("username", "unknown"),
        "image_urls": (
            ([item["image"]["imageUrl"]] if item.get("image", {}).get("imageUrl") else [])
            + [img["imageUrl"] for img in item.get("thumbnailImages", [])]
        ),
    }
