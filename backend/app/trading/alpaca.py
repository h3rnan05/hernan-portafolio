"""Alpaca Paper Trading REST client (httpx-based, no SDK required)."""

from __future__ import annotations

import os
from typing import Any

import httpx

PAPER_BASE = "https://paper-api.alpaca.markets/v2"
DATA_BASE  = "https://data.alpaca.markets"


class AlpacaClient:
    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str = PAPER_BASE,
    ) -> None:
        self.key    = api_key    or os.environ["ALPACA_API_KEY"]
        self.secret = secret_key or os.environ["ALPACA_SECRET_KEY"]
        self.base   = base_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID":     self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "Content-Type":        "application/json",
        }

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_account(self) -> dict[str, Any]:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self.base}/account", headers=self._headers, timeout=15)
            r.raise_for_status()
            return r.json()

    async def get_equity(self) -> float:
        acc = await self.get_account()
        return float(acc["equity"])

    # ── Positions ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        """Returns {ticker: position_dict}."""
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self.base}/positions", headers=self._headers, timeout=15)
            r.raise_for_status()
            return {p["symbol"]: p for p in r.json()}

    async def close_position(self, ticker: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as c:
            r = await c.delete(
                f"{self.base}/positions/{ticker}",
                headers=self._headers,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()

    # ── Orders ────────────────────────────────────────────────────────────────

    async def is_fractionable(self, ticker: str) -> bool:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self.base}/assets/{ticker}", headers=self._headers, timeout=15)
            if r.status_code != 200:
                return False
            return bool(r.json().get("fractionable", False))

    async def get_latest_price(self, ticker: str) -> float | None:
        """Get latest trade price from Alpaca data API."""
        headers = {k: v for k, v in self._headers.items() if k != "Content-Type"}
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{DATA_BASE}/v2/stocks/{ticker}/trades/latest",
                headers=headers,
                timeout=15,
            )
            if r.status_code != 200:
                return None
            return float(r.json().get("trade", {}).get("p", 0) or 0) or None

    async def market_buy(self, ticker: str, notional: float) -> dict[str, Any]:
        """Buy $notional worth of ticker at market. Uses qty for non-fractionable assets."""
        if notional < 1.0:
            raise ValueError(f"notional ${notional:.2f} too small (min $1)")
        fractionable = await self.is_fractionable(ticker)
        if fractionable:
            payload = {
                "symbol":        ticker,
                "notional":      f"{notional:.2f}",
                "side":          "buy",
                "type":          "market",
                "time_in_force": "day",
            }
        else:
            # Non-fractionable: calculate whole shares
            price = await self.get_latest_price(ticker)
            if not price:
                raise ValueError(f"Could not get price for {ticker}")
            qty = int(notional // price)
            if qty < 1:
                raise ValueError(f"${notional:.2f} not enough for 1 share of {ticker} @ ${price:.2f}")
            payload = {
                "symbol":        ticker,
                "qty":           str(qty),
                "side":          "buy",
                "type":          "market",
                "time_in_force": "day",
            }
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{self.base}/orders",
                headers=self._headers,
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()

    async def market_sell_qty(self, ticker: str, qty: float) -> dict[str, Any]:
        """Sell a specific qty of shares at market."""
        payload = {
            "symbol":        ticker,
            "qty":           f"{qty:.8f}",
            "side":          "sell",
            "type":          "market",
            "time_in_force": "day",
        }
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{self.base}/orders",
                headers=self._headers,
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()

    async def market_sell_notional(self, ticker: str, notional: float) -> dict[str, Any]:
        """Sell $notional worth of ticker at market (fractional)."""
        if notional < 1.0:
            raise ValueError(f"notional ${notional:.2f} too small (min $1)")
        payload = {
            "symbol":        ticker,
            "notional":      f"{notional:.2f}",
            "side":          "sell",
            "type":          "market",
            "time_in_force": "day",
        }
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{self.base}/orders",
                headers=self._headers,
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()

    # ── Market clock ─────────────────────────────────────────────────────────

    async def is_market_open(self) -> bool:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self.base}/clock", headers=self._headers, timeout=15)
            r.raise_for_status()
            return bool(r.json().get("is_open", False))
