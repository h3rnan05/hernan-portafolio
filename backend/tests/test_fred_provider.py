"""Test FRED provider against a mocked HTTP response.

Uses respx to mock httpx — no real network calls. To run a smoke test
against the real FRED API, set FRED_API_KEY and run scripts/run_ingestion.py.
"""

from datetime import date

import httpx
import pytest
import respx

from app.ingestion.base import ProviderRateLimited
from app.ingestion.fred import FREDProvider


@pytest.fixture
def fred() -> FREDProvider:
    return FREDProvider(api_key="test-key")


@respx.mock
async def test_fred_returns_observations(fred: FREDProvider) -> None:
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-01-01", "value": "300.5"},
                    {"date": "2026-02-01", "value": "301.2"},
                    {"date": "2026-03-01", "value": "."},  # missing — should skip
                ]
            },
        )
    )

    points = await fred.fetch("CPIAUCSL", date(2026, 1, 1), date(2026, 4, 1))

    assert len(points) == 2
    assert points[0].observed_on == date(2026, 1, 1)
    assert points[0].value == 300.5
    assert points[1].value == 301.2


@respx.mock
async def test_fred_rate_limited_raises(fred: FREDProvider) -> None:
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(429, text="rate limited")
    )

    with pytest.raises(ProviderRateLimited):
        await fred.fetch("CPIAUCSL", date(2026, 1, 1), date(2026, 4, 1))


@respx.mock
async def test_fred_unknown_series_returns_empty(fred: FREDProvider) -> None:
    """FRED returns 400 for unknown series — should be empty, not raise."""
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(400, text="Bad Request")
    )

    points = await fred.fetch("NOTREAL", date(2026, 1, 1), date(2026, 4, 1))
    assert points == []


def test_fred_requires_api_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If neither arg nor env supplies a key, construction must fail."""
    monkeypatch.setenv("FRED_API_KEY", "")
    # Clear cached settings so the fresh empty value is read
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ValueError, match="FRED_API_KEY"):
        FREDProvider(api_key="")
    get_settings.cache_clear()
