"""Cierre diario -- no dejar ninguna posición abierta de un día para otro.

EL HUECO QUE ESTO TAPA (encontrado el 2026-08-21). Las órdenes bracket se
mandan con `time_in_force: "day"`, así que sus dos patas de salida
(take-profit y stop-loss) se cancelan solas al cerrar el mercado. Si la
compra se llenó a las 10 de la mañana y para el cierre no tocó ni el stop
ni el objetivo, la posición queda abierta durante la noche SIN STOP Y SIN
OBJETIVO -- desprotegida contra cualquier hueco de apertura del día
siguiente. `seguimiento.py` sabe detectar ese estado y avisarlo, pero
avisar no es arreglarlo.

LA DECISIÓN (usuario, 2026-08-21): liquidar todo antes del cierre. Es
además lo coherente con la estrategia -- este bot busca movimientos
INTRADÍA (catalizador del día, patrones de Ross Cameron, ventana de
minutos); mantener una posición de un día para otro es una apuesta
distinta, con riesgos distintos (huecos de apertura, noticias nocturnas)
que nada en este sistema evalúa. Cerrar es el default honesto.

CUÁNDO. `cfg.minutos_antes_del_cierre` antes de las 20:00 UTC (16:00 ET),
o sea 19:50 UTC por defecto. El re-chequeo de watchlist corre cada 5
minutos hasta las 20:00, así que siempre hay al menos una corrida dentro
de esa ventana. Misma convención de horario de verano que ya usan los
cron y `factors/intradia` -- con la misma limitación honesta: en horario
de invierno el mercado cierra a las 21:00 UTC y esta ventana quedaría una
hora antes de tiempo (cerraría a las 14:50 ET). Anotado, no resuelto:
requiere una fuente de calendario de mercado que este proyecto no tiene.

IDEMPOTENTE por construcción: la segunda corrida dentro de la ventana ya
no encuentra posiciones y no hace nada. No hace falta estado persistido."""

from __future__ import annotations

import logging
from datetime import datetime

from momentum_hunter.factors.intradia import HORA_CIERRE_UTC
from momentum_hunter.run import enviar_telegram

from momentum_paper_trader.alpaca_client import AlpacaPaperClient
from momentum_paper_trader.config import PaperTraderConfig

log = logging.getLogger("momentum_paper_trader.cierre")


def _hora_utc(ahora: datetime) -> float:
    return ahora.hour + ahora.minute / 60.0


def en_ventana_de_cierre(ahora: datetime, cfg: PaperTraderConfig) -> bool:
    """¿Estamos en los últimos minutos de la sesión regular?

    La ventana va desde `minutos_antes_del_cierre` antes del cierre hasta
    el cierre mismo. Después de las 20:00 UTC ya no se intenta: el
    mercado está cerrado y una orden a mercado no se ejecutaría hasta el
    día siguiente -- justo lo contrario de lo que se busca."""
    inicio = HORA_CIERRE_UTC - cfg.minutos_antes_del_cierre / 60.0
    return inicio <= _hora_utc(ahora) < HORA_CIERRE_UTC


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mensaje(posiciones: list[dict]) -> str:
    """Un resumen de lo que se liquidó, con el resultado de cada una.
    `unrealized_pl` es la ganancia/pérdida en el momento de cerrar: como
    se liquida a mercado en ese instante, es la cifra realizada salvo por
    el deslizamiento de los segundos siguientes -- se dice "aprox." en
    vez de fingir precisión que no se midió."""
    lineas = ["🧪 [PAPER] CIERRE DEL DÍA", "",
              "Se liquidó todo antes del cierre para no dejar posiciones "
              "desprotegidas durante la noche.", ""]
    total = 0.0
    for p in posiciones:
        ticker = p.get("symbol", "?")
        cantidad = _num(p.get("qty")) or 0
        pl = _num(p.get("unrealized_pl"))
        if pl is not None:
            total += pl
            signo = "+" if pl >= 0 else "-"
            lineas.append(f"{ticker}: {int(cantidad)} acciones, {signo}${abs(pl):,.2f} aprox.")
        else:
            lineas.append(f"{ticker}: {int(cantidad)} acciones")
    if any(_num(p.get("unrealized_pl")) is not None for p in posiciones):
        signo = "+" if total >= 0 else "-"
        lineas += ["", f"Resultado del día: {signo}${abs(total):,.2f} aprox."]
    lineas += ["", "Cuenta de práctica -- ningún dinero real se movió."]
    return "\n".join(lineas)


def cerrar_si_toca(
    client: AlpacaPaperClient, cfg: PaperTraderConfig, ahora: datetime,
) -> list[dict]:
    """Liquida todo si estamos en la ventana de cierre. Devuelve las
    posiciones que había (vacío si no tocaba o si no había ninguna).

    Nunca lanza: un fallo acá no debe tumbar la corrida ni impedir que el
    resto del trader funcione -- se loguea y se reintenta en la corrida
    siguiente, que todavía está dentro de la ventana."""
    if not cfg.cerrar_antes_del_cierre or not en_ventana_de_cierre(ahora, cfg):
        return []

    try:
        posiciones = client.posiciones()
    except Exception as ex:
        log.warning("no se pudieron leer las posiciones para el cierre diario: %s", ex)
        return []
    if not posiciones:
        log.info("cierre diario: no hay posiciones abiertas")
        return []

    try:
        client.cerrar_todas_las_posiciones()
    except Exception as ex:
        log.warning("falló el cierre diario de posiciones: %s", ex)
        return []

    log.info("cierre diario: %d posición(es) liquidada(s)", len(posiciones))
    enviar_telegram(_mensaje(posiciones))
    return posiciones
