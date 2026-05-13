"""Pytest fixtures."""

from datetime import date

import pytest


@pytest.fixture
def yesterday() -> date:
    from datetime import timedelta

    return date.today() - timedelta(days=1)
