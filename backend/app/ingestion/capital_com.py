"""Capital.com REST client — session auth + positions snapshot.

Brief §2.3 spec:
  * Demo base URL: ``https://demo-api-capital.backend-capital.com``
  * Auth: POST /api/v1/session  → returns CST + X-SECURITY-TOKEN headers
  * Sessions live ~10 min; auto-refresh on first 401 or after 9 minutes
  * Endpoints used: /api/v1/positions, /api/v1/accounts

The class is async-safe but not concurrency-safe — instantiate one per
process and reuse it. Token refresh is single-flight per instance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

SESSION_PATH = "/api/v1/session"
POSITIONS_PATH = "/api/v1/positions"
ACCOUNTS_PATH = "/api/v1/accounts"

# Sessions live 10 min; refresh proactively at 9 min to avoid round-trip races.
SESSION_TTL = timedelta(minutes=9)


class CapitalComError(Exception):
    """Raised when Capital.com returns an unrecoverable error."""


class CapitalComAuthError(CapitalComError):
    """Bad credentials or invalid token — the user must re-auth."""


@dataclass(slots=True)
class _Session:
    cst: str
    security_token: str
    expires_at: datetime


@dataclass(slots=True)
class CapitalPosition:
    """One open position from /api/v1/positions, normalized for our schema."""

    ticker: str
    quantity: float
    avg_price: float
    last_price: float
    market_value: float
    open_pnl: float
    open_pnl_pct: float
    account_id: str

    @classmethod
    def from_api(cls, item: dict[str, Any], account_id: str) -> CapitalPosition:
        position = item.get("position") or {}
        market = item.get("market") or {}

        size = float(position.get("size") or 0.0)
        avg = float(position.get("level") or 0.0)
        bid = market.get("bid")
        offer = market.get("offer")
        last = float(bid if bid is not None else offer or avg)
        # Capital.com tags positions long/short via ``direction``
        direction = (position.get("direction") or "BUY").upper()
        signed_qty = size if direction == "BUY" else -size
        market_value = signed_qty * last
        open_pnl = (last - avg) * signed_qty
        open_pnl_pct = (open_pnl / (abs(signed_qty) * avg)) if (avg and signed_qty) else 0.0

        # Capital.com's instrument identifier is `epic`. We store the human
        # ticker if available; otherwise fall back to epic.
        ticker = (
            (market.get("instrumentName") and market.get("epic"))
            or market.get("epic")
            or ""
        )
        return cls(
            ticker=str(ticker),
            quantity=signed_qty,
            avg_price=avg,
            last_price=last,
            market_value=market_value,
            open_pnl=open_pnl,
            open_pnl_pct=open_pnl_pct,
            account_id=account_id,
        )


class CapitalComClient:
    """Thin async client over the parts of Capital.com we use."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        identifier: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        s = get_settings()
        self.api_key = api_key or s.capital_api_key
        self.identifier = identifier or s.capital_identifier
        self.password = password or s.capital_api_password
        self.base_url = (base_url or s.capital_base_url).rstrip("/")
        self.timeout_s = timeout_s
        self._session: _Session | None = None
        self._lock = asyncio.Lock()

    def _is_session_live(self) -> bool:
        return self._session is not None and self._session.expires_at > datetime.now(UTC)

    async def _ensure_session(self, client: httpx.AsyncClient) -> _Session:
        async with self._lock:
            if self._is_session_live():
                assert self._session is not None
                return self._session

            if not (self.api_key and self.identifier and self.password):
                raise CapitalComAuthError(
                    "Capital.com credentials missing — set CAPITAL_API_KEY / "
                    "CAPITAL_IDENTIFIER / CAPITAL_API_PASSWORD"
                )

            resp = await client.post(
                f"{self.base_url}{SESSION_PATH}",
                headers={"X-CAP-API-KEY": self.api_key},
                json={
                    "identifier": self.identifier,
                    "password": self.password,
                    "encryptedPassword": False,
                },
            )
            if resp.status_code in (401, 403):
                raise CapitalComAuthError(
                    f"Capital.com auth failed ({resp.status_code}): {resp.text[:200]}"
                )
            if resp.status_code != 200:
                raise CapitalComError(
                    f"Capital.com session error ({resp.status_code}): {resp.text[:200]}"
                )

            cst = resp.headers.get("CST")
            tok = resp.headers.get("X-SECURITY-TOKEN")
            if not cst or not tok:
                raise CapitalComError("Capital.com session response missing CST/X-SECURITY-TOKEN")

            self._session = _Session(
                cst=cst,
                security_token=tok,
                expires_at=datetime.now(UTC) + SESSION_TTL,
            )
            log.info("capital_session_refreshed", expires_at=self._session.expires_at.isoformat())
            return self._session

    async def _auth_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        sess = await self._ensure_session(client)
        return {
            "X-CAP-API-KEY": self.api_key,
            "CST": sess.cst,
            "X-SECURITY-TOKEN": sess.security_token,
        }

    async def get_accounts(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            headers = await self._auth_headers(client)
            resp = await client.get(f"{self.base_url}{ACCOUNTS_PATH}", headers=headers)
            if resp.status_code == 401:
                # Stale token — drop and retry once
                self._session = None
                headers = await self._auth_headers(client)
                resp = await client.get(f"{self.base_url}{ACCOUNTS_PATH}", headers=headers)
            if resp.status_code != 200:
                raise CapitalComError(f"Capital.com /accounts {resp.status_code}: {resp.text[:200]}")
            return list(resp.json().get("accounts") or [])

    async def get_positions(self) -> list[CapitalPosition]:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            headers = await self._auth_headers(client)
            resp = await client.get(f"{self.base_url}{POSITIONS_PATH}", headers=headers)
            if resp.status_code == 401:
                self._session = None
                headers = await self._auth_headers(client)
                resp = await client.get(f"{self.base_url}{POSITIONS_PATH}", headers=headers)
            if resp.status_code != 200:
                raise CapitalComError(
                    f"Capital.com /positions {resp.status_code}: {resp.text[:200]}"
                )

            payload = resp.json()
            items = payload.get("positions") or []

            # Capital.com doesn't echo the account id on every position; we
            # take the first available account as the snapshot's account_id.
            account_id = ""
            try:
                accounts = await self.get_accounts()
                if accounts:
                    account_id = str(accounts[0].get("accountId", ""))
            except CapitalComError:
                # Non-fatal — we still return the positions
                pass

            return [CapitalPosition.from_api(item, account_id=account_id) for item in items]
