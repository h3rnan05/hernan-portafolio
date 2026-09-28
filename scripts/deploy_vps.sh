#!/usr/bin/env bash
# Despliegue del VPS desde GitHub Actions (2026-09-23).
#
# POR QUÉ EXISTE. El VPS baja el código de main en el persist del vigía, que
# solo corre en sesión (el código gatea con el calendario; la tapa UTC
# ancha es 13:00-21:30). Fuera de sesión, un cambio ya
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
#   1. Se niega a correr en el tramo ancho (13:00-21:35 UTC, lun-vie) salvo
#      FORZAR_EN_SESION=1: un pull a mitad de un persist del vigía podría
#      pisarse con él.
#   2. Copia el estado a MOMENTUM_ESTADO_DIR si el destino no existe
#      (no pisa una copia más nueva). Comprueba que revisiones.json
#      quedó. Recién entonces limpia esos paths del worktree y trae
#      main. No se aparta el estado: un pop fallido fue el
#      incidente del 2026-09-28. Nunca `--force`, nunca `reset --hard`.
#   3. Instala los wrappers en /opt/momentum/bin/ con `sudo -n`.
#   4. Imprime antes/después y el estado del vigía. No reinicia nada:
#      el daemon-reload y el restart los hace el operador (ver README).
#
# QUÉ NO HACE: no toca credenciales, no reinicia servicios, no borra nada.
# Si la copia de revisiones.json no queda, el script sale con error
# y no trae main.
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
  momentum_hunter/diario
)

log() { printf '[deploy_vps] %s\n' "$*"; }

# 1. Ventana: fuera de sesión, salvo que se fuerce.
dow=$(date -u +%u)      # 1 = lunes ... 7 = domingo
hhmm=$(date -u +%H%M)
if [ "$FORZAR_EN_SESION" != "1" ] && [ "$dow" -le 5 ] && [ "$hhmm" -ge 1300 ] && [ "$hhmm" -le 2135 ]; then
  log "estamos en sesión ($(date -u +%H:%M) UTC): no se despliega para no pisarse con el persist del vigía."
  log "Vuelve a correr fuera de 13:00-21:35 UTC, o con FORZAR_EN_SESION=1 si sabes lo que haces."
  exit 3
fi

cd "$REPO"
antes=$(git rev-parse --short HEAD)
log "repo $REPO en $antes (rama $(git rev-parse --abbrev-ref HEAD))"

# 2. Copiar fuera del repo ANTES del pull. El commit que saca estos
# paths del índice no puede aplicarse encima de un worktree sucio, y
# un pop abortado se los lleva. /var/lib/momentum es del usuario
# momentum (0750): si hay sudo sin contraseña, la copia corre así.
ESTADO_DIR="${MOMENTUM_ESTADO_DIR:-/var/lib/momentum/estado}"
if sudo -n true 2>/dev/null; then
  sudo -n mkdir -p "$ESTADO_DIR"
  sudo -n chown momentum:momentum "$ESTADO_DIR"
else
  mkdir -p "$ESTADO_DIR"
fi
for p in "${ESTADO[@]}"; do
  dest="$ESTADO_DIR/$p"
  if [ -e "$dest" ]; then
    log "ya existe $dest; no se pisa"
    continue
  fi
  if [ -e "$p" ]; then
    if sudo -n true 2>/dev/null; then
      sudo -n mkdir -p "$(dirname "$dest")"
      sudo -n cp -a "$p" "$dest"
      sudo -n chown -R momentum:momentum "$dest"
    else
      mkdir -p "$(dirname "$dest")"
      cp -a "$p" "$dest"
    fi
    log "copiado $p -> $dest"
  fi
done
if [ -f momentum_paper_trader/revisiones.json ]; then
  if [ ! -s "$ESTADO_DIR/momentum_paper_trader/revisiones.json" ]; then
    log "ERROR: revisiones.json no quedó en $ESTADO_DIR. No se hace pull."
    exit 4
  fi
fi

existentes=()
for p in "${ESTADO[@]}"; do
  [ -e "$p" ] && existentes+=("$p")
done
if [ "${#existentes[@]}" -gt 0 ]; then
  git checkout -- "${existentes[@]}"
  log "worktree de estado alineado a HEAD; la copia viva está en $ESTADO_DIR"
fi

git pull --rebase origin main
despues=$(git rev-parse --short HEAD)
log "main: $antes -> $despues"

# 3. Wrappers: un pull no los actualiza.
if sudo -n true 2>/dev/null; then
  sudo -n install -m 755 infra/systemd/bin/run_watchlist_paper.sh infra/systemd/bin/run_scan_paper.sh infra/systemd/bin/run_movers_sombra.sh "$BIN/"
  log "wrappers instalados en $BIN"
else
  log "AVISO: sudo pide contraseña en este host; los wrappers NO se instalaron. El pull sí quedó hecho."
  exit 5
fi

# 4. Foto final, sin tocar nada.
log "vigía: $(systemctl is-active momentum-vigia.service 2>/dev/null || echo desconocido)"
log "árbol: $(git status --short | wc -l | tr -d ' ') archivo(s) con cambios locales (estado del VPS, es normal)"
