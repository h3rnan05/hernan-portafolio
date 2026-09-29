#!/usr/bin/env bash
# Tareas de SOLO LECTURA que tienen que correr en el VPS porque desde
# GitHub Actions no se llega a la fuente (sec.gov responde 403 a los
# runners; verificado el 2026-09-29). Lo manda por stdin
# `.github/workflows/vps_tarea.yml` con la misma llave que el deploy.
#
# NUNCA toca el checkout de producción (/opt/hernan-portafolio) ni su
# estado (/var/lib/momentum): el runner hace `git archive` de la rama
# pedida y lo sube por ssh a un directorio de trabajo del usuario SSH
# ($CODIGO), y el backtest escribe su almacén y su estado ahí. Usa el venv
# de producción solo como intérprete (mismas dependencias). No coloca
# órdenes, no versiona nada, no reinicia servicios.
#
# Tareas (variable TAREA):
#   verificar_fuentes  exporta REF_FUENTES (por defecto fuentes/edgar-acciones,
#                      que contiene #210–#214), corre sus pruebas y graba
#                      respuestas reales de EDGAR: imprime "ZONA OK" o
#                      "ZONA A REVISAR". Sale con error si no es OK.
#   pead_lanzar        exporta REF y lanza en segundo plano el backtest
#                      `pead_u1` (8-K 2.02 real, universo $2–20, sin IA)
#                      entre DESDE y HASTA, con nice/ionice. Se niega si
#                      ya hay uno corriendo.
#   pead_estado        pid vivo o no, cola del log, archivos de salida.
#   pead_recoger       imprime el informe y deja los archivos en SALIDA
#                      para que el workflow los baje con scp.
#   pead_cancelar      mata el proceso si sigue vivo.
set -euo pipefail

TAREA="${TAREA:?TAREA es obligatoria}"
REF="${REF:-main}"
REF_FUENTES="${REF_FUENTES:-fuentes/edgar-acciones}"
DESDE="${DESDE:-2023-09-26}"
HASTA="${HASTA:-2026-09-25}"
REPO="${REPO:-/opt/hernan-portafolio}"
PY="${PY:-$REPO/.venv/bin/python}"
TRABAJO="${TRABAJO:-$HOME/momentum-tareas}"
CODIGO="$TRABAJO/codigo"
SALIDA="$TRABAJO/salida"
PAPER_ENV="${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"

log() { printf '[vps_tarea] %s\n' "$*"; }

exportar() {   # exportar <ref> <destino>: el código ya lo subió el workflow por ssh (tar)
  # El checkout de producción es de otro usuario (`.git/FETCH_HEAD:
  # Permission denied` el 2026-09-29) y no se toca: el runner de Actions
  # hace `git archive` de la ref y lo manda por ssh a $CODIGO antes de
  # correr este script. Aquí solo se comprueba que llegó.
  local ref="$1" destino="$2"
  if [ ! -s "$destino/CODIGO_REF" ]; then
    log "ERROR: no hay código en $destino (el workflow debía subir $ref antes)"; exit 6
  fi
  log "código de $ref ($(cat "$destino/CODIGO_REF")) en $destino"
}

cargar_credenciales() {
  # Solo lo que el backtest lee: claves del host de DATOS de Alpaca y el
  # User-Agent con contacto para la SEC. Nunca se imprime nada de esto.
  if [ -r "$PAPER_ENV" ]; then
    set -a; . "$PAPER_ENV"; set +a
  elif sudo -n true 2>/dev/null; then
    set -a; . <(sudo -n cat "$PAPER_ENV"); set +a
  else
    log "ERROR: no puedo leer $PAPER_ENV"; exit 4
  fi
  if [ -z "${ALPACA_PAPER_API_KEY:-}" ] || [ -z "${ALPACA_PAPER_API_SECRET:-}" ]; then
    log "ERROR: faltan las claves de Alpaca en $PAPER_ENV"; exit 4
  fi
  export ALPACA_PAPER_API_KEY ALPACA_PAPER_API_SECRET
  # La SEC exige un contacto en el User-Agent. Si paper.env no trae
  # uno, se usa la dirección noreply del repo (igual que en Actions).
  export FUENTES_SEC_USER_AGENT="${FUENTES_SEC_USER_AGENT:-hernan-portafolio vps-tarea github-actions@users.noreply.github.com}"
}

