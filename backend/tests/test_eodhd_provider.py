"""Tests for the EODHD provider.

Covers happy path, plan-required 402, unknown-symbol 404, 429 rate limit,
401 auth, the envelope-error variant at HTTP 200, and adjusted_close fallback.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from app.ingestion.base import ProviderError, ProviderRateLimited
from app.ingestion.eodhd import EODHDProvider


def _eod() -> EODHDProvider:
    return EODHDProvider(api_key="test-key")


@respx.mock
async def test_returns_observations() -> None:
    payload = [
        {
            "date": "2026-04-30",
            "open": 197.0, "high": 200.5, "low": 196.5,
            "close": 199.57, "adjusted_close": 199.57, "volume": 12345,
        },
        {
            "date": "2026-05-01",
            "open": 199.6, "high": 200.0, "low": 197.0,
            "close": 198.45, "adjusted_close": 198.45, "volume": 11111,
        },
    ]
    respx.get("https://eodhd.com/api/eod/NVDA.US").mock(
        return_value=httpx.Response(200, json=payload)
    )

    pts = await _eod().fetch("NVDA.US", date(2026, 4, 1), date(2026, 5, 5))
    assert [p.observed_on for p in pts] == [date(2026, 4, 30), date(2026, 5, 1)]
    assert [p.value for p in pts] == [199.57, 198.45]


@respx.mock
async def test_prefers_adjusted_close() -> None:
    """When ``adjusted_close`` differs from ``close``, use adjusted_close (corp actions)."""
    payload = [
        {"date": "2026-05-01", "close": 100.0, "adjusted_close": 95.0},
    ]
    respx.get("https://eodhd.com/api/eod/NVDA.US").mock(
        return_value=httpx.Response(200, json=payload)
    )
    pts = await _eod().fetch("NVDA.US", date(2026, 4, 1), date(2026, 5, 5))
    assert pts[0].value == 95.0


@respx.mock
async def test_falls_back_to_close_when_adjusted_zero() -> None:
    """EODHD sometimes returns adjusted_close=0 for non-equity instruments."""
    payload = [{"date": "2026-05-01", "close": 1.25, "adjusted_close": 0}]
    respx.get("https://eodhd.com/api/eod/EURUSD.FOREX").mock(
        return_value=httpx.Response(200, json=payload)
    )
    pts = await _eod().fetch("EURUSD.FOREX", date(2026, 4, 1), date(2026, 5, 5))
    assert pts[0].value == 1.25


@respx.mock
async def test_429_raises_rate_limited() -> None:
    respx.get("https://eodhd.com/api/eod/NVDA.US").mock(
        return_value=httpx.Response(429, text="too many")
    )
    with pytest.raises(ProviderRateLimited):
        await _eod().fetch("NVDA.US", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_402_plan_required_returns_empty() -> None:
    """402 = plan doesn't cover the symbol — should fall through to next provider."""
    respx.get("https://eodhd.com/api/eod/NVDA.US").mock(
        return_value=httpx.Response(402, text="Upgrade your plan")
    )
    assert await _eod().fetch("NVDA.US", date(2026, 1, 1), date(2026, 1, 5)) == []


@respx.mock
async def test_404_unknown_symbol_returns_empty() -> None:
    respx.get("https://eodhd.com/api/eod/UNKNOWN.US").mock(
        return_value=httpx.Response(404, text="not found")
    )
    assert await _eod().fetch("UNKNOWN.US", date(2026, 1, 1), date(2026, 1, 5)) == []


@respx.mock
async def test_401_auth_raises_provider_error() -> None:
    respx.get("https://eodhd.com/api/eod/NVDA.US").mock(
        return_value=httpx.Response(401, text="bad key")
    )
    with pytest.raises(ProviderError):
        await _eod().fetch("NVDA.US", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_envelope_error_at_200_returns_empty() -> None:
    """EODHD sometimes returns 200 + {"errors": ...} instead of an array."""
    payload = {"errors": "API call failed", "message": "Something broke"}
    respx.get("https://eodhd.com/api/eod/NVDA.US").mock(
        return_value=httpx.Response(200, json=payload)
    )
    assert await _eod().fetch("NVDA.US", date(2026, 1, 1), date(2026, 1, 5)) == []


async def test_no_api_key_returns_empty() -> None:
    """Empty key short-circuits with no network call."""
    p = EODHDProvider(api_key="")
    assert await p.fetch("NVDA.US", date(2026, 1, 1), date(2026, 1, 5)) == []


@respx.mock
async def test_empty_array_returns_empty() -> None:
    """No data in range (e.g., weekend-only window) returns empty cleanly."""
    respx.get("https://eodhd.com/api/eod/NVDA.US").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert await _eod().fetch("NVDA.US", date(2026, 1, 4), date(2026, 1, 4)) == []
