"""EOD Historical Data (EODHD) provider — primary global EOD source.

Free tier is too restrictive; this targets the paid "EOD Historical Data
— All World" plan ($19.99/mo) which covers every exchange the system needs:
LSE, XETRA, Euronext Paris, SIX Swiss, BME Madrid, Tokyo, HKEX, KRX,
NSE/BSE India, B3 São Paulo, plus commodities (Brent, WTI, copper, wheat)
under the ``.COMM`` virtual exchange and indices under ``.INDX``.

Endpoint:
    GET https://eodhd.com/api/eod/{TICKER}.{EXCHANGE}
        ?from=YYYY-MM-DD&to=YYYY-MM-DD&api_token=KEY&fmt=json&period=d

Response (happy path) is a JSON array of bars:
    [
      {"date": "2026-05-01",
       "open": 197.1, "high": 199.0, "low": 196.5,
       "close": 198.45, "adjusted_close": 198.45, "volume": 12345678},
      ...
    ]

Errors:
    * HTTP 401 / 403 — bad key (raises ProviderError)
    * HTTP 402 — plan doesn't cover the symbol (logged, returns [])
    * HTTP 404 — unknown symbol (logged, returns [])
    * HTTP 429 — rate-limited (raises ProviderRateLimited)
    * 200 with empty array — no data in range (returns [])

Symbol format conventions (already encoded in the seed):
    * US stocks      : ``NVDA.US``
    * LSE            : ``BARC.LSE``
    * XETRA          : ``BMW.XETRA``
    * Paris          : ``MC.PA``     (LVMH)
    * Madrid (BME)   : ``SAN.MC``    (Banco Santander)
    * Swiss (SIX)    : ``NESN.SW``   (Nestlé)
    * NSE India      : ``RELIANCE.NSE``
    * Tokyo          : ``7203.T``    (Toyota — not used here)
    * HKEX           : ``0700.HK``
    * Indices        : ``FTSE.INDX``, ``GDAXI.INDX``, ``N225.INDX`` …
    * Commodities    : ``BRENT.COMM``, ``WHEAT.COMM``, ``COPPER.COMM``
    * FX             : ``EURUSD.FOREX``
"""

from __future__ import annotations

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

EODHD_BASE_URL = "https://eodhd.com/api/eod"


class EODHDProvider(Provider):
    """Pulls daily ``close`` from EODHD's REST EOD endpoint."""

    name = "eodhd"

    def __init__(self, api_key: str | None = None, timeout_s: float = 20.0) -> None:
        self.api_key = api_key if api_key is not None else get_settings().eodhd_api_key
        # Empty key permitted at construction so the runner can still load the
        # provider; fetch() short-circuits with a clear log line.
        self.timeout_s = timeout_s

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        if not self.api_key:
            log.warning("eodhd_no_api_key", symbol=symbol)
            return []

        url = f"{EODHD_BASE_URL}/{symbol}"
        params = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "api_token": self.api_key,
            "fmt": "json",
            "period": "d",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(url, params=params)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"EODHD timeout for {symbol}: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"EODHD request error for {symbol}: {e}") from e

        if resp.status_code == 429:
            raise ProviderRateLimited(f"EODHD 429 for {symbol}")
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"EODHD auth error {resp.status_code} for {symbol} — check EODHD_API_KEY"
            )
        if resp.status_code == 402:
            # Plan doesn't cover the symbol — let the chain fall through
            log.warning(
                "eodhd_plan_required", symbol=symbol, body_preview=resp.text[:200]
            )
            return []
        if resp.status_code == 404:
            log.warning("eodhd_unknown_symbol", symbol=symbol)
            return []
        if resp.status_code != 200:
            raise ProviderError(
                f"EODHD unexpected status {resp.status_code} for {symbol}: "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as e:
            raise ProviderError(f"EODHD non-JSON response for {symbol}: {e}") from e

        # EODHD sometimes returns ``{"errors": ...}`` at HTTP 200 instead of an array
        if isinstance(payload, dict):
            err = payload.get("errors") or payload.get("error")
            if err:
                log.warning("eodhd_envelope_error", symbol=symbol, error=err)
                return []
            # Empty-results envelope variant
            return []

        if not isinstance(payload, list):
            log.warning("eodhd_unexpected_payload", symbol=symbol, type=type(payload).__name__)
            return []

        out: list[DataPoint] = []
        for row in payload:
            try:
                raw_dt = row["date"]
                # Prefer adjusted_close when present (corporate-action adjusted);
                # fall back to close.
                raw_close = row.get("adjusted_close")
                if raw_close is None or raw_close == 0:
                    raw_close = row["close"]
                d = date.fromisoformat(raw_dt[:10])
                v = float(raw_close)
            except (KeyError, ValueError, TypeError) as e:
                log.warning("eodhd_bad_row", symbol=symbol, row=row, err=str(e))
                continue
            out.append(DataPoint(observed_on=d, value=v))

        out.sort(key=lambda p: p.observed_on)
        log.debug("eodhd_fetched", symbol=symbol, count=len(out))
        return out
