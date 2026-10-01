#!/usr/bin/env bash
# Tasa de captura diaria (shadow_alpaca/tasa_captura.py). SOLO LECTURA:
# arma la lista de movers reales del día y anota cuáles vio el bot. No
# escribe la watchlist, no toca el paper trader, no coloca órdenes y no
# commitea. La salida va a $MOMENTUM_ESTADO_DIR/tasa_captura/ (fuera de git).
#
# Apagado salvo MOMENTUM_TASA_CAPTURA=1 en /etc/momentum/paper.env.
# Telegram: MOMENTUM_TASA_CAPTURA_TELEGRAM=1 en el mismo archivo.
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

if [ "${MOMENTUM_TASA_CAPTURA:-0}" != "1" ]; then
  echo "INFO: MOMENTUM_TASA_CAPTURA no es 1: la tasa de captura está apagada (no-op)"
  exit 0
fi

export MOMENTUM_WATCHLIST_VPS_STATE="${MOMENTUM_WATCHLIST_VPS_STATE:-1}"
# Respeta la pausa de Yahoo del bot (429) sin escribirla nunca.
export MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"
ARGS=()
if [ "${MOMENTUM_TASA_CAPTURA_TELEGRAM:-0}" = "1" ]; then
  ARGS+=(--telegram)
fi
exec "$PY" -m shadow_alpaca tasa_captura ${ARGS[@]+"${ARGS[@]}"}
