"""
Comps providers. Add a source by implementing CompProvider in this package,
registering it in _FACTORIES, and setting COMPS_PROVIDER to its name.
"""

from collections.abc import Callable

from app.config import settings
from app.services.comps.base import CompDataType, CompListing, CompProvider


def _ebay_browse() -> CompProvider:
    from app.services.comps.ebay_browse import EbayBrowseProvider
    return EbayBrowseProvider()


def _stub() -> CompProvider:
    from app.services.comps.stub import StubProvider
    return StubProvider()


# Factories, not instances, so nothing is constructed at import time.
_FACTORIES: dict[str, Callable[[], CompProvider]] = {
    "ebay_browse": _ebay_browse,
    "stub": _stub,
}


def get_provider(name: str | None = None) -> CompProvider:
    name = name or settings.comps_provider
    try:
        return _FACTORIES[name]()
    except KeyError:
        raise ValueError(
            f"Unknown comps provider {name!r}. Available: {sorted(_FACTORIES)}"
        ) from None


__all__ = ["CompDataType", "CompListing", "CompProvider", "get_provider"]
