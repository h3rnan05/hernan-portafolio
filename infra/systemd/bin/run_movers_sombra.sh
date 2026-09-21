#!/usr/bin/env bash
# PAPER ONLY. Descubrimiento "movers" EN SOMBRA (momentum_hunter/movers.py):
# solo telemetría, no escribe la watchlist, no manda Telegram, no toca el
# escaneo ni el rechequeo. NO se instala solo: ver infra/systemd/README.md.
#
# Apagado salvo MOMENTUM_MOVERS_SOMBRA=1 en /etc/momentum/paper.env (el
# módulo también lo comprueba y sale sin hacer nada).
#
# Candados: solo contra sí mismo (dos corridas de sombra no se solapan).
# Por decisión del dueño (2026-09-21) nada bloquea al escaneo ni al
# rechequeo mientras corren; la convivencia con Yahoo la resuelve el
# archivo de pausa del bot (MOMENTUM_YAHOO_PAUSA_ARCHIVO): si el escaneo
# recibió un 429, esta corrida tampoco pide nada.
#
# La telemetría (momentum_hunter/telemetria/<fecha>/vps/movers.jsonl) la
# commitea el rechequeo cada 5 min con el resto de la telemetría del
# hunter; este script no hace git.
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
"$PY" -m momentum_hunter.run --movers-sombra
