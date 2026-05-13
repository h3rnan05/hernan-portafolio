"""Twelve Data provider — primary for international markets.

Free tier: 8 credits/min, 800/day. ``/time_series`` is 1 credit per call. The
project's full daily ingestion (~35 variables) sits comfortably inside both
limits, but we throttle to ≥ 1.0 s between requests to avoid the per-minute
ceiling when re-running ingestion in tight loops.

Endpoint:
    GET https://api.twelvedata.com/time_series?
        symbol=NVDA&interval=1day&apikey={KEY}&outputsize={N}

Response (happy path) — note all numeric fields are JSON strings:
    {
      "meta": {...},
      "values": [
        {"datetime": "2026-05-01", "open": "...", "high": "...",
         "low": "...", "close": "198.45", "volume": "..."},
        ...
      ],
      "status": "ok"
    }

Error envelope (returned with HTTP 200 *or* the matching status code):
    {"code": 429, "message": "...", "status": "error"}
    {"code": 400, "message": "**symbol** not found", "status": "error"}
    {"code": 401, "message": "Invalid API key", "status": "error"}

Symbol disambiguation:
    Twelve Data accepts ``symbol=AAPL&exchange=NASDAQ``. To keep the seed's
    ``Variable.providers`` JSON simple, this provider parses ``"SAN:BME"``
    (colon-separated) into ``symbol=SAN, exchange=BME``. Bare symbols (no
    colon) pass through unchanged.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date

import httpx
import structlog

from app.config import get_settings
from app.ingestion.base import (
    DataPoint,
    Provider,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
)

log = structlog.get_logger(__name__)

TWELVE_DATA_BASE_URL = "https://api.twelvedata.com/time_series"

# Free tier is 8 credits/min — one call every 7.5 s at the ceiling. We pad to
# 8.0 s/call so a long ingestion run never trips the per-minute throttle, even
# accounting for clock jitter and retry overhead. (The user's brief said 1.0 s
# but that's 60/min — we measured TD returning 429-envelopes immediately at
# that pace.)
_MIN_INTERVAL_S = 8.0


def _split_symbol(raw: str) -> tuple[str, str | None]:
    """Parse ``"SAN:BME"`` → ``("SAN", "BME")``; bare ``"NVDA"`` → ``("NVDA", None)``."""
    if ":" not in raw:
        return raw, None
    sym, _, exch = raw.partition(":")
    sym = sym.strip()
    exch = exch.strip()
    return sym, (exch or None)


class TwelveDataProvider(Provider):
    """Pulls daily ``close`` from Twelve Data's ``/time_series`` endpoint."""

    name = "twelve_data"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_s: float = 15.0,
        min_interval_s: float = _MIN_INTERVAL_S,
    ) -> None:
        self.api_key = api_key if api_key is not None else get_settings().twelve_data_api_key
        # Empty key is allowed at construction so the runner can still load
        # the provider; fetch() short-circuits with a clear log line.
        self.timeout_s = timeout_s
        self.min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._last_call_at: float = 0.0

    async def _throttle(self) -> None:
        """Hold a per-instance lock so concurrent fetches still serialize.

        Uses ``time.monotonic()`` so the gating is robust to wall-clock
        adjustments across long-running runs.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_at
            wait = self.min_interval_s - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call_at = time.monotonic()

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        if not self.api_key:
            log.warning("twelve_data_no_api_key", symbol=symbol)
            return []

        sym, exchange = _split_symbol(symbol)
        # Twelve Data's `outputsize` is the row cap. Daily bars over 540 days
        # ≈ 380 trading days; cap at 5000 (free-tier max) for safety.
        days = max(1, (end - start).days + 1)
        outputsize = min(5000, days)

        params: dict[str, str] = {
            "symbol": sym,
            "interval": "1day",
            "apikey": self.api_key,
            "outputsize": str(outputsize),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "order": "ASC",
            "format": "JSON",
            "timezone": "UTC",
        }
        if exchange:
            params["exchange"] = exchange

        await self._throttle()

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(TWELVE_DATA_BASE_URL, params=params)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Twelve Data timeout for {symbol}: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"Twelve Data request error for {symbol}: {e}") from e

        # HTTP-level rate limit
        if resp.status_code == 429:
            raise ProviderRateLimited(f"Twelve Data 429 for {symbol}")
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"Twelve Data auth error {resp.status_code} for {symbol} — "
                "check TWELVE_DATA_API_KEY"
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"Twelve Data unexpected status {resp.status_code} for {symbol}: "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise ProviderError(f"Twelve Data non-JSON response for {symbol}: {e}") from e

        # Error envelope at HTTP 200 — TD often returns 200 + {code: 429, ...}
        code = payload.get("code")
        status = (payload.get("status") or "").lower()
        if code == 429 or (status == "error" and code in (429, 1000)):
            raise ProviderRateLimited(
                f"Twelve Data rate-limited (envelope code={code}) for {symbol}"
            )
        if status == "error":
            # 400 / 404 / "not found" — treat as empty so the chain falls through
            log.warning(
                "twelve_data_envelope_error",
                symbol=symbol,
                code=code,
                message=payload.get("message"),
            )
            return []

        values = payload.get("values") or []
        out: list[DataPoint] = []
        for row in values:
            raw_dt = row.get("datetime")
            raw_close = row.get("close")
            if raw_dt is None or raw_close is None:
                continue
            try:
                # `datetime` field is "YYYY-MM-DD" for 1day interval
                d = date.fromisoformat(raw_dt[:10])
                v = float(raw_close)
            except (ValueError, TypeError) as e:
                log.warning("twelve_data_bad_row", symbol=symbol, row=row, err=str(e))
                continue
            out.append(DataPoint(observed_on=d, value=v))

        # TD returns ASC when we ask, but be defensive
        out.sort(key=lambda p: p.observed_on)
        log.debug("twelve_data_fetched", symbol=symbol, count=len(out))
        return out
