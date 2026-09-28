#!/usr/bin/env bash
# PAPER ONLY. Escaneo completo del hunter en el VPS (momentum-scan.timer,
# cada 30 min en sesión) + paper trader. El estado queda en
# MOMENTUM_ESTADO_DIR. Este script no versiona nada: el único pull lo
# hace el wrapper del rechequeo, que el proceso permanente llama.
#
# CANDADOS. Ninguno durante el escaneo (~9 min). La escritura de la
# watchlist lleva su candado dentro de Python (milisegundos). El paso
# paper comparte el candado corto con el proceso permanente.
#
# YAHOO. El escaneo y el panel salen de la misma IP. Ante un 429 el bot
# escribe SU archivo de pausa (MOMENTUM_YAHOO_PAUSA_ARCHIVO) y deja de
# pedir 15 min; el panel lo lee y se frena también. El bot nunca obedece
# la pausa que escribe el panel.
#
# Vuelta atrás sin tocar código: MOMENTUM_SCAN_VPS=0 en paper.env deja
# este script como no-op; `systemctl disable --now momentum-scan.timer`
# lo apaga del todo.
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

# ── Escaneo + paper, SIN candado de versionado ──
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

# El overlay del rechequeo se vuelca al canónico (directorio de estado,
# candado interno, corto). No se versiona.
"$PY" -m momentum_hunter.run --materializar-overlay || echo "WARN: materializar overlay falló"

if [ "$hunter_rc" -ne 0 ]; then
  echo "ERROR: hunter rc=$hunter_rc"
  exit "$hunter_rc"
fi
if [ "$paper_rc" -ne 0 ]; then
  echo "ERROR: paper rc=$paper_rc"
  exit "$paper_rc"
fi
exit 0
