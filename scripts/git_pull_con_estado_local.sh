#!/usr/bin/env bash
# Trae origin/main aunque el estado que el VPS escribe entre commits
# esté sucio.
#
# POR QUÉ. `git pull --rebase` se niega en seco si hay cambios sin
# stage ("cannot pull with rebase: You have unstaged changes"), aunque
# el incoming no toque esos archivos. El vigía reescribe
# `momentum_paper_trader/telemetria/.../events.jsonl` y `sesion.json`
# cada 60 s, y el wrapper hacía el pull ANTES del `git add`: el 2026-09-22
# ese pull falló en casi cada persist (~73 en el día) y el código ya
# mergeado no entraba al VPS.
#
# No se saca la telemetría del worktree. El state file de #132 es para
# la watchlist, que el VPS no debía commitear. Esta telemetría SÍ tiene
# que llegar a main: es el latido que lee el respaldo de GitHub. Una
# segunda copia fuera de git puede perder líneas. Acá se aparta un
# momento (stash de esos paths, untracked incluido), se trae main y se
# devuelve. Nunca --force ni reset --hard: si el stash no vuelve, se
# queda en `git stash list`.
#
# Si todavía no se puede rebasear (otro archivo sucio, o un escritor
# concurrente que ensució de nuevo) y NO hay commits locales por
# reaplicar, se intenta `pull --ff-only`: main entra igual cuando el
# incoming no pisa lo sucio. Con commits locales no se usa ff-only:
# los dejaría atrás.
#
# PAPER ONLY. No toca umbrales, la IA ni el endpoint de Alpaca.
# Hay que ejecutarlo con el cwd en el repo (los wrappers ya hacen cd).
set -u

REMOTE="${PERSIST_REMOTE:-origin}"
BRANCH="${PERSIST_BRANCH:-main}"

# Unión de lo que commitean run_watchlist_paper.sh y run_scan_paper.sh.
# Un path nuevo en esos arrays que no esté acá vuelve a trabar el pull:
# lo cubre test_el_helper_aparta_todos_los_paths_que_los_wrappers_commitean.
PATHS_ESTADO=(
  momentum_hunter/watchlist.json
  momentum_hunter/auditoria
  momentum_hunter/alertas_enviadas.json
  momentum_hunter/estado_diario.json
  momentum_hunter/universo_cache.json
  momentum_hunter/telemetria
  momentum_paper_trader/revisiones.json
  momentum_paper_trader/archivo_triggered.jsonl
  momentum_paper_trader/telemetria
)

for arg in "$@"; do
  case "$arg" in
    --force|--force-with-lease|-f)
      echo "ERROR: force push is forbidden" >&2
      exit 2
      ;;
    *)
      echo "ERROR: argumento no reconocido: ${arg}" >&2
      exit 2
      ;;
  esac
done

PATHS_OK=()
_armar_paths() {
  PATHS_OK=()
  local p
  for p in "${PATHS_ESTADO[@]}"; do
    # Un directorio que todavía no existe no se stashea: `stash push`
    # falla entero si un pathspec no matchea, y nos quedaríamos sin pull.
    if [ -e "$p" ] || git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then
      PATHS_OK+=("$p")
    fi
  done
}

_hay_cambios_en_estado() {
  if [ "${#PATHS_OK[@]}" -eq 0 ]; then
    return 1
  fi
  if ! git diff --quiet -- "${PATHS_OK[@]}"; then
    return 0
  fi
  if ! git diff --cached --quiet -- "${PATHS_OK[@]}"; then
    return 0
  fi
  local sin_track
  sin_track="$(git ls-files --others --exclude-standard -- "${PATHS_OK[@]}")"
  [ -n "$sin_track" ]
}

_ref_stash() {
  git rev-parse -q --verify refs/stash 2>/dev/null || true
}

_armar_paths

if [ "${GIT_PULL_ESTADO_DRY_RUN:-0}" = "1" ]; then
  # Solo lectura: no aborta un rebase, no stashea, no hace pull.
  echo "INFO: dry-run: no se aparta estado ni se hace pull"
  if _hay_cambios_en_estado; then
    echo "INFO: dry-run: hay estado local que se apartaría"
    git status --short -- "${PATHS_OK[@]}"
  else
    echo "INFO: dry-run: los paths de estado están limpios"
  fi
  exit 0
