import asyncio
import re
import structlog
import httpx
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.models.item import ItemMetadata, CompResult, ListingDraft

logger = structlog.get_logger(__name__)
client = AsyncOpenAI(api_key=settings.openai_api_key, http_client = httpx.AsyncClient(verify=False))


# ---------------------------------------------------------------------------
# 1. SYSTEM PROMPTS — define persona per platform (stays constant)
# ---------------------------------------------------------------------------

POSHMARK_SYSTEM = """You are a Poshmark seller who moves 200+ items per month.
You write listings fast and they sell fast because you:
- Lead with what buyers actually search
- Describe what you see in the photo, not what you imagine
- Call out flaws honestly — it builds trust and prevents returns
- Never sound like a marketing email or retail copywriter
- Use hashtags that real buyers type into Poshmark search
- Know which brands move fast and which aesthetics are trending
- Write like a knowledgeable friend who thrifts, not a brand ambassador

You've been doing this for 3 years. You know when to lean into a vibe
and when to just describe the item plainly."""

EBAY_SYSTEM = """You are a top-rated eBay seller with 5,000+ feedback.
Your listings convert because you:
- Pack the title with searchable keywords (brand, type, color, size, material)
- Never waste title characters on filler words
- Write descriptions that remove buyer doubt with specific details
- Include honest condition notes that prevent returns
- Structure specs as clean bullet points
- Never use marketing language — eBay buyers want facts, not vibes
- Know that eBay has a separate condition field, so you never put condition in titles

You write listings that show up in search and close the sale."""


# ---------------------------------------------------------------------------
# 2. FEW-SHOT EXAMPLES — show the model what good looks like
# ---------------------------------------------------------------------------

POSHMARK_EXAMPLES = """
Here are examples of excellent Poshmark listings. Match this voice and quality:

Example 1 — Branded graphic tee:
Item: Thrasher graphic tee, green, cotton, size M, good condition
Listing:
{
  "title": "Thrasher Green Skateboard Magazine Tee M",
  "description": "Classic Thrasher Magazine tee in green. 100% cotton, fits true to size M. No stains, no holes — just honest wear from a previous life. Pairs well with baggy jeans and Dunks if that's your thing.",
  "suggested_price": 16.00,
  "category_hint": "Men's T-Shirts",
  "hashtags": ["#Thrasher", "#streetwear", "#graphictee", "#thrifted", "#mensclothing"],
  "platform": "poshmark"
}

Example 2 — Unbranded basic:
Item: Unknown brand, raglan shirt, grey/brown, no size tag, good condition
Listing:
{
  "title": "Grey Brown Raglan Long Sleeve - Size Tag Missing",
  "description": "Simple raglan with grey body and brown contrast sleeves. No brand tag visible. Soft feel, probably cotton blend. No damage, just light wash wear. Check measurements in last photo — tag is missing so measure before buying.",
  "suggested_price": 14.00,
  "category_hint": "Men's T-Shirts",
  "hashtags": ["#unbranded", "#raglan", "#longsleeve", "#gentlyused", "#mensclothing"],
  "platform": "poshmark"
}

Example 3 — Western/workwear shirt:
Item: Wrangler plaid button-up, multi-color, cotton, XL, good, snap buttons + chest pockets
Listing:
{
  "title": "Wrangler XL Western Plaid Snap Shirt - Cotton",
  "description": "Wrangler snap-front western in a bold plaid. Cotton, XL, all snaps intact. Two chest pockets. Minor fading at the collar — adds to the worn-in look. Great for the vintage western crowd or anyone building a workwear rotation.",
  "suggested_price": 17.00,
  "category_hint": "Men's Shirts",
  "hashtags": ["#Wrangler", "#western", "#buttondown", "#thrifted", "#mensclothing"],
  "platform": "poshmark"
}

Example 4 — Outdoor/technical brand:
Item: Columbia long sleeve shirt, light blue, good condition, logo on chest
Listing:
{
  "title": "Columbia Light Blue Long Sleeve - Chest Logo",
  "description": "Columbia long sleeve in a clean light blue. Logo on the chest, no damage, no stains. Solid layering piece for hiking or just running errands. Size tag is faded — check measurements in photos.",
  "suggested_price": 19.00,
  "category_hint": "Men's Shirts",
  "hashtags": ["#Columbia", "#gorpcore", "#longsleeve", "#thrifted", "#mensclothing"],
  "platform": "poshmark"
}
"""

