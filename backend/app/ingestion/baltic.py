"""Baltic Dry Index scraper.

The BDI has no free public API. This provider scrapes
``https://tradingeconomics.com/commodity/baltic`` once per day, regex-extracts
the headline number from the page's structured data, and validates the value
against a sane range (200–20000) before returning.

Failure modes (HTTP error, parsing error, schema check) all map to
ProviderNotFound semantics (return []) so the fallback chain proceeds; the
runner logs a warning and ingestion continues.

For production-grade reliability, swap to ETF proxy ``BDRY`` via Polygon —
correlated to BDI, not identical, but on a maintained data feed.
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

BDI_URL = "https://tradingeconomics.com/commodity/baltic"
_BDI_MIN, _BDI_MAX = 200, 20000


# tradingeconomics renders the headline number inside the page header in
# multiple places. The most stable anchor is the ``id="p"`` price span; if
# that ever moves, fall back to the OG meta description tag.
_PRICE_PATTERNS = [
    re.compile(r'id="p"[^>]*>([0-9][0-9,\.]*)', re.IGNORECASE),
    re.compile(r'<meta\s+name="description"\s+content="[^"]*?(\d[\d,]+(?:\.\d+)?)\s', re.IGNORECASE),
]


def _parse_bdi(html: str) -> float | None:
    for pat in _PRICE_PATTERNS:
        m = pat.search(html)
        if not m:
            continue
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if _BDI_MIN <= v <= _BDI_MAX:
            return v
    return None


class BalticDryIndexProvider(Provider):
    """Single-value scraper for the Baltic Dry Index (daily, end-of-day)."""

    name = "scrape_baltic"

    def __init__(self, timeout_s: float = 15.0) -> None:
        self.timeout_s = timeout_s
        self.user_agent = get_settings().http_user_agent

    async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]:
        # The page only carries the latest value — symbol & date range are ignored.
        # We label the observation with `end` (today's ingestion target).
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, follow_redirects=True
            ) as client:
                resp = await client.get(BDI_URL, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderTimeout(f"BDI scrape timeout: {e}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"BDI scrape request error: {e}") from e

        if resp.status_code == 429:
            raise ProviderRateLimited("BDI scrape 429")
        if resp.status_code != 200:
            log.warning("bdi_scrape_status", status=resp.status_code)
            return []

        value = _parse_bdi(resp.text)
        if value is None:
            log.warning("bdi_parse_failed", body_preview=resp.text[:200])
            return []

        log.debug("bdi_scraped", value=value, observed_on=end)
        return [DataPoint(observed_on=end, value=value)]
