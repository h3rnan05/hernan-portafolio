#!/usr/bin/env bash
# Copia diaria del overlay VPS. /var/lib/momentum no está en git: sin
# esto un rm, un disco lleno o un rollback mal hecho pierde las
# transiciones locales (VRA watching→expired y las que vengan).
# PAPER ONLY. No toca watchlist.json ni arranca el timer de rechequeo.
set -u
STATE="${MOMENTUM_WATCHLIST_STATE:-/var/lib/momentum/watchlist_vps_state.json}"
STAMP=$(date -u +%F)
PREFERRED="${MOMENTUM_WATCHLIST_STATE_BACKUP_DIR:-/var/backups}"
FALLBACK="/var/lib/momentum/backups"

if [ ! -f "$STATE" ]; then
  echo "INFO: no VPS watchlist state at $STATE -- nothing to backup"
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

DEST="$dest_dir/watchlist-vps-state-${STAMP}.json"
cp -a "$STATE" "$DEST"
echo "INFO: backed up $STATE -> $DEST"
exit 0
