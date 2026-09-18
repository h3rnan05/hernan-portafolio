#!/usr/bin/env bash
# PAPER ONLY. systemd watchdog for momentum-watchlist.service
# (units momentum-watchlist-watchdog.service / .timer on paper VPS).
#
# WHY. The oneshot runs ~every 5m Mon–Fri 13–20 UTC. If it stops finishing
# OK, we must know. Threshold 1200s *inside the session*.
# Without open grace, Monday 13:00 measured ~64h from Friday last_ok and
# fired a false Telegram ERROR (2026-09-14, owner OK).
#
# EXTENDED 2026-09-18 (owner GO B): also FIRE on
#   (1) persist-fail streak N=3 in ~15m window
#   (2) zero remote pushes in whole US session (after 1h into session)
#   (3) local ahead of origin/main >= AHEAD_THRESHOLD (default 50)
# Does NOT alert on empty watchlist (legitimate noise).
# No START/OK spam: Telegram only on ERROR. Does not touch Alpaca.
#
# 2026-09-18 FP: watchdog.timer kept firing "silent >1200s" every ~10m
# while momentum-watchlist.timer was intentionally OFF (post-#132).
# Silence check skips when that timer is not active (no Telegram).
# persist/ahead/zero_push are NOT skipped — they keep their daily dedupe.
set -u

UNIT="${WATCHDOG_UNIT:-momentum-watchlist.service}"
TIMER_UNIT="${WATCHDOG_TIMER_UNIT:-momentum-watchlist.timer}"
THRESHOLD_SEC="${WATCHDOG_THRESHOLD_SEC:-1200}"
PERSIST_FAIL_N="${WATCHDOG_PERSIST_FAIL_N:-3}"
PERSIST_WINDOW_SEC="${WATCHDOG_PERSIST_WINDOW_SEC:-900}"
AHEAD_THRESHOLD="${WATCHDOG_AHEAD_THRESHOLD:-50}"
REPO="${WATCHDOG_REPO:-/opt/hernan-portafolio}"
STATE_DIR="${WATCHDOG_STATE_DIR:-/var/lib/momentum}"
case "$THRESHOLD_SEC" in ''|*[!0-9]*) THRESHOLD_SEC=1200 ;; esac
case "$PERSIST_FAIL_N" in ''|*[!0-9]*) PERSIST_FAIL_N=3 ;; esac
case "$PERSIST_WINDOW_SEC" in ''|*[!0-9]*) PERSIST_WINDOW_SEC=900 ;; esac
case "$AHEAD_THRESHOLD" in ''|*[!0-9]*) AHEAD_THRESHOLD=50 ;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NOTIFY="${ROOT}/scripts/notify_telegram.sh"
PAPER_ENV="${MOMENTUM_PAPER_ENV:-/etc/momentum/paper.env}"

_epoch_from_date() {
  date -u -d "$1" +%s 2>/dev/null || true
}

_notify() {
  local msg="$1"
  echo "$msg"
  if [ "${WATCHDOG_DRY_RUN:-}" = "1" ]; then
    echo "INFO: dry-run skip notify"
    return 0
  fi
  if [ -f "$PAPER_ENV" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$PAPER_ENV"
    set +a
  fi
  bash "$NOTIFY" "$msg" || echo "WARN: telegram notify failed (type=$(basename "$NOTIFY"))"
}

_already_fired_today() {
  local key="$1"
  local f="${STATE_DIR}/watchdog_fired_${today_utc}"
  [ -f "$f" ] && grep -qxF "$key" "$f" 2>/dev/null
}

_mark_fired_today() {
  local key="$1"
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  local f="${STATE_DIR}/watchdog_fired_${today_utc}"
  touch "$f" 2>/dev/null || true
  if [ -w "$f" ] && ! grep -qxF "$key" "$f" 2>/dev/null; then
    echo "$key" >> "$f"
  fi
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

# 1) Weekend and outside US window (UTC hour 13–20 inclusive).
if [ "$dow" -gt 5 ]; then
  echo "INFO: weekend (dow=${dow} UTC); skip"
  exit 0
fi
if [ "$hour" -lt 13 ] || [ "$hour" -gt 20 ]; then
  echo "INFO: outside US window (hour=${hour} UTC); skip"
  exit 0
fi

session_open="$(_epoch_from_date "${today_utc} 13:00:00")"
if [ -z "$session_open" ]; then
  echo "INFO: could not compute session_open; skip (no false positive)"
  exit 0
fi

fired=0

# --- A) silence (grace #119 intact) ---
# If the oneshot timer is not active, silence is expected. Skip this
# check only — do not exit, so persist/ahead/zero_push still run.
timer_state=""
if [ "${WATCHDOG_TIMER_ACTIVE+set}" = "set" ]; then
  # Test/ops override (CI has no systemd unit; VPS verify can force active).
  timer_state="$WATCHDOG_TIMER_ACTIVE"
elif command -v systemctl >/dev/null 2>&1; then
  timer_state="$(systemctl is-active "$TIMER_UNIT" 2>/dev/null || true)"
fi
if [ "$timer_state" != "active" ]; then
  echo "INFO: timer inactive; skip"
else
  last_ok_epoch=""
  if [ "${WATCHDOG_LAST_OK_EPOCH+set}" = "set" ]; then
    last_ok_epoch="${WATCHDOG_LAST_OK_EPOCH}"
  else
    if command -v journalctl >/dev/null 2>&1; then
      line="$(journalctl -u "$UNIT" --since "8 days ago" --no-pager -o short-unix 2>/dev/null \
        | grep -E "Finished |Deactivated successfully" \
        | tail -n 1 || true)"
      if [ -n "${line:-}" ]; then
        ts="${line%% *}"
        ts="${ts%%.*}"
        case "$ts" in
          ""|*[!0-9]*) ;;
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
  case "$last_ok_epoch" in ""|*[!0-9]*) last_ok_epoch="" ;; esac

  if [ -z "$last_ok_epoch" ]; then
    echo "INFO: no known success timestamp; skip silence check"
  else
    if [ "$last_ok_epoch" -lt "$session_open" ]; then
      age=$((now_epoch - session_open))
      age_basis="session_open"
    else
      age=$((now_epoch - last_ok_epoch))
      age_basis="last_ok"
    fi
    if [ "$age" -lt 0 ]; then age=0; fi
    if [ "$age" -gt "$THRESHOLD_SEC" ]; then
      if _already_fired_today "silence"; then
        echo "INFO: silence already fired today; skip duplicate Telegram"
      else
        _notify "ERROR [paper][vps] ${UNIT} silent ${age}s (threshold ${THRESHOLD_SEC}s, basis=${age_basis})"
        _mark_fired_today "silence"
        fired=1
      fi
    else
      echo "INFO: ok age=${age}s basis=${age_basis} threshold=${THRESHOLD_SEC}s"
    fi
  fi
