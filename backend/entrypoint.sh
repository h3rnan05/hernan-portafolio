#!/usr/bin/env sh
# Container entrypoint: apply DB migrations, THEN serve.
#
# Fail-closed by design: if `alembic upgrade head` fails, `set -e` aborts before
# uvicorn starts, the container exits non-zero, and Render's health check never
# passes — so the deploy is marked FAILED and the PREVIOUS healthy version keeps
# serving. This guarantees the API code never runs ahead of its schema, and a
# bad migration is a loud, visible deploy failure rather than a silent outage.
set -e

echo "[entrypoint] applying database migrations (alembic upgrade head)…"
alembic upgrade head
echo "[entrypoint] schema up to date — starting API."

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
