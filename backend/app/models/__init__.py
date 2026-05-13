"""SQLAlchemy ORM models.

Importing this package registers all models with `Base.metadata` so
Alembic autogenerate sees them.
"""

from app.models.ingestion_run import IngestionRun
from app.models.model_fit import ModelFit
from app.models.observation import Observation
from app.models.portfolio import Portfolio
from app.models.position import PositionSnapshot
from app.models.prediction import Prediction
from app.models.variable import Variable

__all__ = [
    "IngestionRun",
    "ModelFit",
    "Observation",
    "Portfolio",
    "PositionSnapshot",
    "Prediction",
    "Variable",
]
