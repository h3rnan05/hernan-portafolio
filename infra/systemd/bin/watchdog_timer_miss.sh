#!/usr/bin/env bash
# PAPER ONLY. systemd watchdog de silencio para momentum-watchlist.service
# (unidades momentum-watchlist-watchdog.service / .timer en el VPS paper).
#
# POR QUÉ. El oneshot corre ~cada 5 min en Lun–Vie 13–20 UTC. Si deja de
# terminar OK, hay que enterarse. El umbral es 1200s *dentro de la sesión*.
# Sin gracia de open, el lunes 13:00 medía ~64h desde el last_ok del viernes
# y disparaba un Telegram ERROR falso (2026-09-14, owner OK).
#
# No START/OK spam: Telegram solo en ERROR. No toca umbrales ni Alpaca.
set -u

UNIT="${WATCHDOG_UNIT:-momentum-watchlist.service}"
THRESHOLD_SEC="${WATCHDOG_THRESHOLD_SEC:-1200}"
case "$THRESHOLD_SEC" in
  ''|*[!0-9]*) THRESHOLD_SEC=1200 ;;
esac
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NOTIFY="${ROOT}/scripts/notify_telegram.sh"
PAPER_ENV="${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"

_epoch_from_date() {
  date -u -d "$1" +%s 2>/dev/null || true
}

if [ -n "${WATCHDOG_NOW_EPOCH:-}" ]; then
  now_epoch="$WATCHDOG_NOW_EPOCH"
  dow="$(date -u -d "@${now_epoch}" +%u)"
  hour="$(date -u -d "@${now_epoch}" +%H)"
  today_utc="$(date -u -d "@${now_epoch}" +%F)"
else
  now_epoch="$(date -u +%s)"
  dow="$(date -u +%u)"
  hour="$(date -u +%H)"
  today_utc="$(date -u +%F)"
fi
hour=$((10#$hour))

# 1) Fin de semana y fuera de ventana US (hora UTC 13–20 inclusive).
if [ "$dow" -gt 5 ]; then
  echo "INFO: weekend (dow=${dow} UTC); skip"
  exit 0
fi
if [ "$hour" -lt 13 ] || [ "$hour" -gt 20 ]; then
  echo "INFO: outside US window (hour=${hour} UTC); skip"
  exit 0
fi

# 2) Último oneshot OK: journalctl Finished / Deactivated successfully;
#    fallback systemd InactiveExitTimestamp.
last_ok_epoch=""
if [ "${WATCHDOG_LAST_OK_EPOCH+set}" = "set" ]; then
  last_ok_epoch="${WATCHDOG_LAST_OK_EPOCH}"
else
  if command -v journalctl >/dev/null 2>&1; then
    line="$(journalctl -u "$UNIT" --since "8 days ago" --no-pager -o short-unix 2>/dev/null \
      | grep -E 'Finished |Deactivated successfully' \
      | tail -n 1 || true)"
    if [ -n "${line:-}" ]; then
      ts="${line%% *}"
      ts="${ts%%.*}"
      case "$ts" in
        ''|*[!0-9]*) ;;
        *) last_ok_epoch="$ts" ;;
      esac
    fi
  fi
  if [ -z "$last_ok_epoch" ] && command -v systemctl >/dev/null 2>&1; then
    raw="$(systemctl show -p InactiveExitTimestamp --value "$UNIT" 2>/dev/null || true)"
    if [ -n "${raw:-}" ] && [ "$raw" != "n/a" ] && [ "$raw" != "0" ]; then
      parsed="$(_epoch_from_date "$raw")"
      if [ -n "${parsed:-}" ]; then
        last_ok_epoch="$parsed"
      fi
    fi
  fi
fi

case "$last_ok_epoch" in
  ''|*[!0-9]*) last_ok_epoch="" ;;
esac

# 3) Sin timestamp conocido → no hay evidencia; no fabricar un ERROR.
if [ -z "$last_ok_epoch" ]; then
  echo "INFO: no known success timestamp; skip (no false positive)"
  exit 0
fi

# 4) Gracia post-finde / open de sesión: el silencio overnight no cuenta.
session_open="$(_epoch_from_date "${today_utc} 13:00:00")"
if [ -z "$session_open" ]; then
  echo "INFO: could not compute session_open; skip (no false positive)"
  exit 0
fi

if [ "$last_ok_epoch" -lt "$session_open" ]; then
  age=$((now_epoch - session_open))
  age_basis="session_open"
else
  age=$((now_epoch - last_ok_epoch))
  age_basis="last_ok"
fi
if [ "$age" -lt 0 ]; then
  age=0
fi

# 5) Silencio intra-sesión por encima del umbral → Telegram ERROR, exit 1.
if [ "$age" -gt "$THRESHOLD_SEC" ]; then
  msg="ERROR [paper][vps] ${UNIT} silent ${age}s (threshold ${THRESHOLD_SEC}s, basis=${age_basis})"
  echo "$msg"
  if [ "${WATCHDOG_DRY_RUN:-}" != "1" ]; then
    if [ -f "$PAPER_ENV" ]; then
      set -a
      # shellcheck disable=SC1091
      source "$PAPER_ENV"
      set +a
    fi
    bash "$NOTIFY" "$msg" || echo "WARN: telegram notify failed (type=$(basename "$NOTIFY"))"
  fi
  exit 1
fi

# 6) OK silencioso: journal del timer, no Telegram.
echo "INFO: ok age=${age}s basis=${age_basis} threshold=${THRESHOLD_SEC}s"
exit 0
