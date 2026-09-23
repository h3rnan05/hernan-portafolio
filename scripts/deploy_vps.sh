#!/usr/bin/env bash
# Despliegue del VPS desde GitHub Actions (2026-09-23).
#
# POR QUÉ EXISTE. El VPS baja el código de main en el persist del vigía, que
# solo corre en sesión (13:00-20:01 UTC). Fuera de sesión, un cambio ya
# fusionado no llega hasta el día siguiente, y los wrappers de
# /opt/momentum/bin/ NUNCA se actualizan con un pull: hay que instalarlos a
# mano (ver infra/systemd/README.md). Este script hace EXACTAMENTE la
# secuencia documentada ahí, y nada más, para que Actions la corra por SSH
# con la llave guardada en GitHub Secrets (regla 7 del CLAUDE.md: las
# credenciales viven ahí, no en un chat ni en otro entorno).
#
# Se manda por stdin (`ssh ... 'bash -s' < scripts/deploy_vps.sh`) para no
# depender de que el VPS ya tenga esta versión del script.
#
# QUÉ HACE, en orden, y se detiene en el primer fallo:
#   1. Se niega a correr en sesión (13:00-20:05 UTC, lun-vie) salvo
#      FORZAR_EN_SESION=1: un pull a mitad de un persist del vigía podría
#      pisarse con él.
#   2. Aparta el estado del VPS (telemetría, revisiones, watchlist...) con
#      `git stash`, trae main con `git pull --rebase` y devuelve el estado
#      con `git stash pop`. Nunca `--force`, nunca `reset --hard`.
#   3. Instala los dos wrappers en /opt/momentum/bin/ con `sudo -n`.
#   4. Imprime antes/después y el estado del vigía. No reinicia nada: el
#      vigía toma el código en su próximo tick.
#
# QUÉ NO HACE: no toca credenciales, no reinicia servicios, no borra nada.
# Si el stash pop deja conflicto, el estado sigue en `git stash list` y el
# script sale con error para que un humano mire.
set -euo pipefail

REPO="${REPO:-/opt/hernan-portafolio}"
BIN="${BIN:-/opt/momentum/bin}"
FORZAR_EN_SESION="${FORZAR_EN_SESION:-0}"

# Paths de estado que el VPS escribe y que no deben estorbar al pull (la
# misma lista que infra/systemd/README.md, "Pull con la telemetría sucia").
ESTADO=(
  momentum_paper_trader/telemetria
  momentum_hunter/telemetria
  momentum_paper_trader/revisiones.json
  momentum_paper_trader/archivo_triggered.jsonl
  momentum_hunter/watchlist.json
  momentum_hunter/auditoria
  momentum_hunter/alertas_enviadas.json
  momentum_hunter/estado_diario.json
  momentum_hunter/universo_cache.json
)

log() { printf '[deploy_vps] %s\n' "$*"; }

# 1. Ventana: fuera de sesión, salvo que se fuerce.
dow=$(date -u +%u)      # 1 = lunes ... 7 = domingo
hhmm=$(date -u +%H%M)
if [ "$FORZAR_EN_SESION" != "1" ] && [ "$dow" -le 5 ] && [ "$hhmm" -ge 1300 ] && [ "$hhmm" -le 2005 ]; then
  log "estamos en sesión ($(date -u +%H:%M) UTC): no se despliega para no pisarse con el persist del vigía."
  log "Vuelve a correr fuera de 13:00-20:05 UTC, o con FORZAR_EN_SESION=1 si sabes lo que haces."
  exit 3
fi

cd "$REPO"
antes=$(git rev-parse --short HEAD)
log "repo $REPO en $antes (rama $(git rev-parse --abbrev-ref HEAD))"

# 2. Apartar estado, traer main, devolver estado.
existentes=()
for p in "${ESTADO[@]}"; do
  [ -e "$p" ] && existentes+=("$p")
done
hubo_stash=0
if [ "${#existentes[@]}" -gt 0 ] && ! git diff --quiet -- "${existentes[@]}" 2>/dev/null \
   || [ -n "$(git ls-files --others --exclude-standard -- "${existentes[@]}" 2>/dev/null)" ]; then
  git stash push --include-untracked -m "deploy_vps $(date -u +%FT%TZ)" -- "${existentes[@]}" >/dev/null
  hubo_stash=1
  log "estado apartado en git stash"
fi

git pull --rebase origin main
despues=$(git rev-parse --short HEAD)

if [ "$hubo_stash" = "1" ]; then
  if git stash pop >/dev/null; then
    log "estado devuelto"
  else
    log "ERROR: git stash pop dejó conflicto. El estado sigue en 'git stash list'; hace falta un humano."
    exit 4
  fi
fi
log "main: $antes -> $despues"

# 3. Wrappers: un pull no los actualiza.
if sudo -n true 2>/dev/null; then
  sudo -n install -m 755 infra/systemd/bin/run_watchlist_paper.sh infra/systemd/bin/run_scan_paper.sh "$BIN/"
  log "wrappers instalados en $BIN"
else
  log "AVISO: sudo pide contraseña en este host; los wrappers NO se instalaron. El pull sí quedó hecho."
  exit 5
fi

# 4. Foto final, sin tocar nada.
log "vigía: $(systemctl is-active momentum-vigia.service 2>/dev/null || echo desconocido)"
log "árbol: $(git status --short | wc -l | tr -d ' ') archivo(s) con cambios locales (estado del VPS, es normal)"
