#!/usr/bin/env bash
# Backup diario del overlay VPS (condición Claude #1) + events.jsonl del
# panel (#136).
# Timer: momentum-watchlist-state-backup.timer @ 02:15 America/Monterrey
#        (o infra/cron/momentum-watchlist-state-backup, mismo horario).
# El wrapper del rechequeo también lo llama antes de mutar el state.
# Dest: /var/backups/momentum/watchlist_vps_state-YYYY-MM-DD.json
#       /var/backups/momentum/events-YYYY-MM-DD.jsonl
# Retención: 14 días. PAPER ONLY. No arranca watchlist/watchdog timers.
set -u
export TZ="${TZ:-America/Monterrey}"
STATE="${MOMENTUM_WATCHLIST_STATE:-/var/lib/momentum/watchlist_vps_state.json}"
EVENTS="${MOMENTUM_EVENTS_LOG:-${DASH_EVENTOS:-/var/lib/momentum/events.jsonl}}"
PREFERRED="${MOMENTUM_WATCHLIST_STATE_BACKUP_DIR:-/var/backups/momentum}"
FALLBACK="/var/lib/momentum/backups"
STAMP=$(date +%F)

_purgar() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  find "$dir" -name 'watchlist_vps_state-*.json' -mtime +14 -delete 2>/dev/null || true
  find "$dir" -name 'events-*.jsonl' -mtime +14 -delete 2>/dev/null || true
}

if [ ! -f "$STATE" ] && [ ! -f "$EVENTS" ]; then
  echo "INFO: no VPS watchlist state at $STATE nor events at $EVENTS -- nothing to backup"
  _purgar "$PREFERRED"
  exit 0
fi

dest_dir="$PREFERRED"
if ! mkdir -p "$dest_dir" 2>/dev/null || [ ! -w "$dest_dir" ]; then
  dest_dir="$FALLBACK"
  if ! mkdir -p "$dest_dir" 2>/dev/null || [ ! -w "$dest_dir" ]; then
    echo "WARN: cannot write backup dir ($PREFERRED or $FALLBACK)"
    exit 1
  fi
  echo "WARN: $PREFERRED not writable; backing up to $dest_dir"
fi

rc=0
_copiar() {
  local src="$1" dest="$2"
  if [ ! -f "$src" ]; then
    echo "INFO: no $src -- skip"
    return 0
  fi
  if cp -a "$src" "$dest"; then
    echo "INFO: backed up $src -> $dest"
  else
    echo "WARN: backup failed $src -> $dest"
    rc=1
  fi
}

_copiar "$STATE" "$dest_dir/watchlist_vps_state-${STAMP}.json"
_copiar "$EVENTS" "$dest_dir/events-${STAMP}.jsonl"
_purgar "$dest_dir"
exit "$rc"
