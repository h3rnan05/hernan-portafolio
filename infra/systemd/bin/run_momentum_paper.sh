#!/usr/bin/env bash
# PAPER ONLY. Wrapper único de las dos unidades systemd del VPS paper:
#
#   run_momentum_paper.sh escaneo    <- momentum-scan.service      (cada 30 min)
#   run_momentum_paper.sh watchlist  <- momentum-watchlist.service (cada 5 min)
#
# POR QUÉ UN SOLO ESCRITOR. Hasta el 2026-09-15 el escaneo completo vivía
# en GitHub Actions y el re-chequeo en el VPS, con el estado repartido
# entre los dos (GHA: watchlist/auditoria/telemetría hunter; VPS: paper).
# Medido esa semana: GHA disparaba 2-3 de 16 veces al día, siempre a las
# mismas horas (~17:00, ~20:00 y ~22:20 UTC), así que solo 2-4 de los 8
# slots del universo se visitaban por día. Y cuando GHA y el VPS sí
# coincidían, el rebase de GHA reventaba: 4 escaneos completos perdidos
# el 2026-09-11. Con este wrapper el VPS es el único que escribe estado
# en git y el reloj es el de systemd, que sí se cumple.
#
# POR QUÉ VIVE EN /opt/momentum/bin/ Y NO EN scripts/: hace `git pull`
# sobre el árbol al arrancar. bash lee el script por trozos, y un pull
# que reemplace el archivo a mitad de ejecución lo rompería. Se instala
# copiando (ver infra/systemd/README.md).
#
# LOCK. Las dos unidades escriben watchlist.json y no pueden correr a la
# vez. El re-chequeo NO espera: si hay un escaneo en curso se salta y el
# timer vuelve en 5 min. Limitación honesta: durante un escaneo (~9 min)
# se pierden 1-2 re-chequeos. Se acepta porque el escaneo también
# re-evalúa la watchlist y corre el paper trader, así que una TRIGGERED
# nueva no espera al re-chequeo. El escaneo sí espera a que termine un
# re-chequeo (dura ~1 min).
#
# Un fallo de git no tumba la unidad ni manda Telegram: los archivos
# quedan en local y se commitean en la corrida siguiente (paso 1).
# No toca umbrales, credenciales ni el endpoint de Alpaca.
set -u

MODE="${1:-}"
case "$MODE" in
  escaneo|watchlist) ;;
  *) echo "uso: $0 escaneo|watchlist" >&2; exit 2 ;;
esac

ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
export PYTHONUNBUFFERED=1
# Escritor primario de TODA la telemetría (hunter y paper). Sin esto las
# corridas caerían en `local/`.
export MOMENTUM_TELEM_FUENTE=vps

set -a
# shellcheck disable=SC1091
source "${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"
set +a

LOCK="${MOMENTUM_RUN_LOCK:-/tmp/momentum-paper-run.lock}"
exec 9>"$LOCK"
if [ "$MODE" = "watchlist" ]; then
  if ! flock -n 9; then
    echo "INFO: otra corrida tiene el lock (escaneo en curso); este re-chequeo se salta"
    exit 0
  fi
else
  LOCK_WAIT="${MOMENTUM_LOCK_WAIT_SEC:-300}"
  if ! flock -w "$LOCK_WAIT" 9; then
    echo "WARN: lock ocupado más de ${LOCK_WAIT}s; este escaneo se salta"
    exit 0
  fi
fi

# Todo el estado que producen hunter y paper. Un solo escritor, una sola
# lista. Si falta algo acá, queda modificado en local y el
# `git pull --rebase` de la corrida siguiente se niega a correr.
PATHS=(
  momentum_hunter/universo_cache.json
  momentum_hunter/alertas_enviadas.json
  momentum_hunter/estado_diario.json
  momentum_hunter/watchlist.json
  momentum_hunter/auditoria
  momentum_hunter/telemetria
  momentum_paper_trader/revisiones.json
  momentum_paper_trader/archivo_triggered.jsonl
  momentum_paper_trader/telemetria
)
# Mismos mensajes que usaba GHA: el historial de git sigue siendo
# legible con los mismos grep de siempre.
if [ "$MODE" = "escaneo" ]; then
  MSG="momentum_hunter: alertas y auditoría del día [skip ci]"
else
  MSG="momentum_hunter: re-chequeo de watchlist [skip ci]"
fi

git config user.name "momentum-opportunity-hunter" || true
git config user.email "momentum-opportunity-hunter@users.noreply.github.com" || true

stage_estado() {
  for p in "${PATHS[@]}"; do
    if [ -e "$p" ]; then git add -A -- "$p" || true; fi
  done
}

# 1) Sobras de una corrida anterior cuyo push falló se commitean ANTES
#    del pull. `git pull --rebase` se niega con cambios sin stagear;
#    hasta hoy eso se tragaba con un WARN y el VPS quedaba desalineado
#    de main sin que nada lo dijera.
stage_estado
if ! git diff --cached --quiet 2>/dev/null; then
  git commit -q -m "$MSG" || true
fi
if ! git pull --rebase origin main >/dev/null 2>&1; then
  git rebase --abort >/dev/null 2>&1 || true
  echo "WARN: git pull --rebase failed (continuing con el árbol local)"
fi

# 2) Correr. Los rc se guardan y se evalúan al final: se persiste siempre.
hunter_rc=0
paper_rc=0
if [ "$MODE" = "escaneo" ]; then
  "$PY" -m momentum_hunter.run --limit "${MOMENTUM_SCAN_LIMIT:-1000}"
else
  "$PY" -m momentum_hunter.run --solo-watchlist
fi
hunter_rc=$?
"$PY" -m momentum_paper_trader.run
paper_rc=$?

# 3) Persistir. Nunca se fuerza el push (git_persist_rebase_push.sh lo rechaza).
stage_estado
if ! git diff --cached --quiet 2>/dev/null; then
  git commit -q -m "$MSG" || true
  if ! PERSIST_BRANCH=main bash "$ROOT/scripts/git_persist_rebase_push.sh"; then
    echo "WARN: git persist failed. Local files kept. Not failing unit."
  fi
else
  echo "INFO: nothing to persist"
fi

if [ "$hunter_rc" -ne 0 ]; then
  echo "ERROR: hunter rc=$hunter_rc"
  exit "$hunter_rc"
fi
if [ "$paper_rc" -ne 0 ]; then
  echo "ERROR: paper rc=$paper_rc"
  exit "$paper_rc"
fi
exit 0