EBAY_EXAMPLES = """
Here are examples of excellent eBay listings. Match this structure and tone:

Example 1 — Branded graphic tee:
Item: Thrasher graphic tee, green, cotton, size M, good condition
Listing:
{
  "title": "Thrasher Skateboard Magazine Graphic Tee Green Cotton M",
  "description": "Thrasher Skateboard Magazine graphic tee in green.\\n\\n- Brand: Thrasher\\n- Color: Green\\n- Size: M\\n- Material: 100% Cotton\\n- Graphic: Thrasher Skateboard Magazine logo on front\\n\\nCondition: Pre-owned, good. No holes or stains. Light pilling at collar from normal wash wear. See photos for details.",
  "suggested_price": 16.00,
  "category_hint": "Men's T-Shirts",
  "hashtags": [],
  "platform": "ebay"
}

Example 2 — Western shirt:
Item: Wrangler plaid button-up, multi-color, cotton, XL, snap buttons, two chest pockets
Listing:
{
  "title": "Wrangler Western Plaid Snap Button Shirt Multi-Color XL Cotton",
  "description": "Wrangler western-style plaid shirt with pearl snap buttons.\\n\\n- Brand: Wrangler\\n- Color: Multi-color plaid\\n- Size: XL\\n- Material: Cotton\\n- Features: Snap button front, two chest pockets, western yoke\\n\\nCondition: Pre-owned, good. All snaps functional. Minor collar fading consistent with age. No tears or stains. See photos for accurate color representation.",
  "suggested_price": 17.00,
  "category_hint": "Men's Casual Shirts",
  "hashtags": [],
  "platform": "ebay"
}
"""


# ---------------------------------------------------------------------------
# 3. BANNED PATTERNS — regex-based, catches variations automatically
# ---------------------------------------------------------------------------

BANNED_PATTERNS = [
    r"perfect for \w+",          # catches all variations
    r"\w+wear rotation",         # catches skatewear/streetwear/workwear rotation
    r"elevate your \w+",             # "elevate your wardrobe/style/look"
    r"\w+ enthusiasts?",             # "style enthusiasts", "fashion enthusiast"
    r"must[\s-]have",                # "must-have", "must have"
    r"don'?t miss out",
    r"add to your collection",
    r"secure this",
    r"making waves",
    r"trend sphere",
    r"effortlessly \w+",             # "effortlessly cool/chic/stylish"
    r"headed your way",
    r"waiting to enhance",
    r"whether you'?re",
    r"enjoy all the",
    r"catchy designs?",
    r"unique piece",
]

# Category mapping — deterministic, not left to model guesswork
EBAY_CATEGORY_MAP = {
    "graphic tee": "Men's T-Shirts",
    "t-shirt": "Men's T-Shirts",
    "tee": "Men's T-Shirts",
    "long sleeve t-shirt": "Men's T-Shirts",
    "raglan": "Men's T-Shirts",
    "raglan shirt": "Men's T-Shirts",
    "crewneck": "Men's T-Shirts",
    "button-up shirt": "Men's Casual Shirts",
    "button-up": "Men's Casual Shirts",
    "plaid button-up shirt": "Men's Casual Shirts",
    "flannel": "Men's Casual Shirts",
    "long sleeve shirt": "Men's Casual Shirts",
    "polo": "Men's Casual Shirts",
    "hoodie": "Men's Sweats & Hoodies",
    "sweatshirt": "Men's Sweats & Hoodies",
    "sweater": "Men's Sweaters",
    "jacket": "Men's Coats, Jackets & Vests",
    "coat": "Men's Coats, Jackets & Vests",
    "pants": "Men's Pants",
    "jeans": "Men's Jeans",
    "shorts": "Men's Shorts",
}

POSHMARK_CATEGORY_MAP = {
    "graphic tee": "Men's T-Shirts",
    "t-shirt": "Men's T-Shirts",
    "tee": "Men's T-Shirts",
    "long sleeve t-shirt": "Men's T-Shirts",
    "raglan": "Men's T-Shirts",
    "raglan shirt": "Men's T-Shirts",
    "crewneck": "Men's T-Shirts",
    "button-up shirt": "Men's Shirts",
    "button-up": "Men's Shirts",
    "plaid button-up shirt": "Men's Shirts",
    "flannel": "Men's Shirts",
    "long sleeve shirt": "Men's Shirts",
    "polo": "Men's Shirts",
    "hoodie": "Men's Sweatshirts & Hoodies",
    "sweatshirt": "Men's Sweatshirts & Hoodies",
    "sweater": "Men's Sweaters",
    "jacket": "Men's Jackets & Coats",
    "coat": "Men's Jackets & Coats",
    "pants": "Men's Pants",
    "jeans": "Men's Jeans",
    "shorts": "Men's Shorts",
}


# ---------------------------------------------------------------------------
# 4. PROMPT BUILDER — clean, structured, uses examples not rules lists
# ---------------------------------------------------------------------------

