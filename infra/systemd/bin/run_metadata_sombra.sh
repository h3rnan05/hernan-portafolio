#!/usr/bin/env bash
# PAPER ONLY. Sombra de metadata (momentum_hunter/sombra_metadata.py):
# Yahoo vs Finnhub sobre una muestra, cinco sesiones, JSONL fuera del
# repo. No escribe la watchlist, no manda Telegram, no hace git, no
# cambia MOMENTUM_METADATA_PROVIDER. NO se instala solo: ver
# infra/systemd/README.md.
#
# Apagado salvo MOMENTUM_METADATA_SOMBRA=1 en /etc/momentum/paper.env (el
# módulo también lo comprueba y sale sin hacer nada). Necesita
# FINNHUB_API_KEY en el mismo archivo.
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

if [ "${MOMENTUM_METADATA_SOMBRA:-0}" != "1" ]; then
  echo "INFO: MOMENTUM_METADATA_SOMBRA no es 1: la sombra de metadata está apagada (no-op)"
  exit 0
fi

export MOMENTUM_ESTADO_DIR="${MOMENTUM_ESTADO_DIR:-/var/lib/momentum/estado}"
# Misma cortesía con Yahoo que el resto del bot: si el escaneo recibió
# un 429, esta corrida tampoco le pide nada.
export MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"
LOCK="${MOMENTUM_METADATA_SOMBRA_LOCK:-/tmp/momentum-metadata-sombra.lock}"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "INFO: otra corrida de la sombra de metadata sigue viva; esta se salta"
  exit 0
fi
exec "$PY" -m momentum_hunter.sombra_metadata
