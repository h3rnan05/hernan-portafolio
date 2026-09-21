"""Aviso cuando la IA no puede decidir y la señal se queda TRIGGERED.

El 2026-09-21, entre ~11:05 y ~12:20 MTY, Anthropic respondió HTTP 400
("credit balance too low"). `decidir` falló cerrado (`entrar=False`,
`fallo_tecnico=True`), el ejecutor no persistió la revisión — correcto,
la señal no se quema — y reintentó cada ~5 min sin Telegram ni píldora
en el panel. Una hora de TRIGGERED se fue en silencio.

Esto no decide ni coloca. Solo cuenta corridas seguidas con fallo
técnico y, al cruzar el umbral, deja un evento `ia_fallo_tecnico` (el
panel lo pinta en rojo) y un Telegram. El aviso de Telegram es como
máximo uno por día UTC y por clase (`credito` aparte del resto), el
mismo criterio que `persist_fallido` en `scripts/run_watchlist_paper.sh`:
otro motivo, otro archivo de marca, para no taparse entre sí ni repetir
el mismo aviso cada 5 minutos.

Umbral: el saldo y la falta de clave no se arreglan solos, así que
avisan en la primera corrida. Un fallo de red o un veredicto ilegible
puede ser de una sola pasada: avisan a la tercera corrida seguida
(~15 min con el cron de 5). Una decisión real pone la racha en cero.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("momentum_paper_trader.aviso_fallo_ia")

# Corridas seguidas con este código antes de avisar. `credito` y
# `sin_clave` no son un blip: mientras no se recargue o no haya clave,
# cada reintento falla igual.
UMBRAL_CONSECUTIVOS = {
    "credito": 1,
    "sin_clave": 1,
    "api": 3,
    "respuesta": 3,
}

# El saldo tiene su propio cupo diario. El resto comparte uno, para no
# mandar un Telegram por cada etiqueta distinta del mismo corte.
_CLASE_AVISO = {
    "credito": "credito",
    "sin_clave": "tecnico",
    "api": "tecnico",
    "respuesta": "tecnico",
}

_PRIORIDAD = ("credito", "sin_clave", "api", "respuesta")

_MOTIVO = {
    "credito": "saldo Anthropic insuficiente",
    "sin_clave": "sin ANTHROPIC_API_KEY",
    "api": "consulta a la IA falló",
    "respuesta": "veredicto de la IA ilegible",
}

_MENSAJE = {
    "credito": (
        "🧪 [PAPER] ERROR: saldo Anthropic insuficiente "
        "(credit balance / HTTP 400). La señal sigue TRIGGERED, "
        "no se colocó orden (fail-closed) y se reintenta solo. "
        "Aviso como máximo uno por día."
    ),
    "sin_clave": (
        "🧪 [PAPER] ERROR: falta ANTHROPIC_API_KEY. No se puede pedir "
        "criterio a la IA, no se colocó orden (fail-closed) y la señal "
        "sigue TRIGGERED. Aviso como máximo uno por día."
    ),
    "api": (
        "🧪 [PAPER] ERROR: la consulta a la IA falló en varias corridas "
        "seguidas. No se colocó orden (fail-closed) y la señal sigue "
        "TRIGGERED. Aviso como máximo uno por día."
    ),
    "respuesta": (
        "🧪 [PAPER] ERROR: la IA no devolvió un veredicto usable en "
        "varias corridas seguidas. No se colocó orden (fail-closed) y "
        "la señal sigue TRIGGERED. Aviso como máximo uno por día."
    ),
}

try:
    from dashboard.events import log_event
except Exception:  # pragma: no cover - sin panel instalado
    def log_event(tipo: str, **campos) -> None:
        return None


def observar_corrida(
    *,
    fallos: list[tuple[str, str]],
    hubo_decision: bool,
    dry_run: bool = False,
) -> None:
    """Registra la corrida. Nunca lanza: un aviso no puede tumbar el bot
    ni cambiar lo que ya decidió el ejecutor."""
    if dry_run:
        return
    try:
        _observar(fallos, hubo_decision)
    except Exception as ex:
        log.warning("aviso de fallo de IA no registrado: %s", type(ex).__name__)


def _hoy() -> str:
    return datetime.now(UTC).date().isoformat()


def ruta_estado() -> Path:
    base = os.environ.get("MOMENTUM_AVISOS_DIR", "/var/lib/momentum")
    return Path(base) / "ia_fallo_tecnico.json"


def _observar(fallos: list[tuple[str, str]], hubo_decision: bool) -> None:
    codigo, tickers = _codigo_de_corrida(fallos, hubo_decision)
    estado = _cargar()
    hoy = _hoy()
    if estado.get("fecha") != hoy:
        estado["consecutivos"] = 0
        estado["fecha"] = hoy
    avisado = estado.get("avisado")
    if not isinstance(avisado, dict):
        avisado = {}
        estado["avisado"] = avisado

    if codigo is None:
        # Sin fallo, y la IA sí respondió: la racha se corta. Sin ninguno
        # de los dos (no hubo nada que preguntar) no se toca la racha:
        # un mercado cerrado no es evidencia de que el saldo volvió.
        # Si ya estaba en cero, no reescribimos el archivo cada 5 min.
        if hubo_decision and int(estado.get("consecutivos") or 0) != 0:
            estado["consecutivos"] = 0
            estado["codigo"] = None
            estado["fecha"] = hoy
            _guardar(estado)
        return

    consecutivos = int(estado.get("consecutivos") or 0) + 1
    estado["consecutivos"] = consecutivos
    estado["codigo"] = codigo
    estado["fecha"] = hoy
    _guardar(estado)

    umbral = UMBRAL_CONSECUTIVOS[codigo]
    if consecutivos < umbral:
        return

    motivo = _MOTIVO[codigo]
    try:
        log_event(
            "ia_fallo_tecnico",
            codigo=codigo,
            consecutivos=consecutivos,
            motivo=motivo,
            tickers=",".join(_tickers_avisables(tickers)),
        )
    except Exception:
        pass

    clase = _CLASE_AVISO[codigo]
    if avisado.get(clase) == hoy:
        log.info("ia_fallo_tecnico ya avisado hoy (%s); no se repite el Telegram", clase)
        return
    if not _enviar_telegram(_texto(codigo, consecutivos, tickers)):
        return
    avisado[clase] = hoy
    estado["avisado"] = avisado
    _guardar(estado)


def _codigo_de_corrida(
    fallos: list[tuple[str, str]], hubo_decision: bool,
) -> tuple[str | None, list[str]]:
    """Un código por corrida, no por ticker: dos símbolos sin saldo son
    el mismo corte. Si otro ticker sí obtuvo decisión, no era un corte
    de cuenta (saldo o clave)."""
    vistos: dict[str, list[str]] = {}
    for ticker, codigo in fallos:
        if codigo == "sin_niveles":
            continue
        if codigo not in UMBRAL_CONSECUTIVOS:
            codigo = "api"
        if hubo_decision and codigo in ("credito", "sin_clave"):
            continue
        vistos.setdefault(codigo, []).append(str(ticker))
    for codigo in _PRIORIDAD:
        if codigo in vistos:
            return codigo, vistos[codigo]
    return None, []


def _texto(codigo: str, consecutivos: int, tickers: list[str]) -> str:
    sims = ", ".join(_tickers_avisables(tickers))
    extra = f" Tickers: {sims}." if sims else ""
    return f"{_MENSAJE[codigo]}{extra} Corridas seguidas: {consecutivos}."


def _tickers_avisables(tickers: list[str]) -> list[str]:
    limpios: list[str] = []
    for t in tickers:
        s = str(t).strip().upper()
        if not s or len(s) > 12:
            continue
        if all(c.isalnum() or c in ".-" for c in s):
            limpios.append(s)
        if len(limpios) == 5:
            break
    return limpios


def _cargar() -> dict:
    try:
        data = json.loads(ruta_estado().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _guardar(estado: dict) -> None:
    ruta = ruta_estado()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, ruta)


def _script_telegram() -> str:
    override = os.environ.get("MOMENTUM_NOTIFY_TELEGRAM")
    if override:
        return override
    return str(Path(__file__).resolve().parents[1] / "scripts" / "notify_telegram.sh")


def _enviar_telegram(texto: str) -> bool:
    """Mismo script que el persist fallido y el watchdog. True solo si
    salió con 0: si no hay credenciales, la marca no se guarda y la
    corrida siguiente puede reintentar el aviso."""
    try:
        r = subprocess.run(
            ["bash", _script_telegram(), texto],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as ex:
        log.warning("telegram de fallo de IA no enviado: %s", type(ex).__name__)
        return False
    if r.returncode != 0:
        log.warning("telegram de fallo de IA no enviado (rc=%s)", r.returncode)
        return False
    return True
