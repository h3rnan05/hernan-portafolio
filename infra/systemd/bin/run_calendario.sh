#!/usr/bin/env bash
# PAPER ONLY. Refresco diario del calendario de sesión
# (momentum_paper_trader/calendario_job.py). Escribe
# /var/lib/momentum/calendario_alpaca.json. Si Alpaca no responde, el
# módulo deja el archivo anterior intacto y este script sale en error
# para que el timer se vea failed en el journal.
# Vive en /opt/momentum/bin/: un git pull no lo actualiza.
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

export MOMENTUM_CALENDARIO_PATH="${MOMENTUM_CALENDARIO_PATH:-/var/lib/momentum/calendario_alpaca.json}"

exec "$PY" -m momentum_paper_trader.calendario_job
