# DEPRECATED — replaced by async job pattern.
# Use create_job.py + process_job.py + get_job.py instead.
# This file is kept as an archive. The AnalyzeItemFunction in template.yaml
# still references analyze_item.py (also deprecated in-place) for backwards
# compatibility until the old endpoint is formally decommissioned.

import asyncio
import base64
import json

import structlog

from app.services.vision import analyze_image
from app.services.pricing import fetch_comps
from app.services.listing import draft_listings

logger = structlog.get_logger(__name__)

_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def handler(event, context):
    logger.info("analyze_item_start")

    try:
        body = json.loads(event.get("body") or "{}")

        image_b64 = body.get("image")
        if not image_b64:
            return _response(400, {"error": "Missing required field: image"})

        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            return _response(400, {"error": "Invalid base64 image data"})

        if len(image_bytes) > _MAX_IMAGE_BYTES:
            return _response(413, {"error": "Image too large. Maximum size is 5MB."})

        mime_type = body.get("mime_type", "image/jpeg")

        measurements = None
        chest = body.get("chest_width_inches")
        length = body.get("body_length_inches")
        if chest is not None and length is not None:
            measurements = {"chest_width_inches": chest, "body_length_inches": length}

        item = asyncio.run(analyze_image(image_bytes, mime_type, measurements))

        comps = asyncio.run(fetch_comps(item.search_query))

        async def _generate_listings():
            return await draft_listings(item, comps)

        listings = asyncio.run(_generate_listings())

        logger.info("analyze_item_complete", item_type=item.item_type, brand=item.brand)

        return _response(200, {
            "metadata": item.model_dump(),
            "comps": comps.model_dump(),
            "listings": [listing.model_dump() for listing in listings],
        })

    except ValueError as e:
        logger.warning("analyze_item_validation_error", error=str(e))
        return _response(400, {"error": str(e)})
    except Exception as e:
        logger.error("analyze_item_error", error=str(e))
        return _response(500, {"error": "Internal server error"})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
