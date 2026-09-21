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

# El estado del VPS (watchlist, revisiones, archivo, telemetría) tiene
# que llegar a main: sin eso GitHub y el respaldo trabajan con datos
# viejos. Si no llega, se deja rastro donde alguien lo va a ver: un
# evento en el log del panel (se pinta en rojo) y un Telegram, a lo
# sumo uno por día para no repetir el mismo aviso cada 5 minutos. Nada
# de esto puede tumbar la unidad ni tocar una orden. (Mismo bloque que
# scripts/run_watchlist_paper.sh, #150: ESTE es el wrapper que corre
# en el VPS; el de scripts/ es código muerto ahí -- ver README.)
AVISOS_DIR="${MOMENTUM_AVISOS_DIR:-/var/lib/momentum}"
persist_fallido() {
  local motivo="$1" intentos="${2:-0}"
  echo "ERROR: persist_fallido motivo=${motivo} intentos=${intentos}"
  "$PY" -m dashboard.events persist_fallido "motivo=${motivo}" "intentos=${intentos}" >/dev/null 2>&1 || true
  local marca="${AVISOS_DIR}/persist_fallido.avisado"
  local hoy
  hoy="$(date -u +%F)"
  if [ -f "$marca" ] && [ "$(cat "$marca" 2>/dev/null)" = "$hoy" ]; then
    echo "INFO: persist_fallido ya avisado hoy por Telegram; no se repite"
    return 0
  fi
  if bash "$ROOT/scripts/notify_telegram.sh" \
      "ERROR [paper][vps] persist fallido: ${motivo} (intentos=${intentos}). El estado paper del VPS no llegó a main; sigue en disco." \
      >/dev/null 2>&1; then
    echo "$hoy" > "$marca" 2>/dev/null || true
  else
    echo "WARN: telegram notify de persist_fallido no enviado (sin credenciales o sin red)"
  fi
  return 0
}

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
      persist_fallido "git persist failed" "${PERSIST_MAX_INTENTOS:-5}"
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
    persist_fallido "flock timeout" 0
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