def _build_prompt(item: ItemMetadata, comps: CompResult, platform: str) -> str:
    """Build the user prompt for listing generation."""
    details = ", ".join(item.notable_details) if item.notable_details else "none"
    size_str = item.size if item.size else "size not listed"
    comp_summary = (
        f"{comps.sample_size} recent sold listings · "
        f"${comps.low_price}–${comps.high_price} range · "
        f"${comps.median_price} median"
    )

    examples = POSHMARK_EXAMPLES if platform == "poshmark" else EBAY_EXAMPLES

    platform_notes = {
        "poshmark": (
            "Poshmark-specific rules:\n"
            "- Title: max 80 characters\n"
            "- Description: 2-3 short paragraphs, plain text only (no markdown, no bold, no asterisks)\n"
            "- Include exactly 5 hashtags. Every hashtag starts with # and has NO spaces.\n"
            "- Hashtag slot 1: brand name (use #unbranded if unknown)\n"
            "- Hashtag slots 2-3: searchable terms buyers actually type — "
            "choose from: #streetwear #gorpcore #western #y2k #vintage90s #workwear #vintagestyle #skatewear. "
            "Only use #gorpcore for outdoor brands (Columbia, Patagonia, Kuhl, Arc'teryx, TNF). "
            "Only pick terms that genuinely fit this specific item.\n"
            "- Hashtag slot 4: item category — #graphictee #longsleeve #buttondown #crewneck #raglan #flannel\n"
            "- Hashtag slot 5: #thrifted or #gentlyused or #mensclothing\n"
            "- category_hint must be plain text like 'Men's T-Shirts' — never a hashtag\n"
        ),
        "ebay": (
            "eBay-specific rules:\n"
            "- Title: max 80 characters, keyword-first (brand, type, color, size, material)\n"
            "- NO condition words in the title — eBay has a separate condition field\n"
            "- Description: 1-sentence intro, then bullet specs, then 1 paragraph on condition with specifics\n"
            "- If size is unlisted, end with 'Please check photos for measurements'\n"
            "- hashtags must be an empty list []\n"
            "- No marketing language. Facts only.\n"
        ),
    }

    return f"""Write a {platform} listing for this item.

Item details:
- Brand: {item.brand or "Unknown"}
- Type: {item.item_type}
- Color: {item.color or "Unknown"}
- Size: {size_str}
- Condition: {item.condition or "Not assessed"}
- Material: {item.material or "Unknown"}
- Notable details: {details}

Pricing context (from {comps.sample_size} real eBay sold listings):
{comp_summary}
Suggested listing price: ${comps.suggested_price}

{platform_notes[platform]}

{examples}

Return a JSON object with exactly these fields:
- title: string (max 80 chars)
- description: string (plain text, no markdown)
- suggested_price: number (use {comps.suggested_price})
- category_hint: string (plain text category name, not a hashtag)
- hashtags: list of strings (5 for poshmark, empty list for ebay)
- platform: "{platform}"
"""


def _get_system_prompt(platform: str) -> str:
    """Return the system prompt for the given platform."""
    return POSHMARK_SYSTEM if platform == "poshmark" else EBAY_SYSTEM


# ---------------------------------------------------------------------------
# 5. POST-PROCESSING — deterministic fixes after generation
# ---------------------------------------------------------------------------

def _validate_listing(
    draft: ListingDraft,
    item: ItemMetadata,
    platform: str,
) -> ListingDraft:
    """Validate and fix listing quality issues deterministically."""

    # --- Title: enforce 80 char limit ---
    if len(draft.title) > 80:
        draft.title = draft.title[:77] + "..."

    # --- Hashtags: enforce format and count for Poshmark ---
    if platform == "poshmark":
        # Ensure all hashtags start with # and have no spaces
        cleaned = []
        for tag in draft.hashtags:
            tag = tag.strip()
            if not tag.startswith("#"):
                tag = "#" + tag
            tag = tag.replace(" ", "")
            if len(tag) > 1:
                cleaned.append(tag)
        # Pad to 5 if needed
        fallbacks = ["#thrifted", "#gentlyused", "#mensclothing", "#vintagestyle", "#unisex"]
        while len(cleaned) < 5:
            for fb in fallbacks:
                if fb not in cleaned and len(cleaned) < 5:
                    cleaned.append(fb)
        draft.hashtags = cleaned[:5]

    if platform == "ebay":
        draft.hashtags = []

    # --- Category: override with deterministic mapping ---
    category_map = POSHMARK_CATEGORY_MAP if platform == "poshmark" else EBAY_CATEGORY_MAP
    item_type_lower = item.item_type.lower()

    # Try exact match first, then partial match
    if item_type_lower in category_map:
        draft.category_hint = category_map[item_type_lower]
    else:
        for key, category in category_map.items():
            if key in item_type_lower:
                draft.category_hint = category
                break

    # Make sure category_hint never contains #
    draft.category_hint = draft.category_hint.replace("#", "")

    # --- Description: strip markdown (Poshmark doesn't render it) ---
    if platform == "poshmark":
        draft.description = draft.description.replace("**", "")
        draft.description = draft.description.replace("*", "")
        draft.description = draft.description.replace("##", "")

    # --- Description: remove banned patterns ---
    for pattern in BANNED_PATTERNS:
        draft.description = re.sub(pattern, "", draft.description, flags=re.IGNORECASE)

    # Clean up double spaces left by removals
    draft.description = re.sub(r"  +", " ", draft.description)
    draft.description = draft.description.strip()

    return draft


