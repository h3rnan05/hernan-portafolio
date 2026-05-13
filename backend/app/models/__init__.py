"""SQLAlchemy ORM models.

Importing this package registers all models with `Base.metadata` so
Alembic autogenerate sees them.
"""

from app.models.holding import Holding
from app.models.ingestion_run import IngestionRun
from app.models.model_fit import ModelFit
from app.models.observation import Observation
from app.models.portfolio import Portfolio
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.position import PositionSnapshot
from app.models.prediction import Prediction
from app.models.variable import Variable

__all__ = [
    "Holding",
    "IngestionRun",
    "ModelFit",
    "Observation",
    "Portfolio",
    "PortfolioSnapshot",
    "PositionSnapshot",
    "Prediction",
    "Variable",
]
