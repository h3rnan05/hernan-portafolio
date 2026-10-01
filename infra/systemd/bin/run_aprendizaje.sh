#!/usr/bin/env bash
# Aprendizaje nocturno (momentum_paper_trader/aprendizaje.py). SOLO SOMBRA:
# lee revisiones.json, la auditoría, events.jsonl y GET de órdenes paper;
# escribe en /var/lib/momentum/estado/momentum_paper_trader/aprendizaje/,
# nunca en git ni en el bróker. Telegram: MOMENTUM_APRENDIZAJE_TELEGRAM=1
# en /etc/momentum/paper.env (apagado por defecto).
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1
ARGS=()
if [ "${MOMENTUM_APRENDIZAJE_TELEGRAM:-0}" = "1" ]; then
  ARGS+=(--telegram)
fi
exec "$PY" -m momentum_paper_trader.aprendizaje ${ARGS[@]+"${ARGS[@]}"}
