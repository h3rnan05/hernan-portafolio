"""Webhook de Telegram — chat directo con el wizards bot.

Flujo: el usuario manda una noticia/idea por Telegram → Claude la evalúa con
el marco de Market Wizards (app/ai/idea_evaluator.py) → responde el veredicto
al chat → si es INVERTIR, encola la idea en wizards_inbox.json del repo (vía
GitHub API) para que el bot de Actions la ejecute en su siguiente corrida,
sujeta a sus límites de riesgo en código.

Configuración (una sola vez):
  1. Crear el bot con @BotFather → TELEGRAM_BOT_TOKEN.
  2. Obtener tu chat id (mándale un mensaje al bot y mira
     https://api.telegram.org/bot<TOKEN>/getUpdates) → TELEGRAM_CHAT_ID.
  3. Inventar un secreto → TELEGRAM_WEBHOOK_SECRET.
  4. Registrar el webhook:
     curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<TU-BACKEND>.onrender.com/telegram/webhook&secret_token=<SECRETO>"
  5. PAT fine-grained (contents:write en el repo) → GITHUB_BOT_TOKEN.

Seguridad: se valida el header X-Telegram-Bot-Api-Secret-Token contra
TELEGRAM_WEBHOOK_SECRET y el chat_id contra TELEGRAM_CHAT_ID. Cualquier otro
chat recibe un rechazo y ninguna acción.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, Request

from app.ai.idea_evaluator import evaluar_idea
from app.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])

INBOX_PATH = "wizards_inbox.json"
STATE_PATH = "wizards_state.json"
_updates_vistos: set[int] = set()  # dedupe: Telegram reintenta si tardamos

AYUDA = (
    "🧙 Soy el filtro de ideas de tu wizards bot.\n\n"
    "Mándame una noticia o una idea de inversión y te digo si merece una "
    "posición, con el criterio de Los magos del mercado. Si el veredicto es "
    "INVERTIR, la encolo y el bot la ejecuta en su siguiente corrida "
    "(máx. 30 min en horario de mercado) — siempre con sus límites de "
    "riesgo: 1.5% por trade, stop 2×ATR, y nunca más de 4 posiciones.\n\n"
    "Las salidas las maneja el bot solo (trailing stop). Yo no acepto "
    "órdenes de venta: el sistema no se sobreescribe por impulso — eso "
    "también viene del libro."
)


async def _telegram_send(chat_id: str, texto: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as http:
        await http.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": texto[:4000]},
        )


async def _github_get(http: httpx.AsyncClient, path: str) -> tuple[dict | None, str | None]:
    """Contenido JSON + sha de un archivo del repo (None si no existe)."""
    settings = get_settings()
    r = await http.get(
        f"https://api.github.com/repos/{settings.github_repo}/contents/{path}",
        headers={"Authorization": f"Bearer {settings.github_bot_token}"},
    )
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    body = r.json()
    contenido = json.loads(base64.b64decode(body["content"]).decode())
    return contenido, body["sha"]


async def _encolar_idea(idea: dict) -> None:
    """Añade la idea a wizards_inbox.json en main (el bot de Actions la lee)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as http:
        inbox, sha = await _github_get(http, INBOX_PATH)
        inbox = inbox or {"pendientes": []}
        inbox["pendientes"].append(idea)
        payload: dict = {
            "message": f"idea: encola {idea['ticker']} desde Telegram [skip ci]",
            "content": base64.b64encode(
                json.dumps(inbox, indent=2, ensure_ascii=False).encode()
            ).decode(),
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha
        r = await http.put(
            f"https://api.github.com/repos/{settings.github_repo}/contents/{INBOX_PATH}",
            headers={"Authorization": f"Bearer {settings.github_bot_token}"},
            json=payload,
        )
        r.raise_for_status()


async def _contexto_portafolio() -> str:
    """Estado actual del portafolio (para que el evaluador lo considere)."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            estado, _ = await _github_get(http, STATE_PATH)
        if not estado:
            return "(sin estado aún)"
        pos = ", ".join(
            f"{t} x{p['qty']} (entrada ${p['entrada']})"
            for t, p in estado.get("posiciones", {}).items()
        ) or "ninguna"
        return (
            f"cash ${estado.get('cash', 0):,.2f} | posiciones: {pos} | "
            f"P&L realizado ${estado.get('pnl_realizado', 0):+,.2f}"
        )
    except Exception as e:
        log.warning("[telegram] no pude leer el estado del portafolio: %s", e)
        return "(estado no disponible)"


async def _procesar(texto: str, chat_id: str) -> None:
    """Evalúa la idea y responde. Corre como background task."""
    try:
        veredicto = await evaluar_idea(texto, await _contexto_portafolio())
        respuesta = veredicto.get("respuesta_usuario") or "(sin respuesta)"
        if veredicto.get("veredicto") == "INVERTIR":
            idea = {
                "id": uuid.uuid4().hex[:12],
                "ticker": str(veredicto["ticker"]).upper(),
                "tesis": veredicto.get("tesis") or texto[:200],
                "confianza": veredicto.get("confianza"),
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "origen": "telegram",
            }
            await _encolar_idea(idea)
            respuesta += (
                f"\n\n✅ Encolada: {idea['ticker']}. El bot la ejecutará en su "
                f"siguiente corrida si pasa los filtros de riesgo (te avisa "
                f"por aquí y por Discord con el resultado)."
            )
        await _telegram_send(chat_id, respuesta)
    except Exception as e:
        log.exception("[telegram] procesamiento falló")
        try:
            await _telegram_send(
                chat_id, f"⚠️ Algo falló procesando tu idea: {type(e).__name__}. "
                         f"Inténtalo de nuevo en un momento.")
        except Exception:
            pass


@router.get("/health")
async def telegram_health() -> dict:
    s = get_settings()
    return {
        "enabled": bool(
            s.telegram_bot_token and s.telegram_chat_id
            and s.telegram_webhook_secret and s.github_bot_token
            and s.anthropic_api_key
        )
    }


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    if not settings.telegram_webhook_secret or (
        x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        # 200 vacío a impostores: no darles señal de qué falló.
        return {"ok": True}

    update = await request.json()
    update_id = update.get("update_id")
    if update_id in _updates_vistos:
        return {"ok": True}  # reintento de Telegram por timeout previo
    _updates_vistos.add(update_id)
    if len(_updates_vistos) > 500:
        _updates_vistos.clear()

    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    texto = (msg.get("text") or "").strip()
    if not chat_id or not texto:
        return {"ok": True}

    if chat_id != settings.telegram_chat_id:
        background.add_task(
            _telegram_send, chat_id,
            "Este bot es privado. No estás en su lista de chats autorizados.")
        return {"ok": True}

    if texto.startswith(("/start", "/help", "/ayuda")):
        background.add_task(_telegram_send, chat_id, AYUDA)
        return {"ok": True}

    background.add_task(_procesar, texto, chat_id)
    return {"ok": True}
