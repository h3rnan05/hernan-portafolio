"""FRED (Federal Reserve Economic Data) provider.

Free, well-documented, rate-limit-friendly. The reliable backbone for all US
macro data plus FX rates plus Brent and Gold.

Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

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

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


class FREDProvider(Provider):
    """Pulls a single FRED series as a time series of DataPoints."""

    name = "fred"

    def __init__(self, api_key: str | None = None, timeout_s: float = 15.0) -> None:
        self.api_key = api_key or get_settings().fred_api_key
        if not self.api_key:
            raise ValueError("FRED_API_KEY is required to use FREDProvider")
        self.timeout_s = timeout_s

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        """Fetch daily observations for a FRED series_id.

        Note: many FRED series are monthly (CPI, UNRATE) or quarterly. We pass
        them through unchanged — downstream code handles frequency alignment.
        """
        params = {
            "series_id": symbol,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(FRED_BASE_URL, params=params)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"FRED timeout for {symbol}: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"FRED request error for {symbol}: {e}") from e

        if resp.status_code == 429:
            raise ProviderRateLimited(f"FRED 429 for {symbol}")
        if resp.status_code == 400:
            # FRED returns 400 for unknown series — treat as empty, not error
            log.warning("fred_unknown_series", symbol=symbol, body=resp.text[:200])
            return []
        if resp.status_code != 200:
            raise ProviderError(
                f"FRED unexpected status {resp.status_code} for {symbol}: {resp.text[:200]}"
            )

        payload = resp.json()
        observations = payload.get("observations", [])

        out: list[DataPoint] = []
        for obs in observations:
            # FRED uses "." for missing values
            raw = obs.get("value", ".")
            if raw == "." or raw is None:
                continue
            try:
                out.append(
                    DataPoint(
                        observed_on=date.fromisoformat(obs["date"]),
                        value=float(raw),
                    )
                )
            except (ValueError, KeyError) as e:
                log.warning("fred_bad_obs", symbol=symbol, obs=obs, err=str(e))
                continue

        log.debug("fred_fetched", symbol=symbol, count=len(out), start=start, end=end)
        return out
