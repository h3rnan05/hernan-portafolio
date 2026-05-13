# Portfolio Prediction Engine

A backend service that ingests ~30 macro/market predictors plus 9 portfolio stocks, fits lagged multivariable OLS regression models with econometric diagnostics, generates predictions, and reconciles them against a Capital.com brokerage account.

This repo is the implementation of `BACKEND_BRIEF.md` (the design doc). Read the brief first.

## Repository layout

```
portfolio-engine/
├── BACKEND_BRIEF.md          # full design doc — read first
├── backend/                  # Python FastAPI service (data layer + model layer)
├── frontend/                 # Next.js dashboard (port of the HTML mockup)
├── infra/                    # GitHub Actions cron workflows
└── README.md                 # this file
```

## Phase 0 — what's in this scaffold

The scaffold ships **Phase 0 + a working slice of Phase 1** from the brief:

- ✅ Monorepo structure
- ✅ Supabase/Postgres migrations for the full schema (§3 of the brief)
- ✅ Variables registry seed script (all 30 predictors + 9 stocks)
- ✅ Working FRED ingestion provider (13 macro variables)
- ✅ Provider base class + `fetch_with_fallback()` orchestrator
- ✅ Read-only API endpoints: `/health`, `/variables`, `/observations/{id}`
- ✅ Minimal Next.js frontend that consumes the API
- ✅ GitHub Actions cron workflow (commented, ready to enable)
- ⏳ Stooq, yfinance, Polygon, Capital.com — provider stubs only, agent fills these in (Phase 1 finish)
- ⏳ Model layer, predictions, portfolio optimizer — Phases 2–4 not started

## Phase 0 acceptance criteria

You should be able to run the following commands and see the listed outcomes. If any step fails, fix it before moving on.

```bash
# 1. Install deps and run migrations
cd backend
cp .env.example .env                    # then edit .env with your FRED_API_KEY
uv sync                                 # or: pip install -e .
uv run alembic upgrade head             # creates all tables in Supabase

# 2. Seed the variables registry
uv run python scripts/seed_variables.py
# ✅ Expected: "Seeded 39 variables"

# 3. Run a manual ingestion (FRED only for now)
uv run python scripts/run_ingestion.py --providers fred
# ✅ Expected: "Ingested 13 variables, ~30 days each"

# 4. Start the API
uv run uvicorn app.main:app --reload
# ✅ Expected: server on http://localhost:8000

# 5. Verify endpoints
curl http://localhost:8000/health
# ✅ {"ok": true, "version": "0.1.0"}

curl http://localhost:8000/variables | jq '. | length'
# ✅ 39

curl 'http://localhost:8000/observations/CPI_YoY_US?from=2026-01-01' | jq '. | length'
# ✅ ≥ 3   (3+ months of CPI data)

# 6. Run tests
uv run pytest
# ✅ All green

# 7. Frontend
cd ../frontend
cp .env.example .env.local
pnpm install
pnpm dev
# Open http://localhost:3000 — should show variable count from the API
```

## What the coding agent does next

After Phase 0 is green, work through the brief's phases in order:

1. **Finish Phase 1** — implement `stooq.py`, `yfinance_provider.py`, `polygon.py`, `baltic.py`, `ism_pmi.py`. Verify each provider produces real data into `observations`. Manual symbol verification on Stooq is required (see brief §2.2).
2. **Phase 2** — model layer in `app/modeling/`. The functions are sketched in brief §4.
3. **Phase 3** — predictions and backtest.
4. **Phase 4** — portfolio optimizer.
5. **Phase 5** — Capital.com integration.
6. **Phase 6** — port the HTML mockup to Next.js.

Each phase ends with something demoable. Don't skip phases.

## Get API keys before you start

- **FRED** (mandatory, free) → https://fred.stlouisfed.org/docs/api/api_key.html
- **Polygon** (optional, free tier) → https://polygon.io/dashboard/signup
- **Capital.com demo** (Phase 5) → https://capital.com → enable 2FA → Settings → API integrations
- **Supabase** (mandatory, free) → https://supabase.com → create project → get connection string from Project Settings → Database

Stooq and yfinance need no keys.

## A note on Supabase RLS

Supabase has Row-Level Security on by default. This backend uses the **service role connection string** (not the anon key), which bypasses RLS. That's the correct setup for a server-side data API. **Never expose the service role key to the frontend.** The frontend talks to FastAPI; only FastAPI talks to the DB.

## License

Private. Do not redistribute.
