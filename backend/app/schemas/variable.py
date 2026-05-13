"""Variable response schema."""

from datetime import date

from pydantic import BaseModel, ConfigDict


class ProviderConfig(BaseModel):
    name: str
    symbol: str


class VariableOut(BaseModel):
    """Public representation of a Variable, including last-observed snapshot."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str
    kind: str
    category: str | None = None
    unit: str | None = None
    providers: list[ProviderConfig]
    active: bool
    last_observed_on: date | None = None
    last_value: float | None = None
