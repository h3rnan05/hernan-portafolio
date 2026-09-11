#!/usr/bin/env bash
# PAPER ONLY: watchlist + paper + persist. Git failures must NOT fail the unit / spam Telegram.
set -u
ROOT=/opt/hernan-portafolio
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1

set -a
# shellcheck disable=SC1091
source /etc/momentum/paper.env
set +a

# Best-effort sync (never abort run)
git pull --rebase origin main >/dev/null 2>&1 || git pull --rebase >/dev/null 2>&1 || echo "WARN: git pull --rebase failed (continuing)"

hunter_rc=0
paper_rc=0
set +e
"$PY" -m momentum_hunter.run --solo-watchlist
hunter_rc=$?
"$PY" -m momentum_paper_trader.run
paper_rc=$?
set -e

git config user.name "momentum-opportunity-hunter" || true
git config user.email "momentum-opportunity-hunter@users.noreply.github.com" || true

paths=(
  momentum_hunter/watchlist.json
  momentum_hunter/alertas_enviadas.json
  momentum_paper_trader/revisiones.json
  momentum_hunter/auditoria
  momentum_hunter/telemetria
  momentum_paper_trader/telemetria
)
for p in "${paths[@]}"; do
  if [ -e "$p" ]; then git add -A "$p" || true; fi
done

if ! git diff --cached --quiet 2>/dev/null; then
  git commit -m "momentum_hunter: re-chequeo de watchlist [skip ci]" || true
  set +e
  git pull --rebase origin main >/dev/null 2>&1 || git pull --rebase >/dev/null 2>&1
  git push origin HEAD:main
  push_rc=$?
  set -e
  if [ "${push_rc:-0}" -ne 0 ]; then
    echo "WARN: git push failed (rc=${push_rc}). Local files kept. Not failing unit."
  fi
else
  echo "INFO: nothing to persist"
fi

if [ "$hunter_rc" -ne 0 ]; then
  echo "ERROR: hunter rc=$hunter_rc"
  exit "$hunter_rc"
fi
if [ "$paper_rc" -ne 0 ]; then
  echo "ERROR: paper rc=$paper_rc"
  exit "$paper_rc"
fi
exit 0
