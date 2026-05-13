"""Tests for the Yahoo Finance / yfinance provider.

Mocks Yahoo's chart API JSON response. No real network.
"""

from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from app.ingestion.base import ProviderRateLimited
from app.ingestion.yfinance import YFinanceProvider


def _ts(d: date) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=UTC).timestamp())


@pytest.fixture
def yf() -> YFinanceProvider:
    return YFinanceProvider()


@respx.mock
async def test_yfinance_returns_observations(yf: YFinanceProvider) -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [_ts(date(2026, 1, 2)), _ts(date(2026, 1, 3))],
                    "indicators": {
                        "quote": [{"close": [137.2, 136.8]}],
                    },
                }
            ],
            "error": None,
        }
    }
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/NVDA").mock(
        return_value=httpx.Response(200, json=payload)
    )

    pts = await yf.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))

    assert [p.observed_on for p in pts] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert [p.value for p in pts] == [137.2, 136.8]


@respx.mock
async def test_yfinance_skips_null_close(yf: YFinanceProvider) -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [_ts(date(2026, 1, 2)), _ts(date(2026, 1, 3))],
                    "indicators": {"quote": [{"close": [None, 100.0]}]},
                }
            ],
            "error": None,
        }
    }
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/NVDA").mock(
        return_value=httpx.Response(200, json=payload)
    )
    pts = await yf.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))
    assert len(pts) == 1
    assert pts[0].value == 100.0


@respx.mock
async def test_yfinance_chart_error_returns_empty(yf: YFinanceProvider) -> None:
    """Yahoo returns 200 with `error: {...}` for unknown tickers."""
    payload = {
        "chart": {
            "result": None,
            "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"},
        }
    }
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/UNKNOWN").mock(
        return_value=httpx.Response(200, json=payload)
    )

    pts = await yf.fetch("UNKNOWN", date(2026, 1, 1), date(2026, 1, 5))
    assert pts == []


@respx.mock
async def test_yfinance_429_raises(yf: YFinanceProvider) -> None:
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/NVDA").mock(
        return_value=httpx.Response(429, text="rate")
    )
    with pytest.raises(ProviderRateLimited):
        await yf.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))


@respx.mock
async def test_yfinance_999_raises(yf: YFinanceProvider) -> None:
    """Yahoo's unofficial 999 Edge throttle should also fall through."""
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/NVDA").mock(
        return_value=httpx.Response(999, text="rate")
    )
    with pytest.raises(ProviderRateLimited):
        await yf.fetch("NVDA", date(2026, 1, 1), date(2026, 1, 5))
