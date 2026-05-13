"""Provider base class and shared types.

A Provider knows how to fetch a time series for one symbol, in one upstream's
format. The IngestionRunner orchestrates Providers via the fallback chain
defined per variable in the `variables.providers` JSON column.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class DataPoint:
    """One (date, value) pair returned by a provider."""

    observed_on: date
    value: float


class ProviderError(Exception):
    """Base exception for any provider failure."""


class ProviderRateLimited(ProviderError):
    """Provider rejected the request due to rate-limiting (transient).

    The runner should try the next provider and not retry this one in the
    current cycle.
    """


class ProviderTimeout(ProviderError):
    """Provider request exceeded the timeout."""


class ProviderNotFound(ProviderError):
    """Symbol exists in the provider but no data was returned for the date range."""


class Provider(ABC):
    """A provider knows how to fetch a time series for a symbol from one upstream.

    Implementations must be stateless and async-safe. Construction is cheap;
    one instance per provider per process is fine.
    """

    name: str

    @abstractmethod
    async def fetch(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[DataPoint]:
        """Fetch daily observations for `symbol` between `start` and `end` inclusive.

        Returns an empty list if the symbol has no data in the range (this is
        a normal case, not an error). Raises ProviderRateLimited /
        ProviderTimeout / ProviderError for transient or fatal failures.
        """
        ...
