"""FastAPI application entry point.

Run with:
    uv run uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.logging import get_logger, setup_logging
from app.routers import (
    health,
    models,
    observations,
    portfolios,
    positions,
    predictions,
    variables,
)

setup_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks."""
    settings = get_settings()
    log.info("app_start", version=__version__, log_level=settings.log_level)
    yield
    log.info("app_stop")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Portfolio Prediction Engine",
        description="Backend for the lagged-regression portfolio prediction system",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(variables.router)
    app.include_router(observations.router)
    app.include_router(models.router)
    app.include_router(predictions.router)
    app.include_router(portfolios.router)
    app.include_router(positions.router)

    return app


app = create_app()
