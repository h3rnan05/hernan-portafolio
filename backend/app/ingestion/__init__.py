"""Data ingestion: providers and the fallback-chain runner."""

from app.ingestion.baltic import BalticDryIndexProvider
from app.ingestion.base import (
    DataPoint,
    Provider,
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.ingestion.fred import FREDProvider
from app.ingestion.ism_pmi import ISMManufacturingPMIProvider
from app.ingestion.polygon import PolygonProvider
from app.ingestion.runner import IngestionRunner
from app.ingestion.stooq import StooqProvider
from app.ingestion.twelve_data import TwelveDataProvider
from app.ingestion.yfinance import YFinanceProvider

__all__ = [
    "BalticDryIndexProvider",
    "DataPoint",
    "FREDProvider",
    "ISMManufacturingPMIProvider",
    "IngestionRunner",
    "PolygonProvider",
    "Provider",
    "ProviderError",
    "ProviderNotFound",
    "ProviderRateLimited",
    "ProviderTimeout",
    "StooqProvider",
    "TwelveDataProvider",
    "YFinanceProvider",
]
