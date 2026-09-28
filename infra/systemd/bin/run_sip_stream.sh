#!/usr/bin/env bash
# PAPER DATA ONLY. Un solo proceso dueño del websocket SIP de barras.
# No coloca órdenes. Vive en /opt/momentum/bin/: un git pull no lo
# actualiza; cada cambio exige volver a hacer `install`.
#
# MOMENTUM_SIP_STREAM queda forzado a sombra. `primario` existe en el
# código y no se enciende desde aquí: habría que quitar esta línea y,
# en el mismo cambio, exportarla en el entorno del vigía. No se hace
# en el deploy de las 3 sesiones de sombra.
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

export MOMENTUM_SIP_STREAM=sombra
export MOMENTUM_ESTADO_DIR="${MOMENTUM_ESTADO_DIR:-/var/lib/momentum/estado}"

exec "$PY" -m momentum_hunter.data.sip_stream
