import os

for key, value in {
    "EBAY_APP_ID": "x", "EBAY_CERT_ID": "x", "TOKENS_TABLE_NAME": "t",
    "RATE_LIMITS_TABLE_NAME": "r", "BEDROCK_MODEL_ID": "m",
}.items():
    os.environ.setdefault(key, value)

import httpx
import pytest

from app.services.comps import CompListing, get_provider
from app.services.comps.ebay_browse import EbayBrowseProvider, _is_transient, _to_listing
from app.services.comps.stub import StubProvider
from app.services.pricing import fetch_comps


class FakeSoldProvider:
    name = "fake_sold"
    data_type = "sold"

    def __init__(self, by_query):
        self.by_query = by_query
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return self.by_query.get(query, [])


def _listings(*prices):
    return [CompListing(title=f"item {p}", price=p) for p in prices]


async def test_result_carries_provider_label():
    result = await fetch_comps("levis 501", FakeSoldProvider({"levis 501": _listings(10, 20, 30)}))
    assert (result.data_type, result.source) == ("sold", "fake_sold")
    assert result.median_price == 20 and result.suggested_price == 19.0


async def test_browse_provider_declares_active():
    result = await fetch_comps("levis 501", StubProvider())
    assert result.data_type == "active"
    assert EbayBrowseProvider.data_type == "active"


async def test_broadens_query_when_empty():
    p = FakeSoldProvider({"levis 501": _listings(10, 30)})
    result = await fetch_comps("levis 501 dark wash", p)
    assert p.queries == ["levis 501 dark wash", "levis 501 dark", "levis 501"]
    assert result.search_query == "levis 501"


async def test_raises_when_nothing_found():
    with pytest.raises(ValueError):
        await fetch_comps("levis 501", FakeSoldProvider({}))


def test_non_usd_dropped():
    assert _to_listing({"title": "a", "price": {"currency": "EUR", "value": "5"}}) is None
    listing = _to_listing({"title": "a", "price": {"currency": "USD", "value": "5.5"}})
    assert listing.price == 5.5


def _status_error(code):
    req = httpx.Request("GET", "https://x")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))


def test_only_transient_errors_retry():
    assert _is_transient(httpx.ReadTimeout("t"))
    assert _is_transient(_status_error(429)) and _is_transient(_status_error(503))
    assert not _is_transient(_status_error(400)) and not _is_transient(_status_error(401))


def test_registry():
    assert get_provider("stub").name == "stub"
    assert get_provider("ebay_browse").data_type == "active"
    with pytest.raises(ValueError):
        get_provider("nope")
