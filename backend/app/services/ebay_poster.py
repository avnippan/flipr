from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

import httpx

_log = logging.getLogger(__name__)

_BASE_URL = "https://api.ebay.com"

_CONDITION_MAP = {
    "New with tags": "NEW_WITH_TAGS",
    "New without tags": "NEW_WITHOUT_DEFECTS",
    "Like new": "LIKE_NEW",
    "Excellent": "LIKE_NEW",
    "Very good": "LIKE_NEW",
    "Good": "USED_EXCELLENT",
    "Fair": "USED_EXCELLENT",
    "Poor": "FOR_PARTS_OR_NOT_WORKING",
}

_CATEGORY_MAP = {
    "t-shirt": "15687",
    "tshirt": "15687",
    "graphic tee": "15687",
    "shirt": "57990",
    "button-up": "57990",
    "polo": "57990",
    "hoodie": "155183",
    "sweatshirt": "155183",
    "jacket": "57988",
    "pants": "57989",
    "jeans": "11483",
    "long sleeve shirt": "57990",
    "long sleeve": "57990",
}


@dataclass
class EbayListingResult:
    success: bool
    listing_id: str | None = None
    listing_url: str | None = None
    offer_id: str | None = None
    sku: str | None = None
    error: str | None = None


class EbayPoster:
    def __init__(
        self,
        user_token: str,
        fulfillment_policy_id: str,
        payment_policy_id: str,
        return_policy_id: str,
        merchant_location_key: str,
    ) -> None:
        self._token = user_token
        self._fulfillment_policy_id = fulfillment_policy_id
        self._payment_policy_id = payment_policy_id
        self._return_policy_id = return_policy_id
        self._merchant_location_key = merchant_location_key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Content-Language": "en-US",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        }

    async def post_listing(self, listing_data: dict) -> EbayListingResult:
        try:
            sku = f"FLIPR-{uuid4().hex[:12].upper()}"
            async with httpx.AsyncClient(base_url=_BASE_URL, verify=False) as client:
                await self._create_inventory_item(client, sku, listing_data)
                offer_id = await self._create_offer(client, sku, listing_data)
                listing_id = await self._publish_offer(client, offer_id)
            return EbayListingResult(
                success=True,
                listing_id=listing_id,
                listing_url=f"https://www.ebay.com/itm/{listing_id}",
                offer_id=offer_id,
                sku=sku,
            )
        except Exception as exc:
            return EbayListingResult(success=False, error=str(exc))

    async def _create_inventory_item(
        self, client: httpx.AsyncClient, sku: str, listing_data: dict
    ) -> None:
        aspects: dict[str, list[str]] = {
            "Department": ["Men"],
            "Size": [listing_data["size"]] if listing_data.get("size") else ["See description"],
            "Size Type": ["Regular"],
            "Sleeve Length": ["Long Sleeve"] if "long" in (listing_data.get("item_type") or "").lower() else ["Short Sleeve"],
            "Collar Style": ["Crewneck"],
            "Fit": ["Regular"],
        }
        for key, label in (
            ("brand", "Brand"),
            ("color", "Color"),
            ("material", "Material"),
        ):
            if listing_data.get(key):
                aspects[label] = [listing_data[key]]

        image_urls = (
            [listing_data["s3_url"]]
            if listing_data.get("s3_url")
            else listing_data.get("image_urls", [])
        )

        condition_input = listing_data.get("condition", "")
        condition_enum = _CONDITION_MAP.get(condition_input, "USED_EXCELLENT")
        _log.info(f"condition input={condition_input!r} → enum={condition_enum!r}")

        body = {
            "availability": {"shipToLocationAvailability": {"quantity": 1}},
            "condition": condition_enum,
            "conditionDescription": listing_data.get("condition_notes", ""),
            "product": {
                "title": listing_data["title"][:80],
                "description": listing_data.get("description", ""),
                "aspects": aspects,
                "imageUrls": image_urls,
            },
        }

        resp = await client.put(
            f"/sell/inventory/v1/inventory_item/{sku}",
            json=body,
            headers=self._headers(),
        )
        if resp.status_code not in (200, 204):
            raise ValueError(f"inventory_item {resp.status_code}: {resp.text}")

    async def _create_offer(
        self, client: httpx.AsyncClient, sku: str, listing_data: dict
    ) -> str:
        category_id = _CATEGORY_MAP.get(
            listing_data.get("item_type", "").lower(), "15687"
        )

        body = {
            "sku": sku,
            "marketplaceId": "EBAY_US",
            "format": "FIXED_PRICE",
            "availableQuantity": 1,
            "categoryId": category_id,
            "listingDescription": listing_data.get("description", ""),
            "listingPolicies": {
                "fulfillmentPolicyId": self._fulfillment_policy_id,
                "paymentPolicyId": self._payment_policy_id,
                "returnPolicyId": self._return_policy_id,
            },
            "merchantLocationKey": self._merchant_location_key,
            "pricingSummary": {
                "price": {
                    "currency": "USD",
                    "value": f"{float(listing_data['price']):.2f}",
                }
            },
        }

        resp = await client.post(
            "/sell/inventory/v1/offer",
            json=body,
            headers=self._headers(),
        )
        if resp.status_code != 201:
            raise ValueError(f"offer {resp.status_code}: {resp.text}")

        return resp.json()["offerId"]

    async def _publish_offer(self, client: httpx.AsyncClient, offer_id: str) -> str:
        resp = await client.post(
            f"/sell/inventory/v1/offer/{offer_id}/publish",
            headers=self._headers(),
        )
        if resp.status_code != 200:
            raise ValueError(f"publish {resp.status_code}: {resp.text}")

        return resp.json()["listingId"]
