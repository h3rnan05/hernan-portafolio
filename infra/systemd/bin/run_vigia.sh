#!/usr/bin/env bash
# PAPER ONLY. Wrapper del vigía (2026-09-22): proceso permanente que
# reemplaza al timer de 5 min del rechequeo. Mismo entorno que
# run_watchlist_paper.sh; el bucle vive en momentum_paper_trader/vigia.py.
# Vive en /opt/momentum/bin/ como el resto (un git pull no lo actualiza:
# cada cambio exige volver a hacer `install`).
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

# Rollback del overlay: MOMENTUM_WATCHLIST_VPS_STATE=0 en paper.env.
export MOMENTUM_WATCHLIST_VPS_STATE="${MOMENTUM_WATCHLIST_VPS_STATE:-1}"
# Freno de Yahoo del bot (429): mismo archivo que usan escaneo y rechequeo.
export MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"
# La persistencia (git) la hace el wrapper del rechequeo en modo solo-persistir.
export MOMENTUM_VIGIA_WRAPPER_PERSIST="${MOMENTUM_VIGIA_WRAPPER_PERSIST:-/opt/momentum/bin/run_watchlist_paper.sh}"
# Candado corto del ejecutor, compartido con el paso paper del escaneo.
export MOMENTUM_PAPER_LOCK="${MOMENTUM_PAPER_LOCK:-/tmp/momentum-paper-exec.lock}"

exec "$PY" -m momentum_paper_trader.vigia
