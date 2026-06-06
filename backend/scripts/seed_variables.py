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
        "BDRY ETF (Baltic Dry Index proxy)",
        "predictor",
        "Shipping",
        "USD",
        # The real Baltic Dry Index has no free API in 2026 — Baltic Exchange
        # licenses it commercially (Trading Economics $50/mo, etc.). The BDRY
        # ETF (Breakwave Dry Bulk Shipping) holds rolling Capesize/Panamax/
        # Supramax futures and is directionally correlated with the BDI.
        # EODHD All-World covers BDRY.US under our existing subscription.
        # NOTE: BDRY tracks ~3-month avg of futures, so it lags spot moves —
        # treat as a smoothed proxy rather than a tick-by-tick BDI feed.
        [
            {"name": "eodhd", "symbol": "BDRY.US"},
            {"name": "polygon", "symbol": "BDRY"},
        ],
    ),
    (
        "Citi_Econ_Surprise_US",
        "Chicago Fed National Activity Index (CFNAI)",
        "predictor",
        "Published Index",
        "Index",
        # The brief specified ADSBCISMV (ADS Business Conditions Index) as a free
        # substitute for Citi Economic Surprise. Both the original Citi index
        # and ADSBCISMV are now unavailable on FRED. CFNAI is a similar
        # composite (85 monthly indicators rolled into one z-scored value).
        [{"name": "fred", "symbol": "CFNAI"}],
    ),
    (
        "ISM_Manufacturing_PMI",
        "Empire State Manufacturing (ISM PMI proxy)",
        "predictor",
        "Published Index",
        "Index",
        # ISM revoked FRED redistribution rights in ~2016; the actual ISM PMI
        # series is no longer free via any reliable API. The NY Fed Empire
        # State Manufacturing General Business Conditions Diffusion Index is
        # built on the same diffusion methodology, released ~2 weeks earlier
        # each month, and correlates with the ISM PMI at ~0.7-0.8 over multi-
        # year windows. Free on FRED, kept current.
        [{"name": "fred", "symbol": "GACDISA066MSFRBNY"}],
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
        "10Y-2Y Treasury spread (LEI substitute)",
        "predictor",
        "Published Index",
        "Percent",
        # Original USSLIND (Atlanta Fed Leading Index) was discontinued in 2020.
        # T10Y2Y — the 10Y vs 2Y Treasury yield curve — is the NY Fed's canonical
        # recession leading indicator and is structurally similar in role: it
        # turns negative ahead of every US recession since 1980.
        [{"name": "fred", "symbol": "T10Y2Y"}],
    ),
    # ─── Bucket B: International indices (8) — EODHD primary (.INDX) ────────
    # EODHD symbol convention: {TICKER}.{EXCHANGE}, indices live on .INDX
    (
        "FTSE_100",
        "FTSE 100 (iShares Core ETF, ISF.LSE)",
        "predictor",
        "UK Index",
        "GBX",
        # EODHD All-World plan does not include the spot FTSE.INDX feed
        # (FTSE Russell licenses the index commercially). iShares Core
        # FTSE 100 ETF tracks the index within 0.1% — drop-in proxy.
        [
            {"name": "eodhd", "symbol": "ISF.LSE"},
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
            {"name": "eodhd", "symbol": "GDAXI.INDX"},
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
            {"name": "eodhd", "symbol": "FCHI.INDX"},
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
            {"name": "eodhd", "symbol": "N225.INDX"},
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
            {"name": "eodhd", "symbol": "HSI.INDX"},
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
            {"name": "eodhd", "symbol": "BVSP.INDX"},
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
            {"name": "eodhd", "symbol": "KS11.INDX"},
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
            {"name": "eodhd", "symbol": "BSESN.INDX"},
            {"name": "twelve_data", "symbol": "SENSEX"},
            {"name": "yfinance", "symbol": "^BSESN"},
        ],
    ),
    # ─── US + Mexico index benchmarks (3) — for the Overview comparison chart ─
    # The Overview "Comparativo de tu portafolio" chart plots the portfolio
    # against NYSE, NASDAQ, IPC México, Nikkei 225, and FTSE 100. The Nikkei and
    # FTSE proxies already exist above; these three add the US + MX benchmarks.
    # Kept as `predictor` kind so the daily ingestion runner (which only pulls
    # predictor/stock variables) keeps them current.
    (
        "NYSE_Composite",
        "NYSE Composite (Vanguard VTI ETF proxy)",
        "predictor",
        "US Index",
        "USD",
        # ^NYA (NYSE Composite) has no reliable free feed; VTI (Vanguard Total
        # Market) is the standard broad-US proxy and is on EODHD All-World.
        [
            {"name": "eodhd", "symbol": "VTI.US"},
            {"name": "polygon", "symbol": "VTI"},
            {"name": "twelve_data", "symbol": "VTI"},
            {"name": "yfinance", "symbol": "VTI"},
        ],
    ),
    (
        "NASDAQ_Composite",
        "NASDAQ 100 (Invesco QQQ ETF proxy)",
        "predictor",
        "US Index",
        "USD",
        # ^IXIC spot is licensed; QQQ (Invesco NASDAQ-100) tracks the tech-heavy
        # index closely and is freely available.
        [
            {"name": "eodhd", "symbol": "QQQ.US"},
            {"name": "polygon", "symbol": "QQQ"},
            {"name": "twelve_data", "symbol": "QQQ"},
            {"name": "yfinance", "symbol": "QQQ"},
        ],
    ),
    (
        "IPC_Mexico",
        "IPC México (S&P/BMV IPC)",
        "predictor",
        "Mexico Index",
        "Points",
        [
            {"name": "eodhd", "symbol": "MXX.INDX"},
            {"name": "twelve_data", "symbol": "MXX"},
            {"name": "yfinance", "symbol": "^MXX"},
        ],
    ),
    # ─── FX rates (4) — FRED primary, EODHD fallback (.FOREX) ───────────────
    (
        "EUR_USD",
        "EUR/USD",
        "predictor",
        "FX Rate",
        "Rate",
        [
            {"name": "fred", "symbol": "DEXUSEU"},
            {"name": "eodhd", "symbol": "EURUSD.FOREX"},
            {"name": "twelve_data", "symbol": "EUR/USD"},
        ],
    ),
    (
        "EUR_JPY",
        "EUR/JPY",
        "predictor",
        "FX Rate",
        "Rate",
        # FRED has no direct EUR/JPY series — EODHD primary
        [
            {"name": "eodhd", "symbol": "EURJPY.FOREX"},
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
            {"name": "eodhd", "symbol": "USDMXN.FOREX"},
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
            {"name": "eodhd", "symbol": "USDCNY.FOREX"},
            {"name": "twelve_data", "symbol": "USD/CNY"},
        ],
    ),
    # ─── Commodities (4) ────────────────────────────────────────────────────
    (
        "Gold_Spot",
        "Gold (SPDR GLD ETF, GLD.US)",
        "predictor",
        "Commodity",
        "USD",
        # FRED's London PM Fix series (GOLDAMGBD228NLBM) was discontinued and
        # EODHD All-World does not include the .COMM exchange. SPDR Gold Trust
        # (GLD) tracks the spot gold price almost perfectly via physical gold
        # holdings — best free proxy available.
        [
            {"name": "eodhd", "symbol": "GLD.US"},
            {"name": "twelve_data", "symbol": "XAU/USD"},
        ],
    ),
    (
        "Brent_Crude",
        "Brent Crude Oil",
        "predictor",
        "Commodity",
        "USD/bbl",
        # FRED's DCOILBRENTEU is the canonical spot Brent series, daily, free.
        # Kept as primary. EODHD's .COMM is not on our plan; we drop it.
        [
            {"name": "fred", "symbol": "DCOILBRENTEU"},
            {"name": "twelve_data", "symbol": "BRENT"},
        ],
    ),
    (
        "Wheat_Futures",
        "Wheat (Teucrium WEAT ETF)",
        "predictor",
        "Commodity",
        "USD",
        # EODHD .COMM not in plan. Teucrium Wheat Fund (WEAT) is a 1x ETF that
        # holds rolling CBOT wheat futures — direct exposure proxy.
        [
            {"name": "eodhd", "symbol": "WEAT.US"},
            {"name": "twelve_data", "symbol": "WHEAT"},
            {"name": "yfinance", "symbol": "ZW=F"},
        ],
    ),
    (
        "Copper_Futures",
        "Copper (CPER ETF)",
        "predictor",
        "Commodity",
        "USD",
        # US Copper Index Fund (CPER) holds rolling COMEX copper futures.
        [
            {"name": "eodhd", "symbol": "CPER.US"},
            {"name": "twelve_data", "symbol": "COPPER"},
            {"name": "yfinance", "symbol": "HG=F"},
        ],
    ),
    # ─── International stocks (4) — EODHD primary ───────────────────────────
    (
        "Banco_Santander_MAD",
        "Banco Santander (Madrid)",
        "predictor",
        "Madrid Stock",
        "EUR",
        [
            {"name": "eodhd", "symbol": "SAN.MC"},
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
            {"name": "eodhd", "symbol": "MC.PA"},
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
            {"name": "eodhd", "symbol": "NESN.SW"},
            {"name": "twelve_data", "symbol": "NESN:SIX"},
            {"name": "yfinance", "symbol": "NESN.SW"},
        ],
    ),
    (
        "Reliance_NSE",
        "Reliance Industries (GDR on LSE)",
        "predictor",
        "India Stock",
        "USD",
        # EODHD All-World does not include NSE India. Reliance is dual-listed
        # as a USD-denominated GDR on the London Stock Exchange (RIGD.LSE),
        # which is the standard international proxy for the NSE shares.
        [
            {"name": "eodhd", "symbol": "RIGD.LSE"},
            {"name": "twelve_data", "symbol": "RELIANCE:NSE"},
            {"name": "yfinance", "symbol": "RELIANCE.NS"},
        ],
    ),
    # ─── Portfolio stocks (9) — EODHD primary, Polygon + TD + yfinance fallbacks
    (
        "NVDA",
        "NVIDIA",
        "stock",
        "US Equity",
        "USD",
        [
            {"name": "eodhd", "symbol": "NVDA.US"},
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
            {"name": "eodhd", "symbol": "XOM.US"},
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
            {"name": "eodhd", "symbol": "CAT.US"},
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
            {"name": "eodhd", "symbol": "AMZN.US"},
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
            {"name": "eodhd", "symbol": "CRM.US"},
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
            {"name": "eodhd", "symbol": "QCOM.US"},
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
            {"name": "eodhd", "symbol": "BA.US"},
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
            {"name": "eodhd", "symbol": "V.US"},
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
            {"name": "eodhd", "symbol": "GOOGL.US"},
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
            # Every seeded stock is a regression target. Migration 0004 only
            # backfilled the original 9; without setting it here, newly seeded
            # stocks would default to is_target=False and never get a model.
            "is_target": v[2] == "stock",
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
            "is_target": stmt.excluded.is_target,
        },
    )

    async with AsyncSessionLocal() as session:
        await session.execute(stmt)
        await session.commit()

    log.info("seed_done", n=len(rows))
    print(f"Seeded {len(rows)} variables")


if __name__ == "__main__":
    asyncio.run(main())
