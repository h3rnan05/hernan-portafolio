"""Tests for the Twelve Data provider.

Covers the happy path, the 200-OK error envelope (TD's quirk), HTTP 429,
auth failure, unknown-symbol, and the SYM:EXCHANGE disambiguation.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from app.ingestion.base import ProviderError, ProviderRateLimited
from app.ingestion.twelve_data import TwelveDataProvider, _split_symbol


def _td(min_interval_s: float = 0.0) -> TwelveDataProvider:
    """Skip the 1s throttle in unit tests — they don't need it."""
    return TwelveDataProvider(api_key="test-key", min_interval_s=min_interval_s)


# ─── Symbol parsing ─────────────────────────────────────────────────────────


def test_split_symbol_bare() -> None:
    assert _split_symbol("NVDA") == ("NVDA", None)


def test_split_symbol_with_exchange() -> None:
    assert _split_symbol("SAN:BME") == ("SAN", "BME")


def test_split_symbol_with_whitespace() -> None:
    assert _split_symbol("  RELIANCE :  NSE  ") == ("RELIANCE", "NSE")


def test_split_symbol_empty_exchange() -> None:
    """Trailing colon with empty exchange should yield no exchange param."""
    assert _split_symbol("NVDA:") == ("NVDA", None)


# ─── Provider behavior ─────────────────────────────────────────────────────


@respx.mock
async def test_returns_observations() -> None:
    payload = {
        "meta": {"symbol": "NVDA", "interval": "1day"},
        "values": [
            {"datetime": "2026-04-30", "close": "199.57", "open": "1", "high": "1",
             "low": "1", "volume": "1"},
            {"datetime": "2026-05-01", "close": "198.45", "open": "1", "high": "1",
             "low": "1", "volume": "1"},
        ],
        "status": "ok",
    }
    respx.get("https://api.twelvedata.com/time_series").mock(
        return_value=httpx.Response(200, json=payload)
    )

    pts = await _td().fetch("NVDA", date(2026, 4, 1), date(2026, 5, 5))
    assert [p.observed_on for p in pts] == [date(2026, 4, 30), date(2026, 5, 1)]
    assert [p.value for p in pts] == [199.57, 198.45]


@respx.mock
async def test_envelope_429_at_http_200_raises_rate_limited() -> None:
    """Twelve Data sometimes returns 200 + {code:429, ...} instead of HTTP 429."""
    payload = {"code": 429, "message": "limit reached", "status": "error"}
    respx.get("https://api.twelvedata.com/time_series").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with pytest.raises(ProviderRateLimited):
        await _td().fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_http_429_raises_rate_limited() -> None:
    respx.get("https://api.twelvedata.com/time_series").mock(
        return_value=httpx.Response(429, text="rate")
    )
    with pytest.raises(ProviderRateLimited):
        await _td().fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_envelope_400_returns_empty() -> None:
    """Unknown symbol → 200 + status=error → empty list, chain falls through."""
    payload = {"code": 400, "message": "**symbol** not found", "status": "error"}
    respx.get("https://api.twelvedata.com/time_series").mock(
        return_value=httpx.Response(200, json=payload)
    )
    assert await _td().fetch("UNKNOWN", date(2026, 1, 1), date(2026, 1, 5)) == []


@respx.mock
async def test_http_401_raises_provider_error() -> None:
    respx.get("https://api.twelvedata.com/time_series").mock(
        return_value=httpx.Response(401, text="bad key")
    )
    with pytest.raises(ProviderError):
        await _td().fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))


async def test_no_api_key_returns_empty() -> None:
    """Without a key, fetch short-circuits with no network call."""
    p = TwelveDataProvider(api_key="", min_interval_s=0.0)
    assert await p.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5)) == []


@respx.mock
async def test_exchange_passed_through_for_disambiguation() -> None:
    """``SAN:BME`` must split into ``symbol=SAN&exchange=BME`` on the wire."""
    captured: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={"values": [{"datetime": "2026-05-01", "close": "4.21"}], "status": "ok"},
        )

    respx.get("https://api.twelvedata.com/time_series").mock(side_effect=respond)
    await _td().fetch("SAN:BME", date(2026, 4, 1), date(2026, 5, 5))

    assert captured.get("symbol") == "SAN"
    assert captured.get("exchange") == "BME"
    assert captured.get("interval") == "1day"


@respx.mock
async def test_skips_rows_with_missing_close() -> None:
    payload = {
        "values": [
            {"datetime": "2026-05-01", "close": None},
            {"datetime": "2026-05-02", "close": "100.0"},
        ],
        "status": "ok",
    }
    respx.get("https://api.twelvedata.com/time_series").mock(
        return_value=httpx.Response(200, json=payload)
    )
    pts = await _td().fetch("NVDA", date(2026, 4, 1), date(2026, 5, 5))
    assert len(pts) == 1
    assert pts[0].value == 100.0
