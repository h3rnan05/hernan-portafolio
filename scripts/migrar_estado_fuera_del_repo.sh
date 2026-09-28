#!/usr/bin/env bash
# Copia UNA vez el estado que todavía está dentro del checkout hacia
# MOMENTUM_ESTADO_DIR. Idempotente: si el destino existe, no se pisa
# (puede ser más nuevo que el legado). No borra el origen.
#
# El deploy por SSH necesita esta misma lista ANTES de que este archivo
# esté en el VPS: está repetida, inline, en scripts/deploy_vps.sh.
# Si se agrega un path acá, hay que agregarlo también ahí y en
# momentum_hunter/rutas_estado.py (RELATIVOS).
set -u
ROOT="${MOMENTUM_ROOT:-/opt/hernan-portafolio}"
cd "$ROOT"
ESTADO_DIR="${MOMENTUM_ESTADO_DIR:-/var/lib/momentum/estado}"

PATHS=(
  momentum_hunter/telemetria
  momentum_hunter/auditoria
  momentum_hunter/alertas_enviadas.json
  momentum_hunter/estado_diario.json
  momentum_hunter/universo_cache.json
  momentum_hunter/watchlist.json
  momentum_hunter/diario
  momentum_paper_trader/telemetria
  momentum_paper_trader/revisiones.json
  momentum_paper_trader/archivo_triggered.jsonl
)

mkdir -p "$ESTADO_DIR"
for p in "${PATHS[@]}"; do
  dest="$ESTADO_DIR/$p"
  if [ -e "$dest" ]; then
    echo "INFO: ya existe $dest; no se pisa"
    continue
  fi
  if [ ! -e "$p" ]; then
    echo "INFO: no hay legado $p"
    continue
  fi
  mkdir -p "$(dirname "$dest")"
  cp -a "$p" "$dest"
  echo "INFO: copiado $p -> $dest"
done

if [ -f momentum_paper_trader/revisiones.json ]; then
  if [ ! -s "$ESTADO_DIR/momentum_paper_trader/revisiones.json" ]; then
    echo "ERROR: revisiones.json legado no quedó en $ESTADO_DIR" >&2
    exit 1
  fi
fi
exit 0
