from app.services.comps.base import CompDataType, CompListing


class StubProvider:
    """Hardcoded listings for development and tests without eBay credentials."""

    name = "stub"
    data_type: CompDataType = "active"

    async def search(self, query: str) -> list[CompListing]:
        return [
            CompListing(title=f"{query} example listing {i}", price=20.0 + i * 6)
            for i in range(6)
        ]
