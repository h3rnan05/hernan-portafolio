"""Horario de la sesión regular -- lee el calendario local, sin red.

El archivo lo refresca el paper trader (`calendario_job`) desde el
calendario de Alpaca. Este módulo solo lo consulta, igual que el
ejecutor, el cierre, el vigía y el panel. Así el cambio de horario, los
feriados y las medias sesiones salen de un solo lugar.

Si el archivo no está o no cubre hoy, no hay sesión para entrar (el
ejecutor no abre nada) y el cierre de fin de día usa las 13:00 de
Nueva York. Un finde o un feriado que SÍ están dentro del rango del
archivo son "cerrado de verdad": no hay ventana y no hay cierre.

La zona `America/New_York` sigue haciendo el cambio de horario solo.
Lo que antes era un 9:30–16:00 fijo, sin feriados, ya no es la
autoridad.
"""

from __future__ import annotations

from datetime import datetime

from momentum_hunter import calendario


def en_sesion(ahora: datetime) -> bool:
    """¿Está abierta la sesión regular en este instante?"""
    return calendario.en_sesion(ahora)


def minutos_hasta_el_cierre(ahora: datetime) -> float:
    """Minutos hasta el cierre, para decidir si se puede entrar.

    Negativo si ya cerró, si el día no tiene sesión, o si no hay
    calendario para hoy. Antes de la apertura de un día conocido sigue
    siendo positivo: no alcanza con "falta mucho" si todavía no abrió.
    """
    return calendario.minutos_hasta_el_cierre(ahora)


def minutos_para_liquidar(ahora: datetime) -> float:
    """Minutos hasta el cierre que usa el cierre de fin de día.

    Con calendario: el cierre de ese día (13:00 ET en una media sesión).
    Sin calendario para hoy: las 13:00 de Nueva York. Si el día se sabe
    cerrado (feriado), negativo: no hay liquidación que disparar.
    """
    return calendario.minutos_para_liquidar(ahora)


def calendario_desconocido(ahora: datetime) -> bool:
    """True si no hay archivo, no se puede leer, o no cubre el día de hoy."""
    return calendario.consultar(ahora).desconocido


def hay_tiempo_para_operar(ahora: datetime, minutos_minimos: float) -> bool:
    """¿La sesión está abierta Y queda al menos `minutos_minimos` para
    el cierre?

    Los dos minutos importan por separado. Con el mercado cerrado no se
    puede comprar. Y a cinco minutos del cierre tampoco tiene sentido
    empezar: una entrada que llegara a llenarse dejaría una posición que
    hay que liquidar en la misma vela, y las patas de salida del bracket
    (órdenes "del día") morirían al cerrar. Sin calendario para hoy esto
    es False: no se fabrica una señal que nadie puede tomar.
    """
    return en_sesion(ahora) and minutos_hasta_el_cierre(ahora) >= minutos_minimos