fi

# --- B1) persist-fail streak N in window ---
persist_n=0
if command -v journalctl >/dev/null 2>&1; then
  since_persist=$((now_epoch - PERSIST_WINDOW_SEC))
  since_iso="$(date -u -d "@${since_persist}" +%Y-%m-%dT%H:%M:%SZ)"
  persist_n="$(journalctl -u "$UNIT" --since "$since_iso" --no-pager 2>/dev/null \
    | grep -c "ERROR: persist failed after" || true)"
fi
case "$persist_n" in ""|*[!0-9]*) persist_n=0 ;; esac
echo "INFO: persist_fail_count=${persist_n} window=${PERSIST_WINDOW_SEC}s need=${PERSIST_FAIL_N}"
if [ "$persist_n" -ge "$PERSIST_FAIL_N" ]; then
  if _already_fired_today "persist_fail"; then
    echo "INFO: persist_fail already fired today; skip duplicate Telegram"
  else
    _notify "ERROR [paper][vps] git persist failed ${persist_n}x in ${PERSIST_WINDOW_SEC}s (threshold ${PERSIST_FAIL_N})"
    _mark_fired_today "persist_fail"
    fired=1
  fi
fi

# --- B2) zero pushes in US session (after 1h into session) ---
session_age=$((now_epoch - session_open))
if [ "$session_age" -ge 3600 ]; then
  session_since="${today_utc} 13:00:00 UTC"
  push_n="$(journalctl -u "$UNIT" --since "$session_since" --no-pager 2>/dev/null \
    | grep -c "INFO: persist ok" || true)"
  case "$push_n" in ""|*[!0-9]*) push_n=0 ;; esac
  echo "INFO: session_persist_ok_count=${push_n} session_age=${session_age}s"
  if [ "$push_n" -eq 0 ]; then
    if _already_fired_today "zero_push_session"; then
      echo "INFO: zero_push_session already fired today; skip duplicate Telegram"
    else
      _notify "ERROR [paper][vps] zero remote pushes this US session (no persist ok since ${today_utc} 13:00 UTC; session_age=${session_age}s)"
      _mark_fired_today "zero_push_session"
      fired=1
    fi
  fi
else
  echo "INFO: session_age=${session_age}s <3600; skip zero-push check"
fi

# --- B3) ahead divergence ---
ahead=0
if [ -d "$REPO/.git" ]; then
  ahead="$(sudo -u momentum git -C "$REPO" rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"
fi
case "$ahead" in ""|*[!0-9]*) ahead=0 ;; esac
echo "INFO: git_ahead=${ahead} threshold=${AHEAD_THRESHOLD}"
if [ "$ahead" -ge "$AHEAD_THRESHOLD" ]; then
  if _already_fired_today "ahead"; then
    echo "INFO: ahead already fired today; skip duplicate Telegram"
  else
    _notify "ERROR [paper][vps] git ahead origin/main by ${ahead} commits (threshold ${AHEAD_THRESHOLD})"
    _mark_fired_today "ahead"
    fired=1
  fi
fi

if [ "$fired" -eq 1 ]; then
  exit 1
fi
exit 0
