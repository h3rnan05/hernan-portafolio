"""Stooq provider — primary source for international stocks/indices/commodities.

Stooq is a Polish data provider that exposes free CSV downloads. No API key,
no auth, no rate limit issues at low volume. Symbols are idiosyncratic and
the agent should verify each one manually during Phase 1 onboarding.

URL pattern (verified working as of 2026):
    https://stooq.com/q/d/l/?s={symbol}&i=d&d1={YYYYMMDD}&d2={YYYYMMDD}

Returns a CSV with columns: Date,Open,High,Low,Close,Volume

Symbol conventions:
    - US stocks:           {ticker}.us       (e.g. nvda.us)
    - Indices:             ^{code}            (e.g. ^ftm, ^dax, ^cac)
    - Spanish stocks:      {ticker}.es       (e.g. san.es)
    - French stocks:       {ticker}.fr       (e.g. mc.fr)
    - Swiss stocks:        {ticker}.ch       (e.g. nesn.ch)
    - Indian stocks:       {ticker}.in       (e.g. reliance.in)
    - Commodity futures:   {ticker}.f         (e.g. zw.f, hg.f)
    - FX:                  {pair}             (e.g. eurusd, usdmxn)

If the response is empty, contains "No data", or returns a too-short CSV,
treat as ProviderNotFound (not an error) and let the runner try the next
provider in the chain.

NOTE (2026-05): Stooq now paywalls bulk CSV downloads behind a captcha-derived
``apikey`` query parameter. Without one, the CSV endpoint returns the message
"Get your apikey:" and the provider yields no data. We detect that case and
return [] so the fallback chain proceeds to yfinance/polygon. Set
``STOOQ_API_KEY`` in env once the key is obtained from
https://stooq.com/q/d/?s=nvda.us&get_apikey (manual captcha).
"""

import csv
import io
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

STOOQ_BASE_URL = "https://stooq.com/q/d/l/"
_APIKEY_GATE_MARKER = "get your apikey"


class StooqProvider(Provider):
    """Fetches daily OHLCV from Stooq's CSV endpoint.

    Free without key for low-volume probes historically; as of 2026 the bulk
    CSV download is gated by an ``apikey`` parameter the user has to fetch via
    a captcha at https://stooq.com/q/d/?s={sym}&get_apikey . If the env var is
    unset we still hit the endpoint — Stooq returns a captcha-prompt message
    which we treat as ProviderNotFound, and the runner falls through.
    """

    name = "stooq"

    def __init__(self, timeout_s: float = 20.0, api_key: str | None = None) -> None:
        settings = get_settings()
        self.timeout_s = timeout_s
        self.user_agent = settings.http_user_agent
        # api_key arg overrides settings; both can be empty
        self.api_key = api_key if api_key is not None else getattr(settings, "stooq_api_key", "")

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        params: dict[str, str] = {
            "s": symbol,
            "i": "d",
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
        }
        if self.api_key:
            params["apikey"] = self.api_key
        headers = {"User-Agent": self.user_agent}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(STOOQ_BASE_URL, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"Stooq timeout for {symbol}: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"Stooq request error for {symbol}: {e}") from e

        if resp.status_code == 429:
            raise ProviderRateLimited(f"Stooq 429 for {symbol}")
        if resp.status_code != 200:
            raise ProviderError(
                f"Stooq unexpected status {resp.status_code} for {symbol}"
            )

        text = resp.text.strip()
        lower = text.lower()
        if (
            not text
            or "no data" in lower
            or _APIKEY_GATE_MARKER in lower
            or text.startswith("<")
        ):
            if _APIKEY_GATE_MARKER in lower:
                log.warning(
                    "stooq_apikey_gate",
                    symbol=symbol,
                    hint="set STOOQ_API_KEY in env (captcha at stooq.com/q/d/?get_apikey)",
                )
            else:
                log.warning("stooq_no_data", symbol=symbol, body_preview=text[:120])
            return []

        out: list[DataPoint] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            try:
                out.append(
                    DataPoint(
                        observed_on=date.fromisoformat(row["Date"]),
                        value=float(row["Close"]),
                    )
                )
            except (ValueError, KeyError) as e:
                log.warning("stooq_bad_row", symbol=symbol, row=row, err=str(e))
                continue

        log.debug("stooq_fetched", symbol=symbol, count=len(out))
        return out
