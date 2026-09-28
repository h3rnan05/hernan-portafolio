#!/usr/bin/env bash
# Reporte de cierre con subastas oficiales (momentum_paper_trader/reporte_cierre.py).
# Solo lectura: watchlist, revisiones.json y el host de DATOS. Escribe en
# /var/lib/momentum/reportes_cierre/, nunca en git. Un fallo no afecta a
# ninguna otra unidad. Para Telegram: MOMENTUM_REPORTE_CIERRE_TELEGRAM=1
# en /etc/momentum/paper.env.
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1
export MOMENTUM_WATCHLIST_VPS_STATE="${MOMENTUM_WATCHLIST_VPS_STATE:-1}"
ARGS=()
if [ "${MOMENTUM_REPORTE_CIERRE_TELEGRAM:-0}" = "1" ]; then
  ARGS+=(--telegram)
fi
exec "$PY" -m momentum_paper_trader.reporte_cierre ${ARGS[@]+"${ARGS[@]}"}