case "$TAREA" in
  verificar_fuentes)
    exportar "$REF_FUENTES" "$CODIGO"
    cd "$CODIGO"
    cargar_credenciales
    mkdir -p "$SALIDA/fuentes"
    export TMPDIR="$TRABAJO/tmp"; mkdir -p "$TMPDIR"
    export FUENTES_CACHE_DIR="$TRABAJO/fuentes_cache"
    if "$PY" -c "import pytest" 2>/dev/null; then
      "$PY" -m pytest fuentes/tests -q 2>&1 | tail -3
    else
      log "pytest no está en el venv del VPS: las pruebas ya corren en CI; aquí solo datos reales"
    fi
    rc=0
    for t in NTLA AAPL MRNA; do
      log "== grabar edgar $t"
      "$PY" -m fuentes grabar edgar "$t" --dir "$SALIDA/fuentes" || rc=$?
    done
    log "== grabar edgar_form4 NTLA"
    "$PY" -m fuentes grabar edgar_form4 NTLA --dir "$SALIDA/fuentes" || rc=$?
    log "== grabar edgar_acciones NTLA"
    "$PY" -m fuentes grabar edgar_acciones NTLA --dir "$SALIDA/fuentes" || rc=$?
    log "veredicto global: $([ "$rc" = 0 ] && echo OK || echo "FALLO rc=$rc")"
    exit "$rc"
    ;;

  pead_lanzar)
    if [ -f "$TRABAJO/pead.pid" ] && kill -0 "$(cat "$TRABAJO/pead.pid")" 2>/dev/null; then
      log "ya hay un backtest corriendo (pid $(cat "$TRABAJO/pead.pid")); no se lanza otro"; exit 3
    fi
    exportar "$REF" "$CODIGO"
    cd "$CODIGO"
    cargar_credenciales
    mkdir -p "$SALIDA" "$TRABAJO/almacen" "$TRABAJO/estado/momentum_hunter"
    # Estado y almacén propios: el universo (#200) escribe su caché en
    # MOMENTUM_ESTADO_DIR y no debe pisar el de producción.
    export MOMENTUM_ESTADO_DIR="$TRABAJO/estado" MOMENTUM_ESTADO_MIGRAR=0 PYTHONUNBUFFERED=1
    export FUENTES_CACHE_DIR="$TRABAJO/fuentes_cache"
    prefijo="estrategia_v2_${DESDE}_${HASTA}"
    rm -f "$SALIDA/${prefijo}_pead_u1.md" "$SALIDA/metricas_pead_u1.json"
    nohup nice -n 19 ionice -c 3 "$PY" -m shadow_alpaca.backtest_v2 \
      --desde "$DESDE" --hasta "$HASTA" --equity 5000 --slippage 0.0015 --sin-ia \
      --variante pead_u1 --refrescar-universo \
      --almacen "$TRABAJO/almacen" \
      --salida "$SALIDA/${prefijo}_pead_u1.md" --metricas "$SALIDA/metricas_pead_u1.json" \
      > "$SALIDA/pead.log" 2>&1 < /dev/null &
    echo $! > "$TRABAJO/pead.pid"
    log "lanzado pid $(cat "$TRABAJO/pead.pid"); log en $SALIDA/pead.log"
    sleep 20
    tail -n 5 "$SALIDA/pead.log" || true
    ;;

  pead_estado)
    if [ -f "$TRABAJO/pead.pid" ] && kill -0 "$(cat "$TRABAJO/pead.pid")" 2>/dev/null; then
      log "CORRIENDO pid $(cat "$TRABAJO/pead.pid")"
    else
      log "TERMINADO (o nunca lanzado)"
    fi
    log "salida:"; ls -la "$SALIDA" 2>/dev/null || true
    log "cola del log:"; tail -n 40 "$SALIDA/pead.log" 2>/dev/null || true
    df -h "$TRABAJO" | tail -1
    ;;

  pead_recoger)
    if [ -f "$TRABAJO/pead.pid" ] && kill -0 "$(cat "$TRABAJO/pead.pid")" 2>/dev/null; then
      log "todavía corre (pid $(cat "$TRABAJO/pead.pid")); no hay nada que recoger"; exit 3
    fi
    ls "$SALIDA"/*.md >/dev/null 2>&1 || { log "no hay informe en $SALIDA"; tail -n 40 "$SALIDA/pead.log" 2>/dev/null; exit 5; }
    log "informes en $SALIDA:"; ls -la "$SALIDA"
    for f in "$SALIDA"/*.md; do log "===== $f"; cat "$f"; done
    ;;

  pead_cancelar)
    if [ -f "$TRABAJO/pead.pid" ] && kill -0 "$(cat "$TRABAJO/pead.pid")" 2>/dev/null; then
      kill "$(cat "$TRABAJO/pead.pid")" && log "cancelado"
    else
      log "no había nada corriendo"
    fi
    ;;

  *)
    log "TAREA desconocida: $TAREA"; exit 2
    ;;
esac
