#!/usr/bin/env bash
# PAPER ONLY. Ownership:
# - GHA hunter: watchlist.json + auditoria + telem hunter
# - VPS: paper telem + revisiones + archivo_triggered.jsonl (PR #117)
# Lives outside git worktree so pull cannot overwrite until aligned.
set -u
ROOT=/opt/hernan-portafolio
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1
export MOMENTUM_TELEM_FUENTE=vps

set -a
# shellcheck disable=SC1091
source /etc/momentum/paper.env
set +a

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

persistir_estado() {
  paths=(
    momentum_paper_trader/revisiones.json
    momentum_paper_trader/archivo_triggered.jsonl
    momentum_paper_trader/telemetria
  )
  for p in "${paths[@]}"; do
    if [ -e "$p" ]; then git add -A "$p" || true; fi
  done
  if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "momentum_hunter: re-chequeo de watchlist [skip ci]" || true
    if ! PERSIST_BRANCH=main bash "$ROOT/scripts/git_persist_rebase_push.sh"; then
      echo "WARN: git persist failed. Local files kept. Not failing unit."
    fi
  else
    echo "INFO: nothing to persist"
  fi
}

LOCK="${MOMENTUM_GIT_LOCK:-/tmp/momentum-paper-git.lock}"
set +e
(
  flock -w 180 9
  flock_rc=$?
  if [ "$flock_rc" -ne 0 ]; then
    echo "WARN: git flock timeout (rc=${flock_rc}). Local files kept. Not failing unit."
    exit 0
  fi
  persistir_estado
) 9>"$LOCK"
set -e

if [ "$hunter_rc" -ne 0 ]; then
  echo "ERROR: hunter rc=$hunter_rc"
  exit "$hunter_rc"
fi
if [ "$paper_rc" -ne 0 ]; then
  echo "ERROR: paper rc=$paper_rc"
  exit "$paper_rc"
fi
exit 0
