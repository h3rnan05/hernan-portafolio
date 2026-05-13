# Backend — Portfolio Prediction Engine

FastAPI service that ingests time-series, fits regression models, and exposes a prediction API.

## Setup

### Prerequisites

- Python 3.11+
- `uv` (recommended, faster than pip): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- A Supabase project (free tier is fine)
- A FRED API key (free): https://fred.stlouisfed.org/docs/api/api_key.html

### First-time setup

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env — at minimum, fill in DATABASE_URL, DATABASE_URL_SYNC, FRED_API_KEY

# 2. Install dependencies
uv sync                     # or: pip install -e ".[dev]"

# 3. Run migrations
uv run alembic upgrade head

# 4. Seed the variables registry
uv run python scripts/seed_variables.py
# → "Seeded 39 variables"

# 5. Pull initial data (FRED only — Stooq/yfinance/Polygon need Phase 1 work)
uv run python scripts/run_ingestion.py --providers fred --days 90
# → "Ingested ~13 variables × ~90 days"

# 6. Start the API
uv run uvicorn app.main:app --reload
# → http://localhost:8000  (docs: http://localhost:8000/docs)
```

### Smoke tests

```bash
curl http://localhost:8000/health
# {"ok":true,"version":"0.1.0","db_reachable":true}

curl http://localhost:8000/variables | python -m json.tool | head -20

curl 'http://localhost:8000/observations/CPI_YoY_US?from=2026-01-01' | python -m json.tool
```

### Running tests

```bash
uv run pytest -v
```

All tests should pass. Tests use `respx` to mock HTTP — no real network.

## Adding a new provider

The `Provider` ABC in `app/ingestion/base.py` is the contract. Steps:

1. Create `app/ingestion/{name}.py` with a class subclassing `Provider`.
2. Implement `async def fetch(self, symbol: str, start: date, end: date) -> list[DataPoint]`.
3. Map upstream errors to `ProviderRateLimited`, `ProviderTimeout`, or `ProviderError`.
4. Register it in `scripts/run_ingestion.py::build_runner()`.
5. Add the provider name + symbol to `variables.providers` in the seed script for the variables it should serve.
6. Re-run `seed_variables.py` to update existing rows (it's idempotent).

`app/ingestion/stooq.py` and `app/ingestion/fred.py` are reference implementations — copy their structure.

## Adding a new migration

```bash
# After changing a model
uv run alembic revision --autogenerate -m "what changed"
# Review the generated file in migrations/versions/, then:
uv run alembic upgrade head
```

## Project structure

```
backend/
├── app/
│   ├── config.py            # Pydantic Settings (env vars)
│   ├── db.py                # Async SQLAlchemy engine + session
│   ├── logging.py           # structlog setup
│   ├── main.py              # FastAPI app + lifespan + routers
│   ├── models/              # SQLAlchemy ORM
│   ├── schemas/             # Pydantic response models
│   ├── ingestion/
│   │   ├── base.py          # Provider ABC + exceptions
│   │   ├── fred.py          # ✅ implemented
│   │   ├── stooq.py         # ✅ implemented (symbols need verification)
│   │   ├── runner.py        # Fallback-chain orchestrator
│   │   └── (yfinance, polygon, capital_com, baltic, ism — Phase 1 TODO)
│   └── routers/
│       ├── health.py
│       ├── variables.py
│       └── observations.py
├── migrations/              # Alembic
├── scripts/
│   ├── seed_variables.py    # Populate the registry
│   └── run_ingestion.py     # Manual ingestion trigger
└── tests/
```

## Common commands

```bash
# Lint + format
uv run ruff check .
uv run ruff format .

# Type check (when you add mypy)
# uv run mypy app

# Database shell (psql)
psql $DATABASE_URL_SYNC

# Drop all data and recreate (NUCLEAR — local dev only)
uv run alembic downgrade base && uv run alembic upgrade head && uv run python scripts/seed_variables.py
```
