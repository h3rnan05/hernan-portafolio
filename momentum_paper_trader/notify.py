"""Alertas de Telegram del paper trader -- SOLO trades completados.

Política anti-spam (dueño, 2026-09-11): Telegram no es el log del cron.
No se avisa un escaneo, una señal TRIGGERED, un rechazo de la IA, una
orden aceptada-pero-sin-llenar, ni el START/OK de cada ciclo de 5 min.
Tampoco se manda un mensaje silencioso (`disable_notification`) para
taparle el pico a esos eventos -- si no es un trade completado, no se
manda nada.

Sí se avisa, y en un solo chat (el mismo de momentum_hunter):

  1. LLENADA  -- la compra se ejecutó (precio real de fill)
  2. CERRADA  -- salió por objetivo, stop o liquidación de fin de día
  3. ERROR    -- fallo duro que impide operar, o posición llena sin
                 salidas vivas (raro, texto mínimo)

El prefijo 🧪 [PAPER] es innegociable: estos avisos nunca deben
parecerse a una alerta live. HTML escapado; un campo ausente se omite,
nunca se inventa (mismo criterio que el resto del repo).

El envío reusa `momentum_hunter.run.enviar_telegram` -- un chat, mismas
credenciales -- con `parse_mode=HTML`. Falta de secrets no es error
fatal: se loguea y se sigue, igual que antes."""

from __future__ import annotations

import html
from typing import Iterable

from momentum_hunter.run import enviar_telegram

PREFIJO = "🧪 [PAPER]"

# Etiquetas que el usuario ve. Un enum corto, estable, en mayúsculas --
# más fácil de escanear en el celular que una frase distinta por evento.
ESTADO_LLENADA = "LLENADA"
ESTADO_CERRADA = "CERRADA"
ESTADO_ERROR = "ERROR"

# Sub-etiqueta de un cierre (no es un evento extra: viaja en el mismo
# mensaje CERRADA). Español corto, sin jerga de broker.
MOTIVO_OBJETIVO = "objetivo"
MOTIVO_STOP = "stop"
MOTIVO_FIN_DIA = "fin de día"

# Resultados de `estado.RevisionIA` que SÍ merecen Telegram. El resto
# (`None`, `no_ejecutada`) se persiste en silencio.
RESULTADOS_CON_AVISO = frozenset({"abierta", "objetivo", "stop", "cerrada"})

# El razonamiento de la IA puede ser un párrafo. En el celular un muro
# empuja los números fuera de la pantalla -- se corta acá, no en el
# criterio de la IA (eso no se toca).
_MAX_RAZON = 140


def escapar(texto: str) -> str:
    """HTML de Telegram: solo &, < y >. Sin esto un ticker o un
    razonamiento con '<' rompe el parse_mode y el aviso no llega."""
    return html.escape(str(texto), quote=False)


def debe_avisar(resultado: str | None) -> bool:
    """True solo para fill / cierre / error de posición desprotegida."""
    return resultado in RESULTADOS_CON_AVISO


def enviar(texto: str) -> None:
    """Manda HTML al chat compartido. Solo deben llamarla los tres
    eventos de arriba -- esta función no filtra, el caller sí."""
    if not texto:
        return
    enviar_telegram(texto, parse_mode="HTML")


def _dinero(valor: float | None) -> str | None:
    if valor is None:
        return None
    if valor > 0:
        return f"+${valor:,.2f}"
    if valor < 0:
        return f"-${abs(valor):,.2f}"
    return "$0.00"


def _precio(valor: float | None) -> str | None:
    if valor is None:
        return None
    return f"${valor:,.2f}"


def _cantidad(valor: float | int | None) -> str | None:
    if valor is None:
        return None
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return f"{n} acc"


def _razon_corta(texto: str | None) -> str | None:
    if not texto:
        return None
    limpio = " ".join(str(texto).split())
    if not limpio:
        return None
    if len(limpio) > _MAX_RAZON:
        return limpio[: _MAX_RAZON - 1] + "…"
    return limpio


def _armar(estado: str, lineas: Iterable[str | None]) -> str:
    cuerpo = [ln for ln in lineas if ln]
    return "\n".join([f"{PREFIJO} <b>{escapar(estado)}</b>", "", *cuerpo])


def _linea_ticker(ticker: str, extra: str | None = None) -> str:
    cab = f"<b>{escapar(ticker)}</b>"
    return f"{cab} · {escapar(extra)}" if extra else cab


