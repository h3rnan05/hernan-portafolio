"""Tests for the Polygon.io provider."""

from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from app.ingestion.base import ProviderError, ProviderRateLimited
from app.ingestion.polygon import PolygonProvider


def _ms(d: date) -> int:
    return int(
        datetime.combine(d, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
    )


@pytest.fixture
def poly() -> PolygonProvider:
    return PolygonProvider(api_key="test-key")


@respx.mock
async def test_polygon_returns_observations(poly: PolygonProvider) -> None:
    payload = {
        "ticker": "NVDA",
        "status": "OK",
        "results": [
            {"t": _ms(date(2026, 1, 2)), "c": 137.2, "o": 1, "h": 1, "l": 1, "v": 1},
            {"t": _ms(date(2026, 1, 3)), "c": 136.8, "o": 1, "h": 1, "l": 1, "v": 1},
        ],
    }
    respx.get(
        "https://api.polygon.io/v2/aggs/ticker/NVDA/range/1/day/2026-01-01/2026-01-05"
    ).mock(return_value=httpx.Response(200, json=payload))

    pts = await poly.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))
    assert [p.observed_on for p in pts] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert [p.value for p in pts] == [137.2, 136.8]


@respx.mock
async def test_polygon_429_raises(poly: PolygonProvider) -> None:
    respx.get(
        "https://api.polygon.io/v2/aggs/ticker/NVDA/range/1/day/2026-01-01/2026-01-05"
    ).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderRateLimited):
        await poly.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_polygon_403_raises_provider_error(poly: PolygonProvider) -> None:
    respx.get(
        "https://api.polygon.io/v2/aggs/ticker/NVDA/range/1/day/2026-01-01/2026-01-05"
    ).mock(return_value=httpx.Response(403, text="invalid key"))
    with pytest.raises(ProviderError):
        await poly.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_polygon_error_status_returns_empty(poly: PolygonProvider) -> None:
    payload = {"status": "ERROR", "error": "Not found", "results": None}
    respx.get(
        "https://api.polygon.io/v2/aggs/ticker/UNKNOWN/range/1/day/2026-01-01/2026-01-05"
    ).mock(return_value=httpx.Response(200, json=payload))
    assert await poly.fetch("UNKNOWN", date(2026, 1, 1), date(2026, 1, 5)) == []


async def test_polygon_no_key_returns_empty() -> None:
    """Without an API key the provider should short-circuit (no network call)."""
    poly = PolygonProvider(api_key="")
    pts = await poly.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))
    assert pts == []
