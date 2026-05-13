# Hernán — Portfolio Prediction Engine

> Daily lagged-OLS regression on 9 stocks against ~30 macro & market predictors.
> FastAPI backend, Next.js dashboard, Supabase Postgres, GitHub Actions cron.
> ~$20/mo to run 24/7. Real numbers, no mock data.

## Architecture

```
GitHub Actions (22:00 UTC weekdays)
   ↓
1. Ingestion fan-out — FRED, EODHD, Polygon, Twelve Data, yfinance, scrapers
2. Capital.com positions snapshot (broker)
3. Backfill yesterday's actuals
4. Run today's predictions (per-stock + portfolio rollup)
5. Rebuild the 5 risk profiles
   ↓ (Sundays only)
6. Refit all 9 models, swap is_active if diagnostics pass

Supabase Postgres ←──── 19 REST endpoints (FastAPI) ──── Next.js dashboard (Vercel)
```

## The algorithm

Per-stock OLS regression with 1-day lagged log-return predictors:

```
ret(stock, t) = α + Σ βᵢ · ret(predictor_i, t−1) + ε
```

Models only go live after passing **four** diagnostic thresholds (brief §4.2):

| Test | Threshold | Why |
|---|---|---|
| R² | ≥ 0.90 | Fit quality |
| Durbin–Watson | ∈ [1.5, 2.5] | No residual autocorrelation |
| Breusch–Pagan p | > 0.05 | Homoscedastic residuals |
| Max VIF | < 10 | No multicollinearity among predictors |

Feature selection is **no-overlap greedy**: each predictor appears in at most
one of the 9 models. ILP upgrade noted in `BACKEND_BRIEF.md` §4.3 for v2.

## Repository layout

```
hernan-portafolio/
├── backend/                Python 3.12 + FastAPI + SQLAlchemy async + statsmodels
│   ├── app/
│   │   ├── ingestion/      7 providers (fred, eodhd, polygon, twelve_data,
│   │   │                   yfinance, baltic scraper, ism_pmi scraper,
│   │   │                   capital_com broker)
│   │   ├── modeling/       regression, feature_select, prediction, refit driver
│   │   ├── portfolio/      5 risk-profile optimizer, backtest, daily runner
│   │   ├── routers/        19 REST endpoints, admin bearer auth on writes
│   │   ├── auth.py / config.py / db.py / main.py / logging.py
│   │   └── models/ schemas/
│   ├── migrations/         Alembic — 7 tables
│   ├── scripts/            CLI entry points (cron + manual)
│   ├── tests/              97 unit tests (respx mocks; no live network)
│   └── Dockerfile          multi-stage; Railway / Fly.io ready
├── frontend/               Next.js 15 + Tailwind v4 + recharts
│   ├── app/                7 routes: overview, models, model detail,
│   │                       portfolios, simulator, positions, variables (+ detail)
│   ├── components/         primitives, charts, diagnostics, top-nav
│   ├── hooks/use-poll.ts   60s SWR polling helper
│   └── lib/api.ts          typed client over all 19 endpoints
├── infra/
│   └── README.md           full deploy guide (Railway / Vercel / secrets)
├── .github/workflows/
│   ├── daily.yml           cron pipeline
│   └── test.yml            CI on every push + PR
└── BACKEND_BRIEF.md        full design doc
```

## Get it running

See **[infra/README.md](infra/README.md)** for the full deploy guide. TL;DR:

```bash
# 1. Local dev
cd backend && cp .env.example .env
# fill in DATABASE_URL, FRED_API_KEY, EODHD_API_KEY
uv sync && uv run alembic upgrade head && uv run python scripts/seed_variables.py
uv run uvicorn app.main:app --reload

# 2. Pull data
uv run python scripts/run_ingestion.py --days 540

# 3. Fit models
uv run python scripts/refit_all.py

# 4. Run predictions
uv run python scripts/run_predictions.py

# 5. Start the dashboard
cd ../frontend && cp .env.example .env.local
pnpm install && pnpm dev
```

## API keys needed

| Key | Cost | Used for |
|---|---|---|
| `FRED_API_KEY` | free | 13 US macro + FX + Gold + Brent |
| `EODHD_API_KEY` | $19.99/mo | 22 market-data vars (US + intl + commodities) |
| `POLYGON_API_KEY` | free tier ok | US-stock fallback |
| `TWELVE_DATA_API_KEY` | free tier ok | intl fallback |
| `CAPITAL_*` | free (demo) | Live position reconciliation |

Sign-up links in [`infra/README.md`](infra/README.md).

## Workflow

Every code change goes on a branch and through a PR:

```bash
git checkout -b feat/<thing>
# … edit, test, lint …
git push -u origin feat/<thing>
gh pr create
```

CI runs `ruff` + `pytest` (97 tests) on the backend and `tsc --noEmit` +
`next build` on the frontend before merge.

## License

Private. Do not redistribute.
