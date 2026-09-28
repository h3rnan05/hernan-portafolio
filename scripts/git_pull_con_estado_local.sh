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
# Si `stash pop` aborta porque otro proceso escribió un path de estado
# entre el stash y el pop ("would be overwritten", 2026-09-28: la sombra
# de movers escribió movers.jsonl y el stash entero se abandonó, con
# revisiones.json adentro), no se tira nada. Se restaura archivo por
# archivo: checkout del stash si nadie más tocó ese path; en un JSONL
# que sí cambió, se anexan las líneas del stash que el archivo no tiene,
# sin duplicar. Solo si queda algo sin restaurar se conserva el stash,
# se loguea ERROR y se emite persist_fallido (mismo evento y mismo
# Telegram deduplicado por día que el wrapper). Nunca se dropea un stash
# a medias para "seguir".
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

# ── recuperación si stash pop no aplica (2026-09-28) ──
# El pop es atómico: si un archivo sucio lo impide, git no aplica NADA
# y el stash sigue entero. Un checkout a ciegas pisaría lo que el otro
# proceso acaba de escribir; un drop a ciegas tira revisiones.json.

_en_lista() {
  local lista="$1" f="$2"
  [ -n "$lista" ] || return 1
  printf '%s\n' "$lista" | grep -Fx -q -- "$f"
}

_es_jsonl() { [[ "$1" == *.jsonl ]]; }

# El incoming (el pull) no cambió este path respecto del HEAD que se stasheó.
_origen_no_toco() {
  local f="$1" en_head en_base
  en_head="$(git rev-parse -q --verify "HEAD:${f}" 2>/dev/null || true)"
  en_base="$(git rev-parse -q --verify "${STASH_COMMIT}^1:${f}" 2>/dev/null || true)"
  [ "$en_head" = "$en_base" ]
}

# El árbol de trabajo coincide con HEAD: ningún otro proceso lo reescribió
# después del stash. Un archivo sin trackear que volvió a aparecer sí es
# un toque (el stash se lo había llevado).
_wt_igual_a_head() {
  local f="$1"
  if git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
    git diff --quiet HEAD -- "$f"
    return
  fi
  if [ -e "$f" ]; then
    return 1
  fi
  if git cat-file -e "HEAD:${f}" 2>/dev/null; then
    return 1
  fi
  return 0
}

_archivos_del_stash() {
  {
    git diff --name-only "${STASH_COMMIT}^1" "$STASH_COMMIT" 2>/dev/null || true
    if git rev-parse -q --verify "${STASH_COMMIT}^3" >/dev/null 2>&1; then
      git ls-tree -r --name-only "${STASH_COMMIT}^3" 2>/dev/null || true
    fi
  } | awk 'NF && !visto[$0]++'
}

_volcar_blob_stash() {
  local f="$1" dest="$2" src=""
  if git cat-file -e "${STASH_COMMIT}:${f}" 2>/dev/null; then
    src="$STASH_COMMIT"
  elif git rev-parse -q --verify "${STASH_COMMIT}^3" >/dev/null 2>&1 \
       && git cat-file -e "${STASH_COMMIT}^3:${f}" 2>/dev/null; then
    src="${STASH_COMMIT}^3"
  else
    return 1
  fi
  git cat-file -p "${src}:${f}" > "$dest"
}

_checkout_stash() {
  local f="$1" src=""
  if git cat-file -e "${STASH_COMMIT}:${f}" 2>/dev/null; then
    src="$STASH_COMMIT"
  elif git rev-parse -q --verify "${STASH_COMMIT}^3" >/dev/null 2>&1 \
       && git cat-file -e "${STASH_COMMIT}^3:${f}" 2>/dev/null; then
    src="${STASH_COMMIT}^3"
  else
    return 1
  fi
  mkdir -p -- "$(dirname -- "$f")"
  git checkout "$src" -- "$f" || return 1
  # Deja el cambio en el árbol, sin stage: igual que un pop limpio.
  git reset -q HEAD -- "$f" || true
  return 0
}

# Anexa las líneas del stash que el archivo actual no tiene. No reescribe
# lo que el otro proceso agregó y no duplica una línea que ya está.
_anexar_lineas_jsonl() {
  local f="$1" blob tmp
  blob="$(mktemp)"
  tmp="$(mktemp)"
  if ! _volcar_blob_stash "$f" "$blob"; then
    rm -f "$blob" "$tmp"
    return 1
  fi
  mkdir -p -- "$(dirname -- "$f")"
  if [ ! -f "$f" ]; then
    : > "$f"
  fi
  awk 'NR==FNR { if (length($0)) seen[$0]=1; next }
       length($0) && !($0 in seen) { print; seen[$0]=1 }' "$f" "$blob" > "$tmp"
  cat "$tmp" >> "$f"
  local ok=0
  if ! awk 'NR==FNR { if (length($0)) seen[$0]=1; next }
             length($0) && !($0 in seen) { exit 1 }' "$f" "$blob"; then
    ok=1
  fi
  rm -f "$blob" "$tmp"
  return "$ok"
}

