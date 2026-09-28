#!/usr/bin/env bash
# SOMBRA. B1 noticias: Alpaca data (Benzinga) + Yahoo, el mismo detector
# del hunter, JSONL propio. No escribe watchlist.json, no manda Telegram,
# no coloca órdenes y no commitea. NO se instala solo.
#
# Apagado salvo SHADOW_ALPACA=1 en /etc/momentum/paper.env.
# La salida va a /var/lib/momentum/shadow_alpaca, FUERA del repo, para que
# el commit de la telemetría del hunter no se la lleve.
#
# Candado solo contra sí misma. No toma el candado del paper.
# Si el bot está en pausa de Yahoo (429), esta corrida no pide Yahoo y lo
# anota como lado no disponible. No escribe esa pausa: escribirla apagaría
# el escaneo.
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
  echo "INFO: SHADOW_ALPACA no es 1: la sombra de noticias está apagada (no-op)"
  exit 0
fi

export MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"
LOCK="${SHADOW_NOTICIAS_LOCK:-/tmp/momentum-shadow-noticias.lock}"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "INFO: otra corrida de sombra de noticias sigue viva; esta se salta"
  exit 0
fi
exec "$PY" -m shadow_alpaca noticias
