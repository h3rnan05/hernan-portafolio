#!/usr/bin/env bash
# Ops helper: send one Telegram message. Credentials come from the environment
# (MOMENTUM_TELEGRAM_* or TELEGRAM_*), never from this file.
# Do not call from run_watchlist_paper.sh on success or git-push WARN.
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

# Hide curl diagnostics: they can echo the request URL, which includes the token.
if curl -sS -o /dev/null --fail \
    -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=${msg}" \
    >/dev/null 2>&1; then
  echo "TELEGRAM_NOTIFY OK"
  exit 0
fi

echo "TELEGRAM_NOTIFY FAIL"
exit 1
