# Infra

## GitHub Actions

The `daily-ingestion.yml` workflow runs the ingestion pipeline on a schedule.
**It is disabled by default** — the `schedule:` block is commented out. Enable
it once secrets are configured.

### Setup

1. **Move the workflow file into place** so GitHub picks it up:
   ```bash
   mkdir -p .github/workflows
   cp infra/github-actions/daily-ingestion.yml .github/workflows/
   git add .github/workflows/daily-ingestion.yml
   git commit -m "chore: enable daily ingestion workflow"
   ```

2. **Add repository secrets** at GitHub → Settings → Secrets and variables → Actions:
   - `DATABASE_URL` — Supabase asyncpg URL
   - `DATABASE_URL_SYNC` — Supabase psycopg2 URL (for Alembic)
   - `FRED_API_KEY` — required
   - `POLYGON_API_KEY` — optional
   - `CAPITAL_*` — Phase 5 only
   - `ADMIN_BEARER_TOKEN` — for any admin endpoints called from CI
   - `SLACK_WEBHOOK_URL` — optional, for failure notifications

3. **Test manually first** via `Actions → Daily Ingestion → Run workflow`.
   Verify it succeeds end-to-end before enabling the schedule.

4. **Enable the schedule** by uncommenting the `schedule:` block in
   `daily-ingestion.yml`. Default is 22:00 UTC weekdays (post-US-close).

### Why the workflow has `continue-on-error` for Stooq

Free providers fail more than paid ones. Stooq, yfinance, and the scrapers
are configured with `continue-on-error: true` so a transient outage on one
provider doesn't fail the whole job — the runner's fallback chain (§2.4 of
the brief) handles it within the Python layer, but if even the fallback
chain fails for a provider, the workflow keeps going and ingests whatever
the next provider can serve.

FRED is **not** marked `continue-on-error` — it's the reliable backbone, and
if FRED fails the whole pipeline should fail loudly.

## Deployment notes (not in scope for Phase 0)

- **Backend** → Railway or Fly.io. Both have free tiers that fit. Inject env
  vars from secret store. Add a `Dockerfile` in Phase 1 if needed.
- **Frontend** → Vercel. Connect the GitHub repo, set `NEXT_PUBLIC_API_URL`
  in environment settings, deploy on push.
- **Database** → Supabase, free tier. The same `DATABASE_URL` works locally
  and in CI/prod.
