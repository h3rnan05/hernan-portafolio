#!/usr/bin/env bash
# PAPER ONLY. Descarga GET /v2/assets del host paper y escribe el JSON
# local que el hunter lee. Este script es el único que habla con ese
# endpoint. No toca la watchlist, no coloca órdenes, no hace git.
#
# NO se habilita solo. La instalación está en infra/systemd/README.md.
# Un fallo (sin claves, HTTP, cuerpo vacío) sale con rc distinto de 0
# y NO reemplaza el archivo anterior: lo decide el módulo de Python.
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

# Fuera del repo. paper.env puede pisarlo; si nadie lo puso, este es
# el default. El directorio lo crea el módulo de Python al escribir.
export MOMENTUM_ESTADO_DIR="${MOMENTUM_ESTADO_DIR:-/var/lib/momentum/estado}"

exec "$PY" -m momentum_paper_trader.assets_job
