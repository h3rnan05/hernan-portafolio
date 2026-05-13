"""ISM Manufacturing PMI scraper.

ISM publishes the Manufacturing PMI on the 1st business day of each month.
The free public page is ``https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/pmi/``.

Strategy:
  1. Hit the page with a real-looking User-Agent + Accept headers.
  2. Regex-extract the headline number — values fall within a tight band (~30–80)
     which makes a simple sanity check effective.
  3. Map the latest reading to the *current* day's date label (we don't have a
     reliable publish-date stamp on the page; predictions only need monotonic
     ordering).

If parsing fails the provider returns [] and the fallback chain skips. There's
no second-tier free PMI source bundled in this scaffold; brief §2.2 Bucket C
references S&P Global PMI as a manual upgrade path.
"""

from __future__ import annotations

import re
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

ISM_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "reports/ism-report-on-business/pmi/"
)
_PMI_MIN, _PMI_MAX = 20.0, 90.0

# The ISM page renders the headline as e.g. ``<strong>49.0</strong> percent``
# next to "PMI®". Anchored regex avoids picking up unrelated numbers.
_PMI_PATTERNS = [
    re.compile(
        r"PMI[^<]{0,40}?<[^>]+>\s*([0-9]{2}\.[0-9])\s*<",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"Manufacturing PMI[^<]{0,80}?([0-9]{2}\.[0-9])\s*(?:percent|%)",
        re.IGNORECASE | re.DOTALL,
    ),
]


def _parse_pmi(html: str) -> float | None:
    for pat in _PMI_PATTERNS:
        m = pat.search(html)
        if not m:
            continue
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if _PMI_MIN <= v <= _PMI_MAX:
            return v
    return None


class ISMManufacturingPMIProvider(Provider):
    """Monthly PMI scraper. Cron should call this at most once per business day."""

    name = "scrape_ism"

    def __init__(self, timeout_s: float = 15.0) -> None:
        self.timeout_s = timeout_s
        self.user_agent = get_settings().http_user_agent

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, follow_redirects=True
            ) as client:
                resp = await client.get(ISM_URL, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"ISM PMI scrape timeout: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"ISM PMI scrape request error: {e}") from e

        if resp.status_code == 429:
            raise ProviderRateLimited("ISM PMI scrape 429")
        if resp.status_code != 200:
            log.warning("ism_pmi_status", status=resp.status_code)
            return []

        value = _parse_pmi(resp.text)
        if value is None:
            log.warning("ism_pmi_parse_failed", body_preview=resp.text[:200])
            return []

        log.debug("ism_pmi_scraped", value=value, observed_on=end)
        return [DataPoint(observed_on=end, value=value)]
