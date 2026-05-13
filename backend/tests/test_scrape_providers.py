"""Tests for the BDI + ISM PMI scrapers.

These exercise the regex extractors against representative HTML snippets so
the parsing logic stays correct as the upstream pages drift.
"""

from datetime import date

import httpx
import pytest
import respx

from app.ingestion.baltic import BalticDryIndexProvider, _parse_bdi
from app.ingestion.ism_pmi import ISMManufacturingPMIProvider, _parse_pmi

# ─── BDI ────────────────────────────────────────────────────────────────────


def test_bdi_parses_id_p_anchor() -> None:
    html = '<span class="big" id="p" data-x="1">1,452</span>'
    assert _parse_bdi(html) == 1452.0


def test_bdi_parses_meta_description_fallback() -> None:
    html = (
        '<meta name="description" content="Baltic Dry 1,287 today, source for shipping">'
    )
    assert _parse_bdi(html) == 1287.0


def test_bdi_rejects_out_of_range_value() -> None:
    """Sanity check — random three-digit numbers shouldn't pass as BDI."""
    html = '<span id="p">42</span>'
    assert _parse_bdi(html) is None


@respx.mock
async def test_bdi_provider_returns_one_point() -> None:
    html = '<html><span id="p" class="big">1,538</span></html>'
    respx.get("https://tradingeconomics.com/commodity/baltic").mock(
        return_value=httpx.Response(200, text=html)
    )
    bdi = BalticDryIndexProvider()
    pts = await bdi.fetch("BDIY", date(2026, 1, 1), date(2026, 1, 5))
    assert len(pts) == 1
    assert pts[0].value == 1538.0
    assert pts[0].observed_on == date(2026, 1, 5)


@respx.mock
async def test_bdi_unparseable_returns_empty() -> None:
    respx.get("https://tradingeconomics.com/commodity/baltic").mock(
        return_value=httpx.Response(200, text="<html>page changed</html>")
    )
    bdi = BalticDryIndexProvider()
    assert await bdi.fetch("BDIY", date(2026, 1, 1), date(2026, 1, 5)) == []


# ─── ISM PMI ────────────────────────────────────────────────────────────────


def test_pmi_parses_strong_anchor() -> None:
    html = (
        "<p>The Manufacturing PMI&reg; registered "
        "<strong>49.2</strong> percent in October.</p>"
    )
    assert _parse_pmi(html) == 49.2


def test_pmi_parses_phrase_fallback() -> None:
    html = "Manufacturing PMI 51.3 percent (a one-month rebound)."
    assert _parse_pmi(html) == 51.3


def test_pmi_rejects_out_of_range_value() -> None:
    html = "<strong>150.0</strong> PMI"
    assert _parse_pmi(html) is None


@respx.mock
async def test_ism_provider_returns_one_point() -> None:
    html = "<p>PMI &reg; <strong>50.4</strong> percent</p>"
    respx.get(
        "https://www.ismworld.org/supply-management-news-and-reports/"
        "reports/ism-report-on-business/pmi/"
    ).mock(return_value=httpx.Response(200, text=html))

    ism = ISMManufacturingPMIProvider()
    pts = await ism.fetch("PMI", date(2026, 1, 1), date(2026, 1, 5))
    assert len(pts) == 1
    assert pts[0].value == 50.4
    assert pts[0].observed_on == date(2026, 1, 5)


@respx.mock
async def test_ism_unparseable_returns_empty() -> None:
    respx.get(
        "https://www.ismworld.org/supply-management-news-and-reports/"
        "reports/ism-report-on-business/pmi/"
    ).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    ism = ISMManufacturingPMIProvider()
    assert await ism.fetch("PMI", date(2026, 1, 1), date(2026, 1, 5)) == []


@pytest.mark.parametrize("status", [500, 502, 503])
@respx.mock
async def test_ism_5xx_returns_empty(status: int) -> None:
    respx.get(
        "https://www.ismworld.org/supply-management-news-and-reports/"
        "reports/ism-report-on-business/pmi/"
    ).mock(return_value=httpx.Response(status, text="upstream"))
    ism = ISMManufacturingPMIProvider()
    assert await ism.fetch("PMI", date(2026, 1, 1), date(2026, 1, 5)) == []
