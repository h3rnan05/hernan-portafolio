#!/usr/bin/env bash
# SOMBRA. B2 screener de data.alpaca.markets (most-actives y movers).
# Solo JSONL. No escribe watchlist.json, no manda Telegram, no coloca
# órdenes y no commitea. NO se instala solo.
#
# Apagado salvo SHADOW_ALPACA=1. Fuera de la sesión regular el proceso
# sale sin llamar a Alpaca (el timer 13-20 UTC es más ancho que la sesión).
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1
export SHADOW_ALPACA_DIR="${SHADOW_ALPACA_DIR:-/var/lib/momentum/shadow_alpaca}"

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

if [ "${SHADOW_ALPACA:-0}" != "1" ]; then
  echo "INFO: SHADOW_ALPACA no es 1: la sombra de screener está apagada (no-op)"
  exit 0
fi

LOCK="${SHADOW_SCREENER_LOCK:-/tmp/momentum-shadow-screener.lock}"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "INFO: otra corrida de sombra de screener sigue viva; esta se salta"
  exit 0
fi
exec "$PY" -m shadow_alpaca screener
