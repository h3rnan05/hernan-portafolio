"""Horario de la sesión regular del mercado de EE.UU. -- puro calendario,
sin red y sin bróker.

POR QUÉ EXISTE (2026-08-27). El resto del proyecto deduce el horario de
dos constantes en horario de VERANO (`factors/intradia.HORA_APERTURA_UTC`
/ `HORA_CIERRE_UTC`). En invierno la sesión se corre una hora entera
(14:30-21:00 UTC) y esas constantes se equivocan en las dos puntas.

Peor: los cron corren `13-20`, y esa hora `20` abarca hasta las 20:55,
o sea que la última hora de escaneo de cada día ocurre ENTERA con el
mercado cerrado. Medido el 2026-08-27 sobre el estado real: 4 de las 6
señales TRIGGERED vivas habían disparado fuera de sesión (20:23, 20:31,
20:42, 20:50 UTC). Ninguna se pudo operar nunca -- el ejecutor las
rechazaba con razón, y para cuando el mercado reabría sus precios ya
estaban rancios.

Se resuelve con la zona horaria real (`America/New_York`) en vez de un
desplazamiento fijo: así el cambio de horario se aplica solo, sin que
nadie tenga que acordarse de editar una constante dos veces al año.

LO QUE NO SABE, dicho sin maquillar: feriados y medias sesiones. Un 4 de
julio esto dice "abierto". Por eso es un filtro BARATO y no la autoridad:
el ejecutor pregunta el reloj de verdad a Alpaca (`GET /v2/clock`, ver
`momentum_paper_trader.executor._mercado_cerrado`), que sí conoce el
calendario. Este módulo existe para que el buscador deje de FABRICAR
señales imposibles, no para autorizar órdenes."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
APERTURA_ET = time(9, 30)
CIERRE_ET = time(16, 0)


def _en_nueva_york(ahora: datetime) -> datetime:
    return ahora.astimezone(_NY)


def en_sesion(ahora: datetime) -> bool:
    """¿Está abierta la sesión regular en este instante? (Sin feriados --
    ver el docstring del módulo.)"""
    ny = _en_nueva_york(ahora)
    if ny.weekday() >= 5:   # sábado, domingo
        return False
    return APERTURA_ET <= ny.time() < CIERRE_ET


def minutos_hasta_el_cierre(ahora: datetime) -> float:
    """Minutos que faltan para el cierre regular. Negativo si ya cerró,
    y también negativo fuera de un día hábil -- quien llame solo tiene
    que preguntar "¿queda tiempo?", sin distinguir los casos."""
    ny = _en_nueva_york(ahora)
    cierre = ny.replace(hour=CIERRE_ET.hour, minute=CIERRE_ET.minute,
                        second=0, microsecond=0)
    if ny.weekday() >= 5:
        return -1.0
    return (cierre - ny).total_seconds() / 60.0


def hay_tiempo_para_operar(ahora: datetime, minutos_minimos: float) -> bool:
    """¿La sesión está abierta Y queda al menos `minutos_minimos` para
    el cierre?

    Los dos minutos importan por separado. Con el mercado cerrado no se
    puede comprar. Y a cinco minutos del cierre tampoco tiene sentido
    empezar: una entrada que llegara a llenarse dejaría una posición que
    hay que liquidar en la misma vela, y las patas de salida del bracket
    (órdenes "del día") morirían al cerrar."""
    return en_sesion(ahora) and minutos_hasta_el_cierre(ahora) >= minutos_minimos
