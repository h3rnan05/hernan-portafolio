#!/usr/bin/env bash
# Deja esta rama mergeable con origin/main.
#
# POR QUÉ. El VPS sigue commiteando telemetría en los paths que el PR #200
# borra. GitHub ve modify/delete, marca el PR dirty y no arma el merge
# commit: sin ese commit no dispara pull_request y Actions no corre.
# Hay que repetir este script justo antes del merge a main. No es el
# deploy del VPS: no toca /var/lib, no para servicios y no suelta stashes.
#
# QUÉ HACE. Trae origin/main. Un modify/delete (o un archivo nuevo) dentro
# de los paths de estado se resuelve con git rm: el borrado gana y el
# archivo no vuelve al índice. Un conflicto de código en un archivo que
# esta rama no reescribió se queda con la versión de main. Si el conflicto
# cae en un archivo que esta rama sí cambió, el script se detiene: pisarlo
# entero perdería el directorio de estado.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: el árbol no está limpio. No se mezcla." >&2
  exit 2
fi

git fetch origin main

# Misma lista que momentum_hunter/rutas_estado.py (RELATIVOS).
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

# Archivos de código que esta rama reescribió. Un `checkout --theirs`
# entero los devolvería a main y se perdería el directorio de estado.
NUESTROS=(
  .github/scripts/momentum_paper_cadence_bridge.py
  .github/scripts/respaldo_gha.py
  .github/workflows/momentum_hunter.yml
  .github/workflows/momentum_hunter_outcomes.yml
  .github/workflows/momentum_hunter_watchlist.yml
  .gitignore
  CLAUDE.md
  dashboard/build_dashboard.py
  infra/systemd/README.md
  infra/systemd/bin/run_scan_paper.sh
  infra/systemd/bin/run_vigia.sh
  infra/systemd/bin/run_watchlist_paper.sh
  infra/systemd/momentum-movers-sombra.service
  infra/systemd/momentum-scan.service
  infra/systemd/momentum-vigia.service
  infra/systemd/momentum-watchlist.service
  momentum_hunter/audit.py
  momentum_hunter/diario.py
  momentum_hunter/heartbeat.py
  momentum_hunter/reporte_semanal.py
  momentum_hunter/rutas_estado.py
  momentum_hunter/telemetria.py
  momentum_hunter/tracker.py
  momentum_hunter/universe.py
  momentum_hunter/watchlist.py
  momentum_paper_trader/archivo.py
  momentum_paper_trader/estado.py
  momentum_paper_trader/executor.py
  momentum_paper_trader/telemetria.py
  scripts/deploy_vps.sh
  scripts/run_watchlist_paper.sh
)

es_bajo() {
  local f="$1" p
  for p in "${PATHS[@]}"; do
    if [ "$f" = "$p" ] || [[ "$f" == "$p"/* ]]; then
      return 0
    fi
  done
  return 1
}

es_nuestro() {
  local f="$1" p
  for p in "${NUESTROS[@]}"; do
    if [ "$f" = "$p" ]; then
      return 0
    fi
  done
  return 1
}

if git merge-base --is-ancestor origin/main HEAD; then
  echo "INFO: origin/main ya está en HEAD"
else
  if ! git merge origin/main --no-edit; then
    echo "INFO: el merge dejó conflictos; se resuelven"
  fi
fi

while IFS= read -r f; do
  [ -n "$f" ] || continue
  if es_bajo "$f"; then
    git rm -f -- "$f"
    echo "INFO: borrado $f"
  elif es_nuestro "$f"; then
    echo "ERROR: conflicto en $f, que esta rama también cambió. No se pisa entero." >&2
    exit 1
  else
    git checkout --theirs -- "$f"
    git add -- "$f"
    echo "INFO: $f queda como origin/main"
  fi
done < <(git diff --name-only --diff-filter=U)

# main puede agregar un archivo nuevo bajo esos prefijos (no es
# modify/delete: no existía en esta rama). También sale del índice.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  git rm -f -- "$f"
  echo "INFO: fuera del índice $f"
done < <(git ls-files -- "${PATHS[@]}")

if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
  echo "ERROR: quedan conflictos" >&2
  git diff --name-only --diff-filter=U >&2
  exit 1
fi

if [ -n "$(git ls-files -- "${PATHS[@]}")" ]; then
  echo "ERROR: todavía hay estado trackeado" >&2
  git ls-files -- "${PATHS[@]}" >&2
  exit 1
fi

if git diff --cached --quiet && git diff --quiet; then
  echo "INFO: nada que commitear"
  exit 0
fi

if [ -f .git/MERGE_HEAD ]; then
  git commit -m "Merge origin/main: la telemetría nueva se queda fuera del índice."
else
  git commit -m "Saca del índice la telemetría que main volvió a trackear."
fi
echo "INFO: listo en $(git rev-parse --short HEAD)"
