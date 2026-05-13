"""Polygon.io provider — fallback for the 9 US portfolio stocks.

Free tier: 5 req/min, EOD only, ~2-year history. Plenty for 9 daily pulls.

Endpoint:
    GET https://api.polygon.io/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?apiKey=KEY

Response (JSON):
    {
      "ticker": "NVDA",
      "results": [
        { "t": 1735689600000,  # ms since epoch (UTC midnight)
          "o": 134.5, "h": 137.0, "l": 134.0, "c": 136.2, "v": 12345678 },
        ...
      ],
      "status": "OK"
    }
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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

POLYGON_BASE_URL = "https://api.polygon.io/v2/aggs/ticker"


class PolygonProvider(Provider):
    """Fetches daily aggregates from Polygon's free-tier REST API."""

    name = "polygon"

    def __init__(self, api_key: str | None = None, timeout_s: float = 15.0) -> None:
        self.api_key = api_key if api_key is not None else get_settings().polygon_api_key
        # Empty key is permitted at construction so the runner can still load
        # the provider; fetch() will short-circuit with an informative warning.
        self.timeout_s = timeout_s

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        if not self.api_key:
            log.warning("polygon_no_api_key", symbol=symbol)
            return []

        url = (
            f"{POLYGON_BASE_URL}/{symbol}/range/1/day/"
            f"{start.isoformat()}/{end.isoformat()}"
        )
        params = {
            "apiKey": self.api_key,
            "adjusted": "true",
            "sort": "asc",
            "limit": "5000",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(url, params=params)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Polygon timeout for {symbol}: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"Polygon request error for {symbol}: {e}") from e

        if resp.status_code == 429:
            raise ProviderRateLimited(f"Polygon 429 for {symbol}")
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"Polygon auth error {resp.status_code} for {symbol} — check POLYGON_API_KEY"
            )
        if resp.status_code == 404:
            log.warning("polygon_unknown_symbol", symbol=symbol)
            return []
        if resp.status_code != 200:
            raise ProviderError(
                f"Polygon unexpected status {resp.status_code} for {symbol}: "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise ProviderError(f"Polygon non-JSON response for {symbol}: {e}") from e

        # Free tier returns DELAYED status with results; treat any non-ERROR as usable
        status = (payload.get("status") or "").upper()
        if status == "ERROR":
            log.warning(
                "polygon_error_status",
                symbol=symbol,
                error=payload.get("error"),
                message=payload.get("message"),
            )
            return []

        results = payload.get("results") or []
        out: list[DataPoint] = []
        for row in results:
            try:
                ts_ms = int(row["t"])
                close = float(row["c"])
            except (KeyError, TypeError, ValueError) as e:
                log.warning("polygon_bad_row", symbol=symbol, row=row, err=str(e))
                continue
            d = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).date()
            out.append(DataPoint(observed_on=d, value=close))

        log.debug("polygon_fetched", symbol=symbol, count=len(out))
        return out
