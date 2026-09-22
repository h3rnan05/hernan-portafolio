#!/usr/bin/env bash
# Persistencia git fail-closed: pull --rebase + push con reintentos.
#
# POR QUÉ. El hunter en GHA y el VPS commitean estado cada pocos
# minutos. Un `git push` a secas pierde la carrera; un `--force`
# (o force-with-lease) podría borrar la telemetría del otro escritor.
# Este script solo rebasea y reintenta. Si el rebase no se puede, se
# aborta y se deja para la próxima corrida -- nunca se fuerza.
#
# PAPER ONLY. No toca umbrales ni el endpoint de Alpaca.
set -u

MAX_INTENTOS="${PERSIST_MAX_INTENTOS:-5}"
REMOTE="${PERSIST_REMOTE:-origin}"
BRANCH="${PERSIST_BRANCH:-main}"

for arg in "$@"; do
  case "$arg" in
    --force|--force-with-lease|-f)
      echo "ERROR: force push is forbidden" >&2
      exit 2
      ;;
  esac
done

_backoff_secs() {
  # 2^intento + jitter 0-3s. Intento 1 → 2-5s, 2 → 4-7s, 3 → 8-11s...
  local intento="$1"
  local base=1
  local i=0
  while [ "$i" -lt "$intento" ]; do
    base=$((base * 2))
    i=$((i + 1))
  done
  local jitter=$((RANDOM % 4))
  echo $((base + jitter))
}

git_persist_rebase_push() {
  local intento=1
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  while [ "$intento" -le "$MAX_INTENTOS" ]; do
    # El pull vive en el helper: aparta la telemetría sucia, rebasea,
    # y si no hay commits locales cae a ff-only. Un pop fallido (rc 3)
    # no se reintenta: otro stash anidaría el estado.
    local rc_pull=0
    bash "$script_dir/git_pull_con_estado_local.sh" || rc_pull=$?
    if [ "$rc_pull" -eq 0 ] || [ "$rc_pull" -eq 3 ]; then
      if git push "$REMOTE" "HEAD:$BRANCH"; then
        if [ "$rc_pull" -eq 3 ]; then
          echo "WARN: push ok, pero el estado apartado no volvió al árbol (queda en git stash list)"
          return 1
        fi
        echo "INFO: persist ok (intento ${intento})"
        return 0
      fi
      echo "WARN: git push failed (intento ${intento}/${MAX_INTENTOS})"
    else
      echo "WARN: git pull falló (intento ${intento}/${MAX_INTENTOS}, rc=${rc_pull})"
      git rebase --abort >/dev/null 2>&1 || true
    fi
    if [ "$rc_pull" -eq 3 ]; then
      echo "ERROR: no se reintenta: un pop fallido y otro stash anidarían el estado"
      return 1
    fi
    if [ "$intento" -eq "$MAX_INTENTOS" ]; then
      break
    fi
    local secs
    secs="$(_backoff_secs "$intento")"
    echo "INFO: retry in ${secs}s (no force)"
    sleep "$secs"
    intento=$((intento + 1))
  done
  echo "ERROR: persist failed after ${MAX_INTENTOS} attempts (no force)"
  return 1
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  git_persist_rebase_push "$@"
fi
