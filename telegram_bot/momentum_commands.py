"""Comandos de Telegram para el Momentum Opportunity Hunter -- namespace
separado del resto de este servicio: el wizards bot YA tiene su propio
`/trade` (plan de opciones del screener, ver `trade_command.py`), así que
estos comandos viven en una ruta de webhook aparte (`/momentum/webhook`
en `app.py`), pensada para un bot de Telegram DISTINTO
(`MOMENTUM_TELEGRAM_BOT_TOKEN`/`_CHAT_ID`, ver `momentum_hunter/run.py`).

READ-ONLY estricto y determinista -- pedido explícito: "Telegram
solamente debe representar lo que decidió el State Engine... Telegram NO
decide. Telegram NO evalúa. Telegram NO cambia scoring. Telegram NO
modifica oportunidades. Solo comunica." Estas funciones nunca llaman a
un proveedor de datos, nunca escriben `watchlist.json`, nunca calculan
una oportunidad nueva -- solo leen las `EntradaWatchlist` YA resueltas
por `momentum_hunter/run.py` (el caller en `app.py` las descarga vía la
API de contenidos de GitHub, sin filesystem local) y las traducen a
texto corto. Puras y testeables sin red, mismo patrón que
`trade_command.py` de este mismo servicio.

Honestidad explícita sobre "Escaneadas" del `/status` pedido: el tamaño
del universo que escaneó la etapa 1 NO se persiste en ningún archivo
accesible desde acá (solo vive en el log de la corrida) -- inventar ese
número violaría "no inventes datos", así que se omite; en su lugar se
muestra "Candidatos evaluados hoy" (`audit.py`, sí persistido)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from momentum_hunter import audit  # noqa: E402
from momentum_hunter.watchlist import (  # noqa: E402
    ESTADO_EXPIRED,
    ESTADO_INVALIDATED,
    ESTADO_MISSED,
    ESTADO_TRIGGERED,
    ESTADO_WATCHING,
    EntradaWatchlist,
)

# Horario regular NYSE, aproximado en UTC -- SIN calendario de feriados
# ni medio día (honestidad explícita, mismo principio que el resto del
# pipeline: mejor decir "aproximado" que fingir precisión que no existe).
_HORA_UTC_APERTURA = 13.5   # 9:30 AM ET
_HORA_UTC_CIERRE = 20.0     # 4:00 PM ET


def _mercado_abierto(ahora: datetime) -> bool:
    if ahora.weekday() >= 5:
        return False
    hora = ahora.hour + ahora.minute / 60.0
    return _HORA_UTC_APERTURA <= hora < _HORA_UTC_CIERRE


def _fmt(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "no disponible"


def _rr(entrada: float | None, stop: float | None, objetivo: float | None) -> str | None:
    if entrada is None or stop is None or objetivo is None or entrada <= stop:
        return None
    return f"{(objetivo - entrada) / (entrada - stop):.1f} : 1"


def _mas_reciente(entradas: list[EntradaWatchlist], ticker: str) -> EntradaWatchlist | None:
    candidatas = [e for e in entradas if e.ticker == ticker.upper()]
    if not candidatas:
        return None
    return max(candidatas, key=lambda e: e.actualizado_en)


def generar_trade(ticker: str, entradas: list[EntradaWatchlist]) -> str:
    """`/trade TICKER` -- lee el estado EXISTENTE y muestra lo que ya se
    calculó (ver `watchlist.actualizar_niveles`). Nunca crea una
    oportunidad nueva: si no hay una entrada en la watchlist para este
    ticker, lo dice explícitamente en vez de intentar evaluarlo."""
    ticker = ticker.upper()
    e = _mas_reciente(entradas, ticker)
    if e is None:
        return f"🔎 {ticker}\n\nNo existe actualmente una oportunidad activa para este ticker."

    if e.estado == ESTADO_WATCHING:
        lineas = [f"📊 {ticker} -- WATCHING", "", "Estado:", "👀 Esperando confirmación",
                  "", "Entrada si confirma:", _fmt(e.ultima_zona_entrada_baja)]
        if e.ultimo_stop is not None:
            lineas += ["", "Stop:", _fmt(e.ultimo_stop)]
        if e.ultimo_objetivo is not None:
            lineas += ["", "Objetivo:", _fmt(e.ultimo_objetivo)]
        lineas += ["", "Falta:", "Confirmación de ruptura + volumen.", "", "NO ENTRAR TODAVÍA."]
    elif e.estado == ESTADO_TRIGGERED:
        rr = _rr(e.ultima_entrada, e.ultimo_stop, e.ultimo_objetivo)
        lineas = [f"🚨 {ticker} -- TRIGGERED", "", "Entrada:", _fmt(e.ultima_entrada),
                  "", "Stop:", _fmt(e.ultimo_stop), "", "Objetivo:", _fmt(e.ultimo_objetivo)]
        if rr is not None:
            lineas += ["", "R/R:", rr]
        lineas += ["", "Timing:", "TEMPRANO"]
    elif e.estado == ESTADO_MISSED:
        lineas = [f"⚠️ {ticker} -- MISSED", "", "Entrada original:", _fmt(e.ultima_zona_entrada_baja),
                  "", "Ya llegamos tarde.", "", "NO PERSEGUIR."]
    elif e.estado == ESTADO_INVALIDATED:
        motivo = e.transiciones[-1].motivo if e.transiciones else "No especificado."
        lineas = [f"❌ {ticker} -- INVALIDATED", "", "La idea quedó invalidada.", "", "Motivo:", motivo,
                  "", "NO ENTRAR."]
    else:   # EXPIRED
        lineas = [f"⏰ {ticker} -- EXPIRED", "", "Nunca llegó a confirmarse -- venció el tiempo de vigilancia."]

    return "\n".join(lineas)


def generar_status(
    entradas: list[EntradaWatchlist], auditoria_hoy: dict | None = None,
    ahora: datetime | None = None,
) -> str:
    """`/status` -- resumen corto del estado actual del bot.

    `auditoria_hoy` es el dict YA descargado de `momentum_hunter/
    auditoria/{hoy}.json` (mismo patrón que `entradas`: el caller en
    `app.py` hace la llamada de red vía la API de GitHub, esta función
    solo lee lo que ya le pasaron -- `None` si el archivo no existe
    todavía hoy, ej. antes de la primera corrida). Ver docstring del
    módulo para por qué "Escaneadas" no aparece (dato no persistido en
    ningún archivo accesible desde acá -- no se inventa)."""
    ahora = ahora or datetime.now(UTC)
    hoy = ahora.date().isoformat()

    watching = [e for e in entradas if e.estado == ESTADO_WATCHING]
    triggered_hoy = [e for e in entradas if e.estado == ESTADO_TRIGGERED and e.actualizado_en[:10] == hoy]

    lineas = [
        "📊 BOT STATUS", "",
        f"Mercado: {'🟢 ABIERTO' if _mercado_abierto(ahora) else '🔴 CERRADO'}", "",
        f"En vigilancia: {len(watching)}",
        f"Entradas confirmadas hoy: {len(triggered_hoy)}",
    ]
    if auditoria_hoy is not None:
        evaluados_hoy, descartados_hoy = audit.resumen(auditoria_hoy)
        lineas.append(f"Candidatos evaluados hoy: {evaluados_hoy}")
        lineas.append(f"Descartados hoy: {descartados_hoy}")

    latencias = [
        t.latencia_desde_transicion_ms
        for e in entradas for t in e.transiciones
        if t.timestamp[:10] == hoy and t.latencia_desde_transicion_ms is not None
    ]
    if latencias:
        promedio_s = sum(latencias) / len(latencias) / 1000.0
        lineas += ["", f"Latencia promedio hoy: {promedio_s:.1f} s ({len(latencias)} transición(es))"]

    disparos_hoy = sorted(triggered_hoy, key=lambda e: e.actualizado_en, reverse=True)
    if disparos_hoy:
        ultima = disparos_hoy[0]
        lineas += ["", f"Última señal: {ultima.ticker} -- {ultima.actualizado_en} UTC"]

    return "\n".join(lineas)


def generar_radar(entradas: list[EntradaWatchlist]) -> str:
    """`/radar` -- únicamente lo que sigue activo (TRIGGERED de hoy +
    WATCHING), sin explicaciones largas -- "nada de explicaciones
    gigantes" (pedido explícito)."""
    ahora = datetime.now(UTC)
    hoy = ahora.date().isoformat()
    triggered_hoy = sorted(
        (e for e in entradas if e.estado == ESTADO_TRIGGERED and e.actualizado_en[:10] == hoy),
        key=lambda e: e.actualizado_en, reverse=True,
    )
    watching = sorted((e for e in entradas if e.estado == ESTADO_WATCHING), key=lambda e: e.ticker)

    if not triggered_hoy and not watching:
        return "📡 MARKET RADAR\n\nNo hay oportunidades activas en este momento."

    lineas = ["📡 MARKET RADAR"]
    if triggered_hoy:
        lineas += ["", "🟢 TRIGGERED"]
        lineas += [f"{e.ticker} -- entrada confirmada" for e in triggered_hoy]
    if watching:
        lineas += ["", "🟡 WATCHING"]
        lineas += [f"{e.ticker} -- esperando confirmación" for e in watching]
    return "\n".join(lineas)


AYUDA_MOMENTUM = (
    "📡 Momentum Opportunity Hunter -- comandos:\n\n"
    "/status -- oportunidades activas\n"
    "/radar -- oportunidades en vigilancia\n"
    "/trade TICKER -- plan de una acción (ej. /trade RKLB)\n"
    "/help -- este mensaje\n\n"
    "Solo lectura: ningún comando ejecuta operaciones, ni se conecta a "
    "un broker. La decisión y la ejecución siempre son tuyas."
)
