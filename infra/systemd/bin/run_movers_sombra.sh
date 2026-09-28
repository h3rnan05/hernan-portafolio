#!/usr/bin/env bash
# PAPER ONLY. Descubrimiento "movers" EN SOMBRA (momentum_hunter/movers.py):
# solo telemetría, no escribe la watchlist, no manda Telegram, no toca el
# escaneo ni el rechequeo. NO se instala solo: ver infra/systemd/README.md.
#
# Apagado salvo MOMENTUM_MOVERS_SOMBRA=1 en /etc/momentum/paper.env (el
# módulo también lo comprueba y sale sin hacer nada).
#
# Candados:
# - contra sí mismo (flock -n): dos corridas de sombra no se solapan.
# - el mismo archivo que el persist (/tmp/momentum-paper-git.lock) mientras
#   corre el módulo. El 2026-09-28 esta sombra escribió movers.jsonl entre
#   el stash y el pop del pull: el pop abortó entero y el stash se llevó
#   revisiones.json, alertas y la auditoría. Tomar ese candado mientras se
#   escribe cierra la carrera aunque el timer se desfase. No frena los ~9 min
#   del escaneo: solo espera si el persist está en medio de un pull o un
#   commit. Si a los 60 s no lo consigue, esta corrida no escribe (el
#   TimeoutStartSec de la unidad es 240 s y el módulo puede tardar ~2 min;
#   esperar más arriesga un kill a mitad de la escritura).
#
# La convivencia con Yahoo la resuelve el archivo de pausa del bot
# (MOMENTUM_YAHOO_PAUSA_ARCHIVO): si el escaneo recibió un 429, esta
# corrida tampoco pide nada.
#
# La telemetría (momentum_hunter/telemetria/<fecha>/vps/movers.jsonl) la
# commitea el rechequeo con el resto; este script no hace commit ni pull.
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

if [ "${MOMENTUM_MOVERS_SOMBRA:-0}" != "1" ]; then
  echo "INFO: MOMENTUM_MOVERS_SOMBRA no es 1: la sombra de movers está apagada (no-op)"
  exit 0
fi

export MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"
LOCK="${MOMENTUM_MOVERS_LOCK:-/tmp/momentum-movers-sombra.lock}"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "INFO: otra corrida de movers en sombra sigue viva; esta se salta"
  exit 0
fi
GIT_LOCK="${MOMENTUM_GIT_LOCK:-/tmp/momentum-paper-git.lock}"
exec 8>"$GIT_LOCK"
if ! flock -w 60 8; then
  echo "WARN: candado del persist ocupado 60s; esta sombra no escribe (no se pisa el pull)"
  exit 0
fi
"$PY" -m momentum_hunter.run --movers-sombra