fi

# Rebase a medias de una corrida anterior: abortarlo devuelve al commit
# de antes del rebase (los commits locales siguen). Sin esto el pull
# nuevo también se niega y el VPS queda sin main.
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "WARN: rebase a medias; se aborta para poder traer ${BRANCH} (los commits locales quedan)"
  git rebase --abort >/dev/null 2>&1 || true
fi

STASHED=0
STASH_COMMIT=""
if _hay_cambios_en_estado; then
  antes="$(_ref_stash)"
  # --include-untracked: el events.jsonl de un día nuevo todavía no
  # está trackeado. El pathspec no guarda el resto del árbol (un .py
  # editado a mano no se esconde detrás del pull).
  if git stash push --include-untracked -m "pre-pull estado local $(date -u +%FT%TZ)" -- "${PATHS_OK[@]}"; then
    despues="$(_ref_stash)"
    if [ -n "$despues" ] && [ "$despues" != "$antes" ]; then
      STASHED=1
      STASH_COMMIT="$despues"
      echo "INFO: estado local apartado (stash) para poder traer ${BRANCH}"
    else
      echo "WARN: stash no creó una entrada nueva; no se va a hacer pop de un stash ajeno"
    fi
  else
    echo "WARN: no se pudo apartar el estado local; se intenta el pull igual"
  fi
fi

_pull_rebase() {
  if git pull --rebase "$REMOTE" "$BRANCH"; then
    return 0
  fi
  # Si el primero dejó un rebase a medias (conflicto), hay que abortar
  # antes del segundo intento. Si se negó por árbol sucio, el abort
  # no encuentra nada y sigue.
  git rebase --abort >/dev/null 2>&1 || true
  if git pull --rebase; then
    return 0
  fi
  git rebase --abort >/dev/null 2>&1 || true
  return 1
}

pull_ok=0
if _pull_rebase; then
  pull_ok=1
else
  echo "WARN: git pull --rebase falló"
  ahead="?"
  if git rev-parse --verify -q "$REMOTE/$BRANCH" >/dev/null 2>&1; then
    ahead="$(git rev-list --count "$REMOTE/$BRANCH"..HEAD 2>/dev/null || echo '?')"
  fi
  if [ "$ahead" = "0" ]; then
    if git pull --ff-only "$REMOTE" "$BRANCH"; then
      echo "INFO: main entró con ff-only (sin commits locales que reaplicar)"
      pull_ok=1
    else
      echo "WARN: git pull --ff-only también falló"
    fi
  else
    echo "WARN: hay commits locales (${ahead}); no se usa ff-only para no dejarlos atrás"
  fi
fi

pop_ok=1
if [ "$STASHED" -eq 1 ]; then
  cima="$(_ref_stash)"
  if [ "$cima" != "$STASH_COMMIT" ]; then
    echo "WARN: el stash recién creado ya no está en la cima; no se hace pop a ciegas (${STASH_COMMIT})"
    pop_ok=0
  elif git stash pop; then
    echo "INFO: estado local restaurado"
  else
    # `stash pop` no tira la entrada si el apply chocó. Lo que aplicó
    # limpio (el JSONL, append-only) se queda en el árbol. Los paths
    # con marcadores se devuelven a HEAD: un `git add` posterior no
    # puede commitearlos. sesion.json es un rollup; el próximo tick lo
    # reescribe desde el JSONL. No se hace reset --hard.
    echo "WARN: stash pop falló; el estado sigue en 'git stash list' (no se descarta)"
    if git diff --name-only --diff-filter=U | grep -q .; then
      git diff --name-only --diff-filter=U | while read -r f; do
        git checkout HEAD -- "$f" || true
        git reset -q HEAD -- "$f" || true
      done
      echo "WARN: marcadores de conflicto limpiados; el JSONL aplicado limpio se conserva"
    fi
    pop_ok=0
  fi
fi

if [ "$pop_ok" -eq 0 ]; then
  # 3: no reintentar desde git_persist_rebase_push.sh. Otro ciclo
  # volvería a stashar y anidaría el estado.
  exit 3
fi
if [ "$pull_ok" -eq 1 ]; then
  exit 0
fi
exit 1
