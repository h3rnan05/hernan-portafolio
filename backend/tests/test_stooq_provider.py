"""Tests for the Stooq CSV provider.

Covers the apikey-gated 200 response Stooq started returning in 2026, the
happy CSV path, the rate-limit path, and HTML/empty fallthrough.
"""

from datetime import date

import httpx
import pytest
import respx

from app.ingestion.base import ProviderRateLimited
from app.ingestion.stooq import StooqProvider


@pytest.fixture
def stooq() -> StooqProvider:
    return StooqProvider(api_key="")


@respx.mock
async def test_stooq_returns_observations(stooq: StooqProvider) -> None:
    csv_body = (
        "Date,Open,High,Low,Close,Volume\n"
        "2026-01-02,135.0,137.5,134.5,137.2,12345678\n"
        "2026-01-03,137.2,138.0,136.0,136.8,11223344\n"
    )
    respx.get("https://stooq.com/q/d/l/").mock(
        return_value=httpx.Response(200, text=csv_body)
    )

    points = await stooq.fetch("nvda.us", date(2026, 1, 1), date(2026, 1, 5))

    assert len(points) == 2
    assert points[0].observed_on == date(2026, 1, 2)
    assert points[0].value == 137.2


@respx.mock
async def test_stooq_apikey_gate_returns_empty(stooq: StooqProvider) -> None:
    """Stooq paywalls bulk CSV downloads behind a captcha-derived apikey.

    Without one we get a friendly text message at HTTP 200 — the provider
    should swallow it and return [] so the chain falls through.
    """
    body = (
        "Get your apikey:\n\n"
        "1. Open https://stooq.com/q/d/?s=nvda.us&get_apikey\n"
        "2. Enter the captcha code.\n"
    )
    respx.get("https://stooq.com/q/d/l/").mock(
        return_value=httpx.Response(200, text=body)
    )

    points = await stooq.fetch("nvda.us", date(2026, 1, 1), date(2026, 1, 5))
    assert points == []


@respx.mock
async def test_stooq_rate_limited_raises(stooq: StooqProvider) -> None:
    respx.get("https://stooq.com/q/d/l/").mock(return_value=httpx.Response(429, text="rate"))
    with pytest.raises(ProviderRateLimited):
        await stooq.fetch("nvda.us", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_stooq_empty_body_returns_empty(stooq: StooqProvider) -> None:
    respx.get("https://stooq.com/q/d/l/").mock(return_value=httpx.Response(200, text=""))
    assert await stooq.fetch("xxx.us", date(2026, 1, 1), date(2026, 1, 5)) == []


@respx.mock
async def test_stooq_apikey_passthrough() -> None:
    """When STOOQ_API_KEY is set, it must be appended to the query."""
    captured: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            text="Date,Open,High,Low,Close,Volume\n2026-01-02,1,1,1,1,1\n",
        )

    respx.get("https://stooq.com/q/d/l/").mock(side_effect=respond)
    sk = StooqProvider(api_key="my-secret")
    await sk.fetch("nvda.us", date(2026, 1, 1), date(2026, 1, 5))
    assert captured.get("apikey") == "my-secret"
