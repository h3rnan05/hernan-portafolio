"""yfinance / Yahoo Finance provider.

Free fallback for international indices, FX, futures, and US stocks. Implemented
against Yahoo's public Chart API directly (the same endpoint the ``yfinance``
package wraps under the hood) — keeps the codebase async-only and lets us mock
with respx like every other provider.

Endpoint: ``https://query1.finance.yahoo.com/v8/finance/chart/{symbol}``

Symbol conventions used in the seed (``Variable.providers``):
    * Indices use a ``^`` prefix (``^FTSE``, ``^GDAXI``, ``^N225``, ``^HSI``, …)
    * European stocks suffix the venue (``SAN.MC``, ``MC.PA``, ``NESN.SW``)
    * Indian stocks use ``.NS`` (``RELIANCE.NS``)
    * FX uses the ``=X`` suffix (``EURJPY=X``)
    * Commodity futures use ``=F`` (``ZW=F``, ``HG=F``)
    * US stocks are bare tickers (``NVDA``)

Yahoo aggressively rate-limits anonymous traffic. The runner adds jitter
between calls and treats HTTP 429/999 as ProviderRateLimited so the chain can
fall through. Don't run this provider in tight loops.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

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

YF_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


def _to_unix_ts(d: date, end_of_day: bool = False) -> int:
    """Yahoo's `period1`/`period2` are Unix seconds at UTC midnight (or +1d for end)."""
    t = time(23, 59, 59) if end_of_day else time(0, 0, 0)
    return int(datetime.combine(d, t, tzinfo=UTC).timestamp())


class YFinanceProvider(Provider):
    """Pulls daily close prices from Yahoo Finance's Chart API."""

    name = "yfinance"

    def __init__(self, timeout_s: float = 20.0) -> None:
        self.timeout_s = timeout_s
        self.user_agent = get_settings().http_user_agent

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        url = f"{YF_BASE_URL}{symbol}"
        params = {
            "period1": str(_to_unix_ts(start)),
            "period2": str(_to_unix_ts(end, end_of_day=True)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"yfinance timeout for {symbol}: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"yfinance request error for {symbol}: {e}") from e

        # Yahoo uses 429 + the unofficial 999 (Edge throttle) for rate limiting
        if resp.status_code in (429, 999):
            raise ProviderRateLimited(f"yfinance {resp.status_code} for {symbol}")
        if resp.status_code == 404:
            log.warning("yfinance_unknown_symbol", symbol=symbol)
            return []
        if resp.status_code != 200:
            raise ProviderError(
                f"yfinance unexpected status {resp.status_code} for {symbol}: "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise ProviderError(f"yfinance non-JSON response for {symbol}: {e}") from e

        chart = (payload or {}).get("chart") or {}
        err = chart.get("error")
        if err:
            # Yahoo returns 200 with {"error":{"code":"Not Found"}} for bad symbols
            log.warning("yfinance_chart_error", symbol=symbol, err=err)
            return []

        results = chart.get("result") or []
        if not results:
            return []

        result = results[0]
        timestamps: list[int] = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quote_blocks = indicators.get("quote") or []
        if not timestamps or not quote_blocks:
            return []

        closes = quote_blocks[0].get("close") or []
        out: list[DataPoint] = []
        for ts, close in zip(timestamps, closes, strict=False):
            if close is None:
                continue
            try:
                d = datetime.fromtimestamp(int(ts), tz=UTC).date()
                out.append(DataPoint(observed_on=d, value=float(close)))
            except (TypeError, ValueError) as e:
                log.warning("yfinance_bad_row", symbol=symbol, ts=ts, close=close, err=str(e))
                continue

        log.debug("yfinance_fetched", symbol=symbol, count=len(out))
        return out
