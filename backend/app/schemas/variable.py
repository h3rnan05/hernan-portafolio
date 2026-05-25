"""Variable response schema."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

VariableKind = Literal["predictor", "stock", "etf", "index", "portfolio"]


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
    is_target: bool
    last_observed_on: date | None = None
    last_value: float | None = None


class VariableCreate(BaseModel):
    """Admin: register a new variable."""

    id: str
    display_name: str
    kind: VariableKind
    category: str | None = None
    unit: str | None = None
    providers: list[ProviderConfig] = []
    is_target: bool = False


class VariablePatch(BaseModel):
    """Admin: update mutable fields on an existing variable."""

    display_name: str | None = None
    category: str | None = None
    unit: str | None = None
    providers: list[ProviderConfig] | None = None
    active: bool | None = None
    is_target: bool | None = None
