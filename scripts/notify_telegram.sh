#!/usr/bin/env bash
# Ops helper: send one Telegram message. Credentials from env only.
set -u
msg="${*:-}"
if [ -z "$msg" ]; then
  echo "TELEGRAM_NOTIFY FAIL: empty message"
  exit 1
fi
token="${MOMENTUM_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
chat="${MOMENTUM_TELEGRAM_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
if [ -z "$token" ] || [ -z "$chat" ]; then
  echo "TELEGRAM_NOTIFY FAIL: missing token or chat id"
  exit 1
fi
resp="$(curl -sS -X POST "https://api.telegram.org/bot${token}/sendMessage" \
  --data-urlencode "chat_id=${chat}" \
  --data-urlencode "text=${msg}" 2>/dev/null || true)"
mid="$(printf "%s" "$resp" | python3 -c "import sys,json; 
try:
 j=json.load(sys.stdin); print(j.get(\"result\",{}).get(\"message_id\",\"\"))
except Exception:
 print(\"\")" 2>/dev/null || true)"
ok="$(printf "%s" "$resp" | python3 -c "import sys,json; 
try:
 j=json.load(sys.stdin); print(\"1\" if j.get(\"ok\") else \"0\")
except Exception:
 print(\"0\")" 2>/dev/null || true)"
if [ "$ok" = "1" ]; then
  echo "TELEGRAM_NOTIFY OK message_id=${mid}"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) message_id=${mid} msg=${msg}" >> /tmp/watchdog_tg_ids.log
  exit 0
fi
echo "TELEGRAM_NOTIFY FAIL"
exit 1
