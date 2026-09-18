#!/usr/bin/env bash
# Backup diario del overlay VPS (condición Claude #1).
# Cron: 15 2 * * *  TZ=America/Monterrey
# Dest: /var/backups/momentum/watchlist_vps_state-YYYY-MM-DD.json
# Retención: 14 días. PAPER ONLY. No arranca el timer de rechequeo.
set -u
export TZ="${TZ:-America/Monterrey}"
STATE="${MOMENTUM_WATCHLIST_STATE:-/var/lib/momentum/watchlist_vps_state.json}"
PREFERRED="${MOMENTUM_WATCHLIST_STATE_BACKUP_DIR:-/var/backups/momentum}"
FALLBACK="/var/lib/momentum/backups"
STAMP=$(date +%F)

_purgar() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  find "$dir" -name 'watchlist_vps_state-*.json' -mtime +14 -delete 2>/dev/null || true
}

if [ ! -f "$STATE" ]; then
  echo "INFO: no VPS watchlist state at $STATE -- nothing to backup"
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

DEST="$dest_dir/watchlist_vps_state-${STAMP}.json"
cp -a "$STATE" "$DEST"
_purgar "$dest_dir"
echo "INFO: backed up $STATE -> $DEST"
exit 0
