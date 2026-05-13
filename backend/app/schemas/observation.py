"""Observation response schema."""

from datetime import date

from pydantic import BaseModel, ConfigDict


class ObservationOut(BaseModel):
    """One row of a time series."""

    model_config = ConfigDict(from_attributes=True)

    observed_on: date
    value: float
    served_by_provider: str | None = None
