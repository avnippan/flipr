from typing import Annotated, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from app.api.schemas import AnalyzeResponse
from app.services.vision import analyze_image
from app.services.pricing import fetch_sold_comps
from app.services.listing import draft_listings
from app.services.comp_aggregator import aggregate_comps

router = APIRouter(prefix="/items", tags=["items"])

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_item(
    image: Annotated[UploadFile, File()],
    chest_width_inches: Annotated[Optional[float], Form()] = None,
    body_length_inches: Annotated[Optional[float], Form()] = None,
):
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    image_bytes = await image.read()

    measurements = None
    if chest_width_inches and body_length_inches:
        measurements = {
            "chest_width_inches": chest_width_inches,
            "body_length_inches": body_length_inches,
        }

    metadata = await analyze_image(image_bytes, image.content_type, measurements)
    comps = await fetch_sold_comps(metadata.search_query)
    listings = await draft_listings(metadata, comps)
    aggregated_pricing = await aggregate_comps(metadata.search_query, comps)

    return AnalyzeResponse(
        metadata=metadata,
        comps=comps,
        listings=listings,
        aggregated_pricing=aggregated_pricing,
    )