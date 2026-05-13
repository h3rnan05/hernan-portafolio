# Infra & deployment

This project is a daily-cron + REST API + Next.js stack. Production layout:

```
GitHub Actions (cron)  ──nightly──▶  ingestion ── upsert ──▶  Supabase Postgres
                                              ▲                      │
                                              │              (read)  │
                                              │                      ▼
                            Capital.com  ◀────                FastAPI backend
                            (broker, demo)                    (Railway / Fly.io)
                                                                     │
                                                                     ▼
                                                             Next.js dashboard
                                                                 (Vercel)
```

All three layers are independently deployable.

## 1. GitHub Actions — the daily pipeline

Workflow lives at [`.github/workflows/daily.yml`](../.github/workflows/daily.yml).
Two schedules:

- `0 22 * * 1-5` — daily ingest + predict + portfolio rebuild after US close
- `0 2 * * 0` — Sunday weekly refit of all 9 models

Required repository secrets (Settings → Secrets and variables → Actions):

| Secret | Source | Required? |
|---|---|---|
| `DATABASE_URL` | Supabase Direct (asyncpg) | ✅ |
| `DATABASE_URL_SYNC` | Supabase Direct (psycopg2) | ✅ |
| `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html | ✅ |
| `EODHD_API_KEY` | https://eodhd.com (All-World, $19.99/mo) | ✅ |
| `POLYGON_API_KEY` | https://polygon.io (free tier ok) | optional |
| `TWELVE_DATA_API_KEY` | https://twelvedata.com (free) | optional |
| `CAPITAL_API_KEY` / `CAPITAL_IDENTIFIER` / `CAPITAL_API_PASSWORD` | Capital.com demo → Settings → API integrations | optional (skips snapshot if absent) |
| `CAPITAL_BASE_URL` | `https://demo-api-capital.backend-capital.com` for demo | optional |
| `ADMIN_BEARER_TOKEN` | `openssl rand -hex 32` | ✅ |
| `SLACK_WEBHOOK_URL` | for failure alerts | optional |

To test the pipeline before letting cron run it: GitHub → Actions → "Daily
ingestion + predictions" → **Run workflow**.

## 2. Backend — Railway / Fly.io

A multi-stage `backend/Dockerfile` is included. Either platform works from
this image at $0/mo on the free tier (or a small paid plan if you want
zero cold starts).

### Railway

1. New project → Deploy from GitHub repo → pick `guth888/hernan-portafolio`
2. Settings → Root Directory → `backend`
3. Variables → paste all the secrets above
4. Deploy. Railway auto-detects the Dockerfile.

### Fly.io

```bash
cd backend
fly launch --no-deploy
fly secrets set DATABASE_URL=...  # paste each from .env
fly deploy
```

The container exposes port 8000 and respects `$PORT`.

## 3. Frontend — Vercel

1. Import the GitHub repo in Vercel.
2. Root Directory → `frontend`
3. Framework Preset → Next.js (auto-detected)
4. Environment Variables → `NEXT_PUBLIC_API_URL=https://<your-backend>.railway.app`
5. Deploy.

The dashboard talks only to the FastAPI backend — it never connects to
Supabase directly (and never should: the connection string in env is the
service-role URL which bypasses RLS).

## 4. Local dev

```bash
# Backend
cd backend && cp .env.example .env  # fill in keys
uv sync && uv run alembic upgrade head
uv run python scripts/seed_variables.py
uv run uvicorn app.main:app --reload   # http://localhost:8000

# Frontend
cd ../frontend && cp .env.example .env.local
pnpm install && pnpm dev               # http://localhost:3000  (or --port 3340)
```

## 5. Costs

| Component | Tier | $/month |
|---|---|---|
| Supabase Postgres | Free | $0 |
| EODHD All-World | Paid | $19.99 |
| FRED | Free | $0 |
| Polygon free | Free | $0 |
| Twelve Data free | Free | $0 |
| Capital.com demo | Free | $0 |
| Railway/Fly backend | Free or Hobby | $0–5 |
| Vercel frontend | Hobby | $0 |
| **Total** | | **≈ $20/mo** |
