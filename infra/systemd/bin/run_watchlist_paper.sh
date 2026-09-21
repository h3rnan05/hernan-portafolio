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

# Overlay VPS: --solo-watchlist NO escribe watchlist.json. Ese PATH es
# de GHA; suciarlo rompe `git pull --rebase` (medido 2026-09-18).
# Rollback: MOMENTUM_WATCHLIST_VPS_STATE=0 en paper.env.
export MOMENTUM_WATCHLIST_VPS_STATE="${MOMENTUM_WATCHLIST_VPS_STATE:-1}"
# Freno de Yahoo del bot (429): mismo archivo que usa el escaneo.
export MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"

# /var/lib no está en git: copia diaria del state antes de mutar.
bash "$ROOT/scripts/backup_watchlist_vps_state.sh" \
  || echo "WARN: backup watchlist VPS state failed"

git pull --rebase origin main >/dev/null 2>&1 || git pull --rebase >/dev/null 2>&1 || echo "WARN: git pull --rebase failed (continuing)"

hunter_rc=0
paper_rc=0
set +e
"$PY" -m momentum_hunter.run --solo-watchlist
hunter_rc=$?
STATE="${MOMENTUM_WATCHLIST_STATE:-/var/lib/momentum/watchlist_vps_state.json}"
if [ -f "$STATE" ]; then
  echo "INFO: vps watchlist state bytes=$(wc -c < "$STATE") mtime=$(stat -c %y "$STATE" 2>/dev/null || true)"
fi
"$PY" -m momentum_paper_trader.run
paper_rc=$?
set -e

git config user.name "momentum-opportunity-hunter" || true
git config user.email "momentum-opportunity-hunter@users.noreply.github.com" || true

persistir_estado() {
  # Desde el 2026-09-21 el VPS es el dueño de la watchlist y de la
  # auditoría/telemetría del hunter (el escaneo corre acá). Antes de
  # commitear, el overlay se vuelca al canónico (candado interno, corto)
  # para que GitHub vea la misma verdad que el VPS.
  "$PY" -m momentum_hunter.run --materializar-overlay || echo "WARN: materializar overlay falló"
  paths=(
    momentum_hunter/watchlist.json
    momentum_hunter/auditoria
    momentum_hunter/alertas_enviadas.json
    momentum_hunter/telemetria
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