def _linea_senal(signal_id: str | None) -> str | None:
    if not signal_id:
        return None
    return f"Señal: <code>{escapar(signal_id)}</code>"


def formatear_llenada(
    *,
    ticker: str,
    signal_id: str | None = None,
    cantidad: float | int | None = None,
    precio_lleno: float | None = None,
    precio_limite: float | None = None,
    stop: float | None = None,
    objetivo: float | None = None,
) -> str:
    """Entrada ejecutada -- el primer aviso permitido de un trade."""
    qty = _cantidad(cantidad)
    fill = _precio(precio_lleno)
    if qty and fill:
        operacion = f"{qty} @ {fill}"
    elif fill:
        operacion = fill
    else:
        operacion = qty

    limite = _precio(precio_limite)
    # El límite solo aporta si el fill real difiere -- si es el mismo
    # número, repetirlo es ruido en una pantalla chica.
    if fill and limite and fill != limite:
        operacion = f"{operacion} (lím. {limite})" if operacion else f"lím. {limite}"

    stop_txt, obj_txt = _precio(stop), _precio(objetivo)
    niveles = " · ".join(
        p for p in (
            f"Stop {stop_txt}" if stop_txt else None,
            f"Obj {obj_txt}" if obj_txt else None,
        ) if p
    )

    return _armar(ESTADO_LLENADA, (
        _linea_ticker(ticker),
        _linea_senal(signal_id),
        operacion,
        niveles,
    ))


def formatear_cerrada(
    *,
    ticker: str,
    motivo: str,
    signal_id: str | None = None,
    cantidad: float | int | None = None,
    precio_entrada: float | None = None,
    precio_salida: float | None = None,
    pnl: float | None = None,
    razon: str | None = None,
) -> str:
    """Salida completada (objetivo, stop o liquidación)."""
    qty = _cantidad(cantidad)
    entrada, salida = _precio(precio_entrada), _precio(precio_salida)
    if entrada and salida:
        recorrido = f"{entrada} → {salida}"
    else:
        recorrido = entrada or salida
    if qty and recorrido:
        movimiento = f"{qty} · {recorrido}"
    else:
        movimiento = qty or recorrido

    pnl_txt = _dinero(pnl)
    return _armar(ESTADO_CERRADA, (
        _linea_ticker(ticker, motivo),
        _linea_senal(signal_id),
        movimiento,
        f"P&L {pnl_txt}" if pnl_txt else None,
        escapar(_razon_corta(razon)) if _razon_corta(razon) else None,
    ))


def formatear_cierre_dia(
    cerradas: list[tuple[dict, str]],
) -> str:
    """Resumen de liquidaciones de fin de día -- solo las que se
    CERRARON. Las que se aguantan overnight no son un trade completado
    y no entran acá (anti-spam)."""
    if not cerradas:
        return ""

    lineas: list[str | None] = []
    total = 0.0
    hay_pl = False

    for p, razon in cerradas:
        ticker = str(p.get("symbol") or "?")
        pl_raw = p.get("unrealized_pl")
        pl: float | None
        try:
            pl = float(pl_raw) if pl_raw is not None else None
        except (TypeError, ValueError):
            pl = None
        if pl is not None:
            hay_pl = True
            total += pl
        qty = _cantidad(p.get("qty"))
        pl_txt = _dinero(pl)
        detalle = " · ".join(x for x in (qty, pl_txt) if x)
        lineas.append(_linea_ticker(ticker, MOTIVO_FIN_DIA))
        if detalle:
            lineas.append(detalle)
        corta = _razon_corta(razon)
        if corta:
            lineas.append(escapar(corta))

    if hay_pl:
        lineas.append(f"P&L del día {_dinero(total)}")

    return _armar(ESTADO_CERRADA, lineas)


def formatear_error(
    *,
    tipo: str,
    ticker: str | None = None,
    signal_id: str | None = None,
    detalle: str | None = None,
) -> str:
    """Fallo duro. Nunca incluye el texto crudo de la excepción: puede
    traer una URL con credenciales (regla del repo). Solo el TIPO y,
    si hace falta, un detalle que nosotros escribimos."""
    return _armar(ESTADO_ERROR, (
        _linea_ticker(ticker) if ticker else None,
        _linea_senal(signal_id),
        escapar(tipo),
        escapar(detalle) if detalle else "No se operó. Se reintenta solo.",
    ))
