#!/bin/bash
# Daily morning trading script — runs predictions + rebalances all bots.
# Scheduled for 9:25 AM ET (14:25 UTC) on trading days via cron or Render.
#
# Cron entry (local machine, adjust path):
#   25 14 * * 1-5 /bin/bash /path/to/backend/scripts/morning_run.sh >> /tmp/trading.log 2>&1
#
# Render cron: add as a Cron Job service pointing to this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo "========================================"
echo "  HERNAN TERMINAL — TRADING BOT"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "========================================"

cd "$BACKEND_DIR"

# Load .env if it exists
if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

# Step 1: Run daily predictions + model refit check
echo ""
echo "[1/2] Running daily predictions..."
uv run python scripts/run_predictions.py

# Step 2: Execute all trading bots
echo ""
echo "[2/2] Executing trading bots..."
uv run python scripts/run_trading.py

echo ""
echo "Done. $(date '+%H:%M:%S')"
