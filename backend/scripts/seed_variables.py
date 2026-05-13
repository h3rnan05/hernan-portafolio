"""Seed the `variables` table with all 30 predictors + 9 portfolio stocks.

Each variable carries a `providers` JSON array — the fallback chain.
Run after `alembic upgrade head`:

    uv run python scripts/seed_variables.py

Idempotent: re-running upserts existing rows.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import AsyncSessionLocal
from app.logging import setup_logging
from app.models import Variable

setup_logging()
log = structlog.get_logger(__name__)


# ─── The full registry ──────────────────────────────────────────────────────
# Format: (id, display_name, kind, category, unit, providers)
# Provider chains are ordered by preference. Symbols marked TODO need manual
# verification on Stooq (see brief §2.2 — symbol-resolution discipline).

VARIABLES: list[tuple[str, str, str, str, str, list[dict]]] = [
    # ─── Bucket A: FRED — published US macro indicators (10) ────────────────
    (
        "Baltic_Dry_Index",
        "Baltic Dry Index",
        "predictor",
        "Published Index",
        "Index",
        # No FRED series — agent implements scraper in Phase 1
        [{"name": "scrape_baltic", "symbol": "BDIY"}],
    ),
    (
        "Citi_Econ_Surprise_US",
        "ADS Business Conditions Index (proxy for Citi Surprise)",
        "predictor",
        "Published Index",
        "Index",
        # ADS Index — Aruoba-Diebold-Scotti, free FRED substitute (brief §2.2 Bucket C)
        [{"name": "fred", "symbol": "ADSBCISMV"}],
    ),
    (
        "ISM_Manufacturing_PMI",
        "ISM Manufacturing PMI",
        "predictor",
        "Published Index",
        "Index",
        # No FRED series — agent implements scraper for ismworld.org
        [{"name": "scrape_ism", "symbol": "PMI"}],
    ),
    (
        "Consumer_Confidence",
        "UMich Consumer Sentiment",
        "predictor",
        "Published Index",
        "Index",
        [{"name": "fred", "symbol": "UMCSENT"}],
    ),
    (
        "CPI_YoY_US",
        "CPI Year-over-Year (US)",
        "predictor",
        "Published Index",
        "%",
        [{"name": "fred", "symbol": "CPIAUCSL"}],  # YoY computed downstream
    ),
    (
        "Unemployment_Rate_US",
        "US Unemployment Rate",
        "predictor",
        "Published Index",
        "%",
        [{"name": "fred", "symbol": "UNRATE"}],
    ),
    (
        "Industrial_Prod_Index",
        "US Industrial Production Index",
        "predictor",
        "Published Index",
        "Index",
        [{"name": "fred", "symbol": "INDPRO"}],
    ),
    (
        "Building_Permits_US",
        "US Building Permits",
        "predictor",
        "Published Index",
        "Thousands",
        [{"name": "fred", "symbol": "PERMIT"}],
    ),
    (
        "PPI_YoY_US",
        "PPI Year-over-Year (US)",
        "predictor",
        "Published Index",
        "%",
        [{"name": "fred", "symbol": "PPIACO"}],
    ),
    (
        "Conference_Board_LEI",
        "Conference Board LEI proxy",
        "predictor",
        "Published Index",
        "Index",
        [{"name": "fred", "symbol": "USSLIND"}],
    ),
    # ─── Bucket B: International indices (8) — Twelve Data primary ──────────
    # NOTE on TD symbols: "SYM:EXCHANGE" disambiguates when needed — the
    # TwelveDataProvider parses the suffix into a separate ?exchange= param.
    # Bare symbols (no colon) pass through unchanged.
    (
        "FTSE_100",
        "FTSE 100",
        "predictor",
        "UK Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "UKX"},
            {"name": "yfinance", "symbol": "^FTSE"},
        ],
    ),
    (
        "DAX",
        "DAX",
        "predictor",
        "Germany Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "DAX"},
            {"name": "yfinance", "symbol": "^GDAXI"},
        ],
    ),
    (
        "CAC_40",
        "CAC 40",
        "predictor",
        "France Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "CAC"},
            {"name": "yfinance", "symbol": "^FCHI"},
        ],
    ),
    (
        "Nikkei_225",
        "Nikkei 225",
        "predictor",
        "Japan Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "N225"},
            {"name": "yfinance", "symbol": "^N225"},
        ],
    ),
    (
        "Hang_Seng",
        "Hang Seng Index",
        "predictor",
        "HK Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "HSI"},
            {"name": "yfinance", "symbol": "^HSI"},
        ],
    ),
    (
        "Bovespa",
        "Bovespa",
        "predictor",
        "Brazil Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "BVSP"},
            {"name": "yfinance", "symbol": "^BVSP"},
        ],
    ),
    (
        "KOSPI",
        "KOSPI",
        "predictor",
        "Korea Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "KS11"},
            {"name": "yfinance", "symbol": "^KS11"},
        ],
    ),
    (
        "BSE_Sensex",
        "BSE Sensex",
        "predictor",
        "India Index",
        "Points",
        [
            {"name": "twelve_data", "symbol": "SENSEX"},
            {"name": "yfinance", "symbol": "^BSESN"},
        ],
    ),
    # ─── FX rates (4) — FRED primary ────────────────────────────────────────
    (
        "EUR_USD",
        "EUR/USD",
        "predictor",
        "FX Rate",
        "Rate",
        [
            {"name": "fred", "symbol": "DEXUSEU"},
            {"name": "twelve_data", "symbol": "EUR/USD"},
        ],
    ),
    (
        "EUR_JPY",
        "EUR/JPY",
        "predictor",
        "FX Rate",
        "Rate",
        # FRED has no direct EUR/JPY series — TD primary
        [
            {"name": "twelve_data", "symbol": "EUR/JPY"},
            {"name": "yfinance", "symbol": "EURJPY=X"},
        ],
    ),
    (
        "USD_MXN",
        "USD/MXN",
        "predictor",
        "FX Rate",
        "Rate",
        [
            {"name": "fred", "symbol": "DEXMXUS"},
            {"name": "twelve_data", "symbol": "USD/MXN"},
        ],
    ),
    (
        "USD_CNY",
        "USD/CNY",
        "predictor",
        "FX Rate",
        "Rate",
        [
            {"name": "fred", "symbol": "DEXCHUS"},
            {"name": "twelve_data", "symbol": "USD/CNY"},
        ],
    ),
    # ─── Commodities (4) ────────────────────────────────────────────────────
    (
        "Gold_Spot",
        "Gold (London PM Fix)",
        "predictor",
        "Commodity",
        "USD/oz",
        [
            {"name": "fred", "symbol": "GOLDAMGBD228NLBM"},
            {"name": "twelve_data", "symbol": "XAU/USD"},
        ],
    ),
    (
        "Brent_Crude",
        "Brent Crude Oil",
        "predictor",
        "Commodity",
        "USD/bbl",
        [
            {"name": "fred", "symbol": "DCOILBRENTEU"},
            {"name": "twelve_data", "symbol": "BRENT"},
        ],
    ),
    (
        "Wheat_Futures",
        "Wheat Futures",
        "predictor",
        "Commodity",
        "USD/bu",
        [
            {"name": "twelve_data", "symbol": "WHEAT"},
            {"name": "yfinance", "symbol": "ZW=F"},
        ],
    ),
    (
        "Copper_Futures",
        "Copper Futures",
        "predictor",
        "Commodity",
        "USD/lb",
        [
            {"name": "twelve_data", "symbol": "COPPER"},
            {"name": "yfinance", "symbol": "HG=F"},
        ],
    ),
    # ─── International stocks (4) ───────────────────────────────────────────
    (
        "Banco_Santander_MAD",
        "Banco Santander (Madrid)",
        "predictor",
        "Madrid Stock",
        "EUR",
        [
            {"name": "twelve_data", "symbol": "SAN:BME"},
            {"name": "yfinance", "symbol": "SAN.MC"},
        ],
    ),
    (
        "LVMH_PAR",
        "LVMH (Paris)",
        "predictor",
        "Paris Stock",
        "EUR",
        [
            {"name": "twelve_data", "symbol": "MC:Euronext"},
            {"name": "yfinance", "symbol": "MC.PA"},
        ],
    ),
    (
        "Nestle_SWX",
        "Nestlé (Swiss)",
        "predictor",
        "Swiss Stock",
        "CHF",
        [
            {"name": "twelve_data", "symbol": "NESN:SIX"},
            {"name": "yfinance", "symbol": "NESN.SW"},
        ],
    ),
    (
        "Reliance_NSE",
        "Reliance Industries (NSE)",
        "predictor",
        "India Stock",
        "INR",
        [
            {"name": "twelve_data", "symbol": "RELIANCE:NSE"},
            {"name": "yfinance", "symbol": "RELIANCE.NS"},
        ],
    ),
    # ─── Portfolio stocks (9) — Polygon primary, Twelve Data + yfinance fallbacks
    (
        "NVDA",
        "NVIDIA",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "NVDA"},
            {"name": "twelve_data", "symbol": "NVDA"},
            {"name": "yfinance", "symbol": "NVDA"},
        ],
    ),
    (
        "XOM",
        "ExxonMobil",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "XOM"},
            {"name": "twelve_data", "symbol": "XOM"},
            {"name": "yfinance", "symbol": "XOM"},
        ],
    ),
    (
        "CAT",
        "Caterpillar",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "CAT"},
            {"name": "twelve_data", "symbol": "CAT"},
            {"name": "yfinance", "symbol": "CAT"},
        ],
    ),
    (
        "AMZN",
        "Amazon",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "AMZN"},
            {"name": "twelve_data", "symbol": "AMZN"},
            {"name": "yfinance", "symbol": "AMZN"},
        ],
    ),
    (
        "CRM",
        "Salesforce",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "CRM"},
            {"name": "twelve_data", "symbol": "CRM"},
            {"name": "yfinance", "symbol": "CRM"},
        ],
    ),
    (
        "QCOM",
        "Qualcomm",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "QCOM"},
            {"name": "twelve_data", "symbol": "QCOM"},
            {"name": "yfinance", "symbol": "QCOM"},
        ],
    ),
    (
        "BA",
        "Boeing",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "BA"},
            {"name": "twelve_data", "symbol": "BA"},
            {"name": "yfinance", "symbol": "BA"},
        ],
    ),
    (
        "V",
        "Visa",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "V"},
            {"name": "twelve_data", "symbol": "V"},
            {"name": "yfinance", "symbol": "V"},
        ],
    ),
    (
        "GOOGL",
        "Alphabet",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "polygon", "symbol": "GOOGL"},
            {"name": "twelve_data", "symbol": "GOOGL"},
            {"name": "yfinance", "symbol": "GOOGL"},
        ],
    ),
]


async def main() -> None:
    rows = [
        {
            "id": v[0],
            "display_name": v[1],
            "kind": v[2],
            "category": v[3],
            "unit": v[4],
            "providers": v[5],
            "active": True,
        }
        for v in VARIABLES
    ]

    stmt = pg_insert(Variable).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "display_name": stmt.excluded.display_name,
            "kind": stmt.excluded.kind,
            "category": stmt.excluded.category,
            "unit": stmt.excluded.unit,
            "providers": stmt.excluded.providers,
        },
    )

    async with AsyncSessionLocal() as session:
        await session.execute(stmt)
        await session.commit()

    log.info("seed_done", n=len(rows))
    print(f"Seeded {len(rows)} variables")


if __name__ == "__main__":
    asyncio.run(main())