# 0 solo si cada path del stash quedó restaurado. Si no, el caller
# conserva el stash: dropearlo perdería lo que no volvió al árbol.
_recuperar_stash_archivo_por_archivo() {
  local f quedan=0 n=0 conflictos
  conflictos="$(git diff --name-only --diff-filter=U 2>/dev/null || true)"
  if [ -n "$conflictos" ]; then
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      git checkout HEAD -- "$f" || true
      git reset -q HEAD -- "$f" || true
    done <<< "$conflictos"
    echo "WARN: marcadores de conflicto limpiados; un JSONL en conflicto se reconstruye desde el stash sin duplicar"
  fi

  while IFS= read -r f; do
    [ -z "$f" ] && continue
    n=$((n + 1))
    if _es_jsonl "$f" && { _en_lista "$conflictos" "$f" || ! _origen_no_toco "$f" || ! _wt_igual_a_head "$f"; }; then
      if _anexar_lineas_jsonl "$f"; then
        echo "INFO: JSONL restaurado sin duplicar: ${f}"
      else
        echo "ERROR: no se pudieron anexar las líneas de ${f}; quedan en el stash"
        quedan=1
      fi
      continue
    fi
    if ! _en_lista "$conflictos" "$f" && _origen_no_toco "$f" && _wt_igual_a_head "$f"; then
      if _checkout_stash "$f"; then
        echo "INFO: restaurado desde el stash: ${f}"
      else
        echo "ERROR: no se pudo restaurar ${f} desde el stash"
        quedan=1
      fi
    else
      echo "ERROR: ${f} lo tocó otro proceso o el incoming; no se pisa y sigue en el stash"
      quedan=1
    fi
  done < <(_archivos_del_stash)

  if [ "$n" -eq 0 ]; then
    echo "ERROR: el stash no listó archivos; no se dropea"
    return 1
  fi
  [ "$quedan" -eq 0 ]
}

# Mismo canal que persist_fallido() de los wrappers: evento del panel
# (siempre) y Telegram a lo sumo una vez por día (misma marca). Un fallo
# de aviso no puede tumbar el pull ni justificar dropear el stash.
_avisar_persist_fallido() {
  local motivo="$1" py="python3" hoy marca avisos notify
  if [ -n "${MOMENTUM_PY:-}" ] && [ -x "${MOMENTUM_PY}" ]; then
    py="${MOMENTUM_PY}"
  elif [ -x .venv/bin/python ]; then
    py=".venv/bin/python"
  fi
  "$py" -m dashboard.events persist_fallido "motivo=${motivo}" "intentos=0" >/dev/null 2>&1 || true
  avisos="${MOMENTUM_AVISOS_DIR:-/var/lib/momentum}"
  marca="${avisos}/persist_fallido.avisado"
  hoy="$(date -u +%F)"
  if [ -f "$marca" ] && [ "$(cat "$marca" 2>/dev/null || true)" = "$hoy" ]; then
    echo "INFO: persist_fallido ya avisado hoy por Telegram; no se repite"
    return 0
  fi
  notify="${MOMENTUM_NOTIFY_SH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/notify_telegram.sh}"
  if [ -f "$notify" ] && bash "$notify" \
      "ERROR [paper][vps] persist fallido: ${motivo} (intentos=0). Parte del estado sigue solo en git stash list; no se descartó." \
      >/dev/null 2>&1; then
    mkdir -p "$avisos" 2>/dev/null || true
    echo "$hoy" > "$marca" 2>/dev/null || true
  else
    echo "WARN: telegram notify de persist_fallido no enviado (sin credenciales o sin red)"
  fi
  return 0
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
    # `stash pop` no tira la entrada si el apply chocó o si abortó antes
    # de tocar el árbol ("would be overwritten"). Lo que un merge dejó
    # limpio se conserva. Lo que no volvió se busca en el stash, path
    # por path. sesion.json en conflicto real se deja en HEAD (es un
    # rollup; el próximo tick lo reescribe) y el stash se conserva.
    # No se hace reset --hard ni stash drop si quedó algo afuera.
    echo "WARN: stash pop falló; se restaura archivo por archivo (no se descarta)"
    if _recuperar_stash_archivo_por_archivo; then
      if [ "$(_ref_stash)" = "$STASH_COMMIT" ]; then
        if ! git stash drop; then
          echo "WARN: el estado volvió al árbol pero no se pudo dropear el stash; queda como copia"
        fi
      else
        echo "WARN: el estado volvió al árbol pero el stash ya no está en la cima; no se dropea a ciegas"
      fi
      echo "INFO: estado local restaurado archivo por archivo"
      pop_ok=1
    else
      echo "ERROR: persist_fallido motivo=stash pop incompleto (el stash se conserva; no se descarta)"
      _avisar_persist_fallido "stash pop incompleto"
      pop_ok=0
    fi
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
