#!/usr/bin/env bash
# PAPER ONLY. Escaneo completo del hunter en el VPS (momentum-scan.timer,
# cada 30 min en sesión) + paper trader + persistencia. Desde el 2026-09-21
# el VPS es el dueño de watchlist.json, auditoria/ y la telemetría del
# hunter; GitHub solo respalda si el VPS se calla (respaldo_gha.py).
#
# CANDADOS. Ninguno durante el escaneo (~9 min): el rechequeo de 5 min
# jamás espera ni se salta por este proceso. Las únicas dos secciones con
# candado son cortas: (1) la escritura de la watchlist, dentro de Python
# (watchlist.lock, milisegundos, con el overlay del rechequeo aplicado en
# el instante de escribir) y (2) el commit/push, con el mismo flock que el
# rechequeo (dos git a la vez fabrican el conflicto de índice ya medido).
#
# YAHOO. El escaneo y el panel salen de la misma IP. Ante un 429 el bot
# escribe SU archivo de pausa (MOMENTUM_YAHOO_PAUSA_ARCHIVO) y deja de
# pedir 15 min; el panel lo lee y se frena también. El bot nunca obedece
# la pausa que escribe el panel.
#
# Vuelta atrás sin tocar código: MOMENTUM_SCAN_VPS=0 en paper.env deja
# este script como no-op; `systemctl disable --now momentum-scan.timer`
# lo apaga del todo. Un fallo de git nunca tumba la unidad.
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1
export MOMENTUM_TELEM_FUENTE=vps

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

if [ "${MOMENTUM_SCAN_VPS:-1}" = "0" ]; then
  echo "INFO: MOMENTUM_SCAN_VPS=0: el escaneo en el VPS está apagado (no-op)"
  exit 0
fi

export MOMENTUM_WATCHLIST_VPS_STATE="${MOMENTUM_WATCHLIST_VPS_STATE:-1}"
export MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"
LIMIT="${MOMENTUM_SCAN_LIMIT:-1000}"
LOCK="${MOMENTUM_GIT_LOCK:-/tmp/momentum-paper-git.lock}"

# Sync corto bajo el candado de git (nunca abre el escaneo con un pull a medias).
(
  flock -w 120 9 || { echo "WARN: git flock timeout en el pull; se escanea con el árbol actual"; exit 0; }
  # Misma razón que el rechequeo: la telemetría sucia niega el rebase.
  # El helper aparta esos paths, trae main y los devuelve.
  bash "$ROOT/scripts/git_pull_con_estado_local.sh" \
    || echo "WARN: git pull con estado local falló (continuing)"
) 9>"$LOCK"

# ── Escaneo + paper, SIN candado ──
set +e
"$PY" -m momentum_hunter.run --limit "$LIMIT"
hunter_rc=$?
# Candado corto compartido con el vigía (momentum_paper_trader/vigia.py):
# a 60 s de cadencia, dos ejecutores podrían revisar la misma señal en el
# mismo instante. Solo el paso paper; el escaneo sigue sin candado.
PAPER_LOCK="${MOMENTUM_PAPER_LOCK:-/tmp/momentum-paper-exec.lock}"
flock -w 120 "$PAPER_LOCK" "$PY" -m momentum_paper_trader.run
paper_rc=$?
set -e

git config user.name "momentum-opportunity-hunter" || true
git config user.email "momentum-opportunity-hunter@users.noreply.github.com" || true

persistir_estado() {
  # El overlay del rechequeo se vuelca al canónico antes de commitear:
  # GitHub recibe la misma verdad que ve el VPS (candado interno, corto).
  "$PY" -m momentum_hunter.run --materializar-overlay || echo "WARN: materializar overlay falló"
  paths=(
    momentum_hunter/watchlist.json
    momentum_hunter/auditoria
    momentum_hunter/alertas_enviadas.json
    momentum_hunter/estado_diario.json
    momentum_hunter/universo_cache.json
    momentum_hunter/telemetria
    momentum_paper_trader/revisiones.json
    momentum_paper_trader/archivo_triggered.jsonl
    momentum_paper_trader/telemetria
  )
  for p in "${paths[@]}"; do
    if [ -e "$p" ]; then git add -A "$p" || true; fi
  done
  if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "momentum_hunter: escaneo en el VPS [skip ci]" || true
    # Rebase + reintentos. NUNCA --force. Un fallo de git no tumba la unidad.
    if ! PERSIST_BRANCH=main bash "$ROOT/scripts/git_persist_rebase_push.sh"; then
      echo "WARN: git persist failed. Local files kept. Not failing unit."
    fi
  else
    echo "INFO: nothing to persist"
  fi
}

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
