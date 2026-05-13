"""Test the fallback chain in IngestionRunner.

Mocks Provider implementations to verify that:
  - first success short-circuits
  - rate-limited and timeout providers fall through
  - empty result falls through
  - total failure returns (0, "none")
"""

from datetime import date, timedelta

from app.ingestion.base import (
    DataPoint,
    Provider,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.ingestion.runner import IngestionRunner


class FakeProvider(Provider):
    def __init__(
        self,
        name: str,
        result: list[DataPoint] | None = None,
        raises: type[Exception] | None = None,
    ) -> None:
        self.name = name
        self.result = result if result is not None else []
        self.raises = raises
        self.call_count = 0

    async def fetch(self, symbol, start, end):
        self.call_count += 1
        if self.raises:
            raise self.raises(f"fake {self.name} error")
        return self.result


async def test_first_success_short_circuits() -> None:
    p1 = FakeProvider("p1", result=[DataPoint(date.today(), 1.0)])
    p2 = FakeProvider("p2", result=[DataPoint(date.today(), 2.0)])
    runner = IngestionRunner({"p1": p1, "p2": p2})

    chain = [{"name": "p1", "symbol": "X"}, {"name": "p2", "symbol": "Y"}]
    # Bypass DB by calling _fetch_with_fallback directly with mocked _upsert
    runner._upsert_points = _noop_upsert

    written, served_by = await runner._fetch_with_fallback(
        "TEST",
        chain,
        date.today() - timedelta(days=1),
        date.today(),
    )

    assert written == 1
    assert served_by == "p1"
    assert p1.call_count == 1
    assert p2.call_count == 0  # never reached


async def test_rate_limited_falls_through() -> None:
    p1 = FakeProvider("p1", raises=ProviderRateLimited)
    p2 = FakeProvider("p2", result=[DataPoint(date.today(), 5.0)])
    runner = IngestionRunner({"p1": p1, "p2": p2})
    runner._upsert_points = _noop_upsert

    chain = [{"name": "p1", "symbol": "X"}, {"name": "p2", "symbol": "Y"}]
    written, served_by = await runner._fetch_with_fallback(
        "TEST",
        chain,
        date.today() - timedelta(days=1),
        date.today(),
    )

    assert served_by == "p2"
    assert written == 1
    assert p1.call_count == 1
    assert p2.call_count == 1


async def test_empty_result_falls_through() -> None:
    p1 = FakeProvider("p1", result=[])  # no rows but no error either
    p2 = FakeProvider("p2", result=[DataPoint(date.today(), 5.0)])
    runner = IngestionRunner({"p1": p1, "p2": p2})
    runner._upsert_points = _noop_upsert

    chain = [{"name": "p1", "symbol": "X"}, {"name": "p2", "symbol": "Y"}]
    _written, served_by = await runner._fetch_with_fallback(
        "TEST",
        chain,
        date.today() - timedelta(days=1),
        date.today(),
    )

    assert served_by == "p2"


async def test_all_providers_fail_returns_none() -> None:
    p1 = FakeProvider("p1", raises=ProviderTimeout)
    p2 = FakeProvider("p2", raises=ProviderError)
    runner = IngestionRunner({"p1": p1, "p2": p2})
    runner._upsert_points = _noop_upsert

    chain = [{"name": "p1", "symbol": "X"}, {"name": "p2", "symbol": "Y"}]
    written, served_by = await runner._fetch_with_fallback(
        "TEST",
        chain,
        date.today() - timedelta(days=1),
        date.today(),
    )

    assert written == 0
    assert served_by == "none"


async def test_unknown_provider_skipped() -> None:
    p1 = FakeProvider("p1", result=[DataPoint(date.today(), 1.0)])
    runner = IngestionRunner({"p1": p1})
    runner._upsert_points = _noop_upsert

    # First entry references a provider not in the runner — should skip silently
    chain = [
        {"name": "missing", "symbol": "X"},
        {"name": "p1", "symbol": "Y"},
    ]
    _written, served_by = await runner._fetch_with_fallback(
        "TEST",
        chain,
        date.today() - timedelta(days=1),
        date.today(),
    )

    assert served_by == "p1"


async def _noop_upsert(variable_id: str, points, provider_name: str) -> int:  # type: ignore[no-untyped-def]
    """Bypass DB writes in unit tests."""
    return len(points)
