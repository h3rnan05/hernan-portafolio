"""Servicio standalone: chat de Telegram con el wizards bot.

Deliberadamente separado del backend grande (backend/app) porque ese backend
exige una base de datos Postgres real para arrancar (Settings de FastAPI con
DATABASE_URL/FRED_API_KEY obligatorios) y este chat no necesita nada de eso:
solo habla con Telegram, Claude y la API de contenidos de GitHub.

Flujo: el usuario manda una noticia/idea por Telegram → este webhook →
Claude la evalúa con el marco de Market Wizards (idea_evaluator.py) →
responde el veredicto al chat → si es INVERTIR, encola la idea en
wizards_inbox.json en main (vía API de GitHub) para que el bot de Actions
la ejecute en su siguiente corrida, sujeta a sus límites de riesgo en código.

DESPLIEGUE (Render, servicio nuevo — sin base de datos):
  1. Render → New → Web Service → conecta el repo hernan-portafolio.
  2. Root Directory: telegram_bot
  3. Runtime: Python 3 | Build Command: pip install -r requirements.txt
     Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
  4. Plan: Free.
  5. Env vars (Settings → Environment):
       ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
       TELEGRAM_WEBHOOK_SECRET, GITHUB_BOT_TOKEN
     (GITHUB_REPO es opcional; default "h3rnan05/hernan-portafolio")
  6. Tras el deploy, registra el webhook:
       curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<ESTE-SERVICIO>.onrender.com/telegram/webhook&secret_token=<TU_SECRETO>"

Seguridad: se valida el header X-Telegram-Bot-Api-Secret-Token contra
TELEGRAM_WEBHOOK_SECRET y el chat_id contra TELEGRAM_CHAT_ID. Cualquier otro
chat recibe un rechazo y ninguna acción.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, Request

from idea_evaluator import evaluar_idea
from options_command import generar_options
from report_command import generar_lista_completa, generar_reporte

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telegram_bot")

app = FastAPI(title="Wizards Bot — Telegram Chat (standalone)")

GITHUB_REPO = os.getenv("GITHUB_REPO", "h3rnan05/hernan-portafolio")
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
    "también viene del libro.\n\n"
    "/report TICKER -- memo de investigación de una empresa (técnico, "
    "fundamentales, consenso de analistas, noticias relevantes). Ej.: "
    "/report AAPL\n\n"
    "/options TICKER -- ranking cuantitativo de estrategias de opciones "
    "(Greeks, probabilidad, valor esperado, liquidez -- 100% "
    "determinístico, el LLM solo lo explica). Top 4 por defecto; agrega "
    "--full para ver todas con el detalle completo. Ej.: /options AAPL, "
    "/options AAPL --full\n\n"
    "/list -- la shortlist completa de hoy (el mensaje diario solo muestra "
    "el Top 10)."
)


def _env(nombre: str) -> str:
    return os.getenv(nombre, "").strip()


async def _telegram_send(chat_id: str, texto: str) -> None:
    token = _env("TELEGRAM_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=15) as http:
        await http.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto[:4000]},
        )


async def _telegram_send_largo(chat_id: str, texto: str) -> None:
    """Como _telegram_send, pero en trozos -- un reporte completo puede
    superar el límite de 4096 caracteres de un solo mensaje de Telegram."""
    for i in range(0, len(texto), 3900):
        await _telegram_send(chat_id, texto[i:i + 3900])


async def _github_get(http: httpx.AsyncClient, path: str) -> tuple[dict | None, str | None]:
    """Contenido JSON + sha de un archivo del repo (None si no existe)."""
    r = await http.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}",
        headers={"Authorization": f"Bearer {_env('GITHUB_BOT_TOKEN')}"},
    )
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    body = r.json()
    contenido = json.loads(base64.b64decode(body["content"]).decode())
    return contenido, body["sha"]


async def _encolar_idea(idea: dict) -> None:
    """Añade la idea a wizards_inbox.json en main (el bot de Actions la lee)."""
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
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{INBOX_PATH}",
            headers={"Authorization": f"Bearer {_env('GITHUB_BOT_TOKEN')}"},
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
        log.warning("no pude leer el estado del portafolio: %s", e)
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
                f"por aquí con el resultado)."
            )
        await _telegram_send(chat_id, respuesta)
    except Exception as e:
        log.exception("procesamiento falló")
        try:
            await _telegram_send(
                chat_id, f"⚠️ Algo falló procesando tu idea: {type(e).__name__}. "
                         f"Inténtalo de nuevo en un momento.")
        except Exception:
            pass


async def _procesar_lista(chat_id: str) -> None:
    """Genera la shortlist completa de hoy (/list) y la manda. Corre como
    background task por consistencia con los demás handlers, aunque solo
    lee un archivo local (no hace llamadas de red)."""
    try:
        texto = await asyncio.to_thread(generar_lista_completa)
        await _telegram_send_largo(chat_id, texto)
    except Exception as e:
        log.exception("lista completa falló")
        try:
            await _telegram_send(
                chat_id, f"⚠️ Algo falló generando la lista: {type(e).__name__}. "
                         f"Inténtalo de nuevo en un momento.")
        except Exception:
            pass


async def _procesar_reporte(ticker: str, chat_id: str) -> None:
    """Genera el memo de /report TICKER y lo manda. Corre como background
    task; generar_reporte hace llamadas de red síncronas (Yahoo/yfinance/
    Google News/Anthropic), así que se ejecuta en un hilo aparte para no
    bloquear el event loop del webhook."""
    try:
        texto = await asyncio.to_thread(generar_reporte, ticker)
        await _telegram_send_largo(chat_id, texto)
    except Exception as e:
        log.exception("reporte de %s falló", ticker)
        try:
            await _telegram_send(
                chat_id, f"⚠️ Algo falló generando el reporte de {ticker}: "
                         f"{type(e).__name__}. Inténtalo de nuevo en un momento.")
        except Exception:
            pass


async def _procesar_options(ticker: str, modo: str, chat_id: str) -> None:
    """Genera el memo de /options TICKER y lo manda. Corre como background
    task; generar_options hace llamadas de red síncronas (Yahoo/yfinance/
    Anthropic), así que se ejecuta en un hilo aparte para no bloquear el
    event loop del webhook."""
    try:
        texto = await asyncio.to_thread(generar_options, ticker, modo)
        await _telegram_send_largo(chat_id, texto)
    except Exception as e:
        log.exception("options de %s falló", ticker)
        try:
            await _telegram_send(
                chat_id, f"⚠️ Algo falló generando las opciones de {ticker}: "
                         f"{type(e).__name__}. Inténtalo de nuevo en un momento.")
        except Exception:
            pass


@app.get("/health")
@app.get("/telegram/health")
async def health() -> dict:
    return {
        "enabled": bool(
            _env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID")
            and _env("TELEGRAM_WEBHOOK_SECRET") and _env("GITHUB_BOT_TOKEN")
            and _env("ANTHROPIC_API_KEY")
        )
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    secret = _env("TELEGRAM_WEBHOOK_SECRET")
    if not secret or x_telegram_bot_api_secret_token != secret:
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

    if chat_id != _env("TELEGRAM_CHAT_ID"):
        background.add_task(
            _telegram_send, chat_id,
            "Este bot es privado. No estás en su lista de chats autorizados.")
        return {"ok": True}

    if texto.startswith(("/start", "/help", "/ayuda")):
        background.add_task(_telegram_send, chat_id, AYUDA)
        return {"ok": True}

    if texto.startswith("/list"):
        background.add_task(_procesar_lista, chat_id)
        return {"ok": True}

    if texto.startswith("/report"):
        partes = texto.split()
        if len(partes) < 2:
            background.add_task(
                _telegram_send, chat_id, "Uso: /report TICKER (ej. /report AAPL)")
            return {"ok": True}
        background.add_task(_procesar_reporte, partes[1].upper(), chat_id)
        return {"ok": True}

    if texto.startswith("/options"):
        partes = texto.split()
        if len(partes) < 2:
            background.add_task(
                _telegram_send, chat_id,
                "Uso: /options TICKER (ej. /options AAPL, o /options AAPL --full)")
            return {"ok": True}
        modo = "full" if "--full" in partes[2:] else "simple"
        background.add_task(_procesar_options, partes[1].upper(), modo, chat_id)
        return {"ok": True}

    background.add_task(_procesar, texto, chat_id)
    return {"ok": True}
