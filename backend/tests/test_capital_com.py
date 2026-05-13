"""Tests for the Capital.com client.

Covers session caching, auto-refresh on 401, normalization of position rows.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.ingestion.capital_com import (
    CapitalComAuthError,
    CapitalComClient,
    CapitalComError,
)

BASE = "https://demo-api-capital.backend-capital.com"


def _client() -> CapitalComClient:
    return CapitalComClient(
        api_key="apik", identifier="user@example.com", password="pw", base_url=BASE
    )


@respx.mock
async def test_session_cached_after_first_call() -> None:
    session_route = respx.post(f"{BASE}/api/v1/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"},
            json={},
        )
    )
    respx.get(f"{BASE}/api/v1/positions").mock(
        return_value=httpx.Response(200, json={"positions": []})
    )
    respx.get(f"{BASE}/api/v1/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": [{"accountId": "A1"}]})
    )

    c = _client()
    await c.get_positions()
    await c.get_positions()

    # Session endpoint hit exactly once across the two get_positions calls
    assert session_route.call_count == 1


@respx.mock
async def test_session_refresh_on_401() -> None:
    """A stale token returns 401; client should refresh and retry once."""
    session_route = respx.post(f"{BASE}/api/v1/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"},
            json={},
        )
    )

    call_state = {"n": 0}

    def positions_handler(request: httpx.Request) -> httpx.Response:
        call_state["n"] += 1
        if call_state["n"] == 1:
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json={"positions": []})

    respx.get(f"{BASE}/api/v1/positions").mock(side_effect=positions_handler)
    respx.get(f"{BASE}/api/v1/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": []})
    )

    c = _client()
    await c.get_positions()

    assert session_route.call_count == 2  # initial + refresh
    assert call_state["n"] == 2


@respx.mock
async def test_auth_failure_raises() -> None:
    respx.post(f"{BASE}/api/v1/session").mock(
        return_value=httpx.Response(401, text="bad creds")
    )
    c = _client()
    with pytest.raises(CapitalComAuthError):
        await c.get_positions()


async def test_missing_credentials_raises() -> None:
    """Construction with empty creds is allowed; first call fails clearly."""
    c = CapitalComClient(api_key="", identifier="", password="", base_url=BASE)
    with pytest.raises(CapitalComAuthError):
        await c.get_positions()


@respx.mock
async def test_position_normalization() -> None:
    respx.post(f"{BASE}/api/v1/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"},
            json={},
        )
    )
    respx.get(f"{BASE}/api/v1/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": [{"accountId": "ACC123"}]})
    )
    respx.get(f"{BASE}/api/v1/positions").mock(
        return_value=httpx.Response(
            200,
            json={
                "positions": [
                    {
                        "position": {
                            "size": 10,
                            "level": 100.0,
                            "direction": "BUY",
                        },
                        "market": {
                            "epic": "NVDA.US",
                            "instrumentName": "NVIDIA",
                            "bid": 110.0,
                            "offer": 110.5,
                        },
                    }
                ]
            },
        )
    )

    c = _client()
    positions = await c.get_positions()

    assert len(positions) == 1
    p = positions[0]
    assert p.quantity == 10.0
    assert p.avg_price == 100.0
    assert p.last_price == 110.0
    assert p.market_value == pytest.approx(1100.0)
    assert p.open_pnl == pytest.approx(100.0)
    assert p.open_pnl_pct == pytest.approx(0.10)
    assert p.account_id == "ACC123"


@respx.mock
async def test_short_position_signs_correctly() -> None:
    respx.post(f"{BASE}/api/v1/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"},
            json={},
        )
    )
    respx.get(f"{BASE}/api/v1/accounts").mock(
        return_value=httpx.Response(200, json={"accounts": []})
    )
    respx.get(f"{BASE}/api/v1/positions").mock(
        return_value=httpx.Response(
            200,
            json={
                "positions": [
                    {
                        "position": {"size": 5, "level": 50.0, "direction": "SELL"},
                        "market": {"epic": "X", "bid": 45.0, "offer": 45.5},
                    }
                ]
            },
        )
    )

    p = (await _client().get_positions())[0]
    assert p.quantity == -5.0
    # Short P&L: (45 - 50) * -5 = +25
    assert p.open_pnl == pytest.approx(25.0)


@respx.mock
async def test_session_response_missing_headers_raises() -> None:
    respx.post(f"{BASE}/api/v1/session").mock(
        return_value=httpx.Response(200, json={})  # no headers
    )
    with pytest.raises(CapitalComError):
        await _client().get_positions()
