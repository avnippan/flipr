import base64

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.comps.base import CompDataType, CompListing

logger = structlog.get_logger(__name__)

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


def _is_transient(exc: BaseException) -> bool:
    """Retry timeouts, 429 and 5xx. Other 4xx (bad query, bad creds) fail at once."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


async def _get_app_token(client: httpx.AsyncClient) -> str:
    """Fetch eBay OAuth2 client credentials token. Valid for 2 hours."""
    credentials = base64.b64encode(
        f"{settings.ebay_app_id}:{settings.ebay_cert_id}".encode()
    ).decode()

    resp = await client.post(
        _TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _to_listing(item: dict) -> CompListing | None:
    """Normalize a Browse itemSummary. None if it has no USD price."""
    price = item.get("price", {})
    if price.get("currency") != "USD" or "value" not in price:
        return None
    return CompListing(
        title=item.get("title", ""),
        price=float(price["value"]),
        url=item.get("itemWebUrl"),
    )


class EbayBrowseProvider:
    """
    eBay Browse API item search.

    The Browse API only returns active (currently for sale) listings. It has
    no sold filter, so these are asking prices, not sale prices.
    """

    name = "ebay_browse"
    data_type: CompDataType = "active"

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def search(self, query: str) -> list[CompListing]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await _get_app_token(client)
            resp = await client.get(
                _SEARCH_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "limit": 50},
            )
            resp.raise_for_status()
            items = resp.json().get("itemSummaries", [])

        listings = [l for i in items if (l := _to_listing(i)) is not None]
        logger.info("comps_search_complete", provider=self.name, query=query, count=len(listings))
        return listings
