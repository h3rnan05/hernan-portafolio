#!/usr/bin/env bash
# Ops helper: send one Telegram message. Credentials from env only.
#
# Filtro TELEGRAM_SOLO_ENTRADAS (2026-10-06, mismo criterio que
# momentum_hunter/telegram_filtro.py): activo por default, solo salen
# TELEGRAM_CATEGORIA=entrada|salida|critico. Todo lo de este script hoy
# (watchdog, persist fallido, fallo/saldo de la IA) es informativo:
# queda en el log (stdout -> journal) y sale con 0 como si se hubiera
# mandado, para que el dedupe de cada caller no reintente en bucle.
# Interruptor sin deploy: /etc/momentum/telegram_solo_entradas (0/1,
# ruta en MOMENTUM_TELEGRAM_FLAG_FILE) manda sobre la variable.
set -u
msg="${*:-}"
if [ -z "$msg" ]; then
  echo "TELEGRAM_NOTIFY FAIL: empty message"
  exit 1
fi
_tg_flag_file="${MOMENTUM_TELEGRAM_FLAG_FILE:-/etc/momentum/telegram_solo_entradas}"
_tg_solo="${TELEGRAM_SOLO_ENTRADAS:-1}"
if [ -r "$_tg_flag_file" ]; then
  _tg_arch="$(head -n 1 "$_tg_flag_file" 2>/dev/null | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  case "$_tg_arch" in
    0|false|no|off|1|true|si|yes|on) _tg_solo="$_tg_arch" ;;
  esac
fi
case "$(printf '%s' "$_tg_solo" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')" in
  0|false|no|off) _tg_solo=0 ;;
  *) _tg_solo=1 ;;
esac
_tg_cat="${TELEGRAM_CATEGORIA:-info}"
if [ "$_tg_solo" = "1" ] && [ "$_tg_cat" != "entrada" ] && [ "$_tg_cat" != "salida" ] && [ "$_tg_cat" != "critico" ]; then
  echo "TELEGRAM_NOTIFY SILENCIADO (TELEGRAM_SOLO_ENTRADAS=1, categoria=${_tg_cat}): ${msg}"
  exit 0
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