# ---------------------------------------------------------------------------
# 6. SCORING — measure listing quality programmatically
# ---------------------------------------------------------------------------

def score_listing(
    draft: ListingDraft,
    item: ItemMetadata,
    platform: str,
) -> dict:
    """Score listing quality. Returns dict of scores (0.0-1.0) per dimension."""
    scores = {}

    # Title length
    scores["title_length"] = 1.0 if len(draft.title) <= 80 else 0.0

    # Brand in title (if brand is known)
    if item.brand:
        scores["brand_in_title"] = 1.0 if item.brand.lower() in draft.title.lower() else 0.0
    else:
        scores["brand_in_title"] = 1.0  # no brand to check

    # Hashtag validity (Poshmark only)
    if platform == "poshmark":
        valid_tags = [t for t in draft.hashtags if t.startswith("#") and " " not in t]
        scores["hashtag_format"] = len(valid_tags) / max(len(draft.hashtags), 1)
        scores["hashtag_count"] = 1.0 if len(draft.hashtags) == 5 else 0.5
    else:
        scores["hashtag_format"] = 1.0 if not draft.hashtags else 0.0
        scores["hashtag_count"] = 1.0

    # No banned patterns in description
    banned_found = [p for p in BANNED_PATTERNS if re.search(p, draft.description, re.IGNORECASE)]
    scores["no_banned_phrases"] = 1.0 if not banned_found else 0.0

    # No markdown in Poshmark description
    if platform == "poshmark":
        has_markdown = "**" in draft.description or "##" in draft.description
        scores["no_markdown"] = 0.0 if has_markdown else 1.0
    else:
        scores["no_markdown"] = 1.0

    # Category not a hashtag
    scores["category_valid"] = 0.0 if "#" in draft.category_hint else 1.0

    # Overall score
    scores["overall"] = round(sum(scores.values()) / len(scores), 2)

    return scores


# ---------------------------------------------------------------------------
# 7. API CALL — with system prompt, temperature tuning, retry logic
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type((APITimeoutError, RateLimitError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
async def _draft_for_platform(
    item: ItemMetadata,
    comps: CompResult,
    platform: str,
) -> ListingDraft:
    """Generate a listing draft for one platform."""
    log = logger.bind(platform=platform, item_type=item.item_type, brand=item.brand)
    log.info("listing_draft_start")

    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_vision_model,
            messages=[
                {"role": "system", "content": _get_system_prompt(platform)},
                {"role": "user", "content": _build_prompt(item, comps, platform)},
            ],
            response_format=ListingDraft,
            max_tokens=1200,
            temperature=0.7,
        )
    except APITimeoutError:
        log.error("listing_draft_timeout")
        raise
    except RateLimitError:
        log.warning("listing_draft_rate_limited")
        raise
    except APIError as e:
        log.error("listing_draft_api_error", error=str(e), status_code=e.status_code)
        raise

    message = response.choices[0].message
    if message.refusal:
        log.warning("listing_draft_refused", refusal=message.refusal)
        raise ValueError(f"Model refused to draft listing: {message.refusal}")

    draft = message.parsed
    draft.platform = platform

    # Post-process: deterministic fixes
    draft = _validate_listing(draft, item, platform)

    # Score the listing
    scores = score_listing(draft, item, platform)
    log.info(
        "listing_draft_complete",
        title=draft.title,
        price=draft.suggested_price,
        quality_score=scores["overall"],
    )

    return draft


# ---------------------------------------------------------------------------
# 8. PUBLIC INTERFACE — concurrent drafting for all platforms
# ---------------------------------------------------------------------------

async def draft_listings(item: ItemMetadata, comps: CompResult) -> list[ListingDraft]:
    """
    Draft listings for all platforms concurrently.
    One platform failing does not cancel the other.
    """
    results = await asyncio.gather(
        _draft_for_platform(item, comps, "poshmark"),
        _draft_for_platform(item, comps, "ebay"),
        return_exceptions=True,
    )

    drafts = []
    for platform, result in zip(["poshmark", "ebay"], results):
        if isinstance(result, Exception):
            logger.error("listing_draft_failed", platform=platform, error=str(result))
        else:
            drafts.append(result)

    if not drafts:
        raise RuntimeError("All listing drafts failed — no usable output")

    return drafts