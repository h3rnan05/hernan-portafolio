"""Seguimiento del ciclo de vida de cada trade paper -- la otra mitad de
"que me avise qué hizo": colocar la orden es el principio de la historia,
no el final. Alpaca resuelve las salidas solo (bracket OCO), pero sin
esto el usuario tendría que abrir el dashboard para enterarse de si la
entrada se llenó, si tocó el objetivo o si lo sacó el stop -- exactamente
la verificación manual que este sistema existe para eliminar.

En cada corrida, para cada revisión con orden viva (`entro=True` y
`resultado` no terminal), consulta el estado real de la orden en Alpaca
(`estado_orden`, solo lectura) y avisa por Telegram EXACTAMENTE UNA VEZ
por cada transición que sea un trade completado:

  - entrada llenada            -> "abierta"      (precio real de ejecución)
  - salida por take-profit     -> "objetivo"     (con ganancia realizada)
  - salida por stop-loss       -> "stop"         (con pérdida realizada)
  - cierre por otra vía        -> "cerrada"      (ERROR: llena sin salidas)

`no_ejecutada` (limit expiró/se canceló sin fill) se persiste igual, pero
NO se manda a Telegram -- no hubo trade. Ver `notify.py`.

El anti-duplicado es la persistencia misma (`revisiones.json`): se guarda
el nuevo `resultado` ANTES de enviar el mensaje -- mismo orden
persistir-antes-de-enviar que ya usa momentum_hunter, con el mismo
compromiso documentado (un crash entre guardar y enviar pierde ese aviso,
nunca lo duplica).

Fallos por orden (red, orden vieja purgada por Alpaca, etc.) se loguean y
se sigue con las demás -- nunca tumban la corrida ni bloquean al executor."""

from __future__ import annotations

import logging

from momentum_paper_trader import estado, notify
from momentum_paper_trader.alpaca_client import AlpacaPaperClient
from momentum_paper_trader.notify import enviar as enviar_telegram

log = logging.getLogger("momentum_paper_trader.seguimiento")

_ESTADOS_ORDEN_MUERTA = frozenset({"canceled", "expired", "rejected", "done_for_day"})


def _num(v) -> float | None:
    """Alpaca devuelve los números como strings -- tolerante a None/basura."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pata_de_salida_llenada(datos: dict) -> dict | None:
    for leg in datos.get("legs") or []:
        if leg.get("status") == "filled":
            return leg
    return None


def _patas_todas_muertas(datos: dict) -> bool:
    legs = datos.get("legs") or []
    return bool(legs) and all(leg.get("status") in _ESTADOS_ORDEN_MUERTA for leg in legs)


def _evaluar(r: estado.RevisionIA, datos: dict) -> tuple[str, float | None, str] | None:
    """(nuevo resultado, pnl, mensaje) para esta revisión según el estado
    real de la orden en Alpaca -- None si no hay ninguna novedad que
    avisar. Función pura: toda la lógica de transición en un solo lugar,
    testeable sin red."""
    status = datos.get("status")
    precio_llenado = _num(datos.get("filled_avg_price"))

    if status in _ESTADOS_ORDEN_MUERTA and precio_llenado is None:
        # Se persiste para no reconsultar, pero el mensaje va vacío:
        # sin fill no hay trade, y sin trade no hay Telegram.
        return ("no_ejecutada", None, "")

    if status != "filled" or precio_llenado is None:
        return None   # la entrada sigue esperando -- nada nuevo que contar

    pata = _pata_de_salida_llenada(datos)
    if pata is not None:
        precio_salida = _num(pata.get("filled_avg_price"))
        cantidad = _num(datos.get("filled_qty")) or (r.cantidad or 0)
        pnl = None
        if precio_salida is not None and cantidad:
            pnl = round((precio_salida - precio_llenado) * cantidad, 2)
        # La pata take_profit es una orden "limit"; la de stop_loss es
        # "stop" (o "stop_limit") -- así distingue Alpaca las dos salidas
        # del bracket en `legs`.
        if pata.get("type") == "limit":
            resultado, motivo = "objetivo", notify.MOTIVO_OBJETIVO
        else:
            resultado, motivo = "stop", notify.MOTIVO_STOP
        return (resultado, pnl, notify.formatear_cerrada(
            ticker=r.ticker, motivo=motivo, signal_id=r.creado_en,
            cantidad=cantidad, precio_entrada=precio_llenado,
            precio_salida=precio_salida, pnl=pnl,
        ))

    if _patas_todas_muertas(datos):
        return ("cerrada", None, notify.formatear_error(
            tipo="posición sin salidas",
            ticker=r.ticker,
            signal_id=r.creado_en,
            detalle="Entrada llena y las dos salidas quedaron inactivas. Revisar en el dashboard paper -- no se reponen solas.",
        ))

    if r.resultado is None:
        cantidad = _num(datos.get("filled_qty")) or (r.cantidad or 0)
        return ("abierta", None, notify.formatear_llenada(
            ticker=r.ticker, signal_id=r.creado_en, cantidad=cantidad,
            precio_lleno=precio_llenado, precio_limite=r.precio_entrada,
            stop=r.stop, objetivo=r.objetivo,
        ))

    return None   # ya está "abierta" y las salidas siguen vivas -- sin novedades


def revisar(client: AlpacaPaperClient) -> list[estado.RevisionIA]:
    """Devuelve las revisiones que cambiaron de estado en esta pasada.
    Guarda ANTES de enviar cada aviso (ver docstring del módulo)."""
    revisiones = estado.cargar()
    cambiadas: list[estado.RevisionIA] = []

    for r in revisiones:
        if not r.entro or not r.order_id:
            continue
        if r.resultado in estado.RESULTADOS_TERMINALES:
            continue
        try:
            datos = client.estado_orden(r.order_id)
        except Exception as ex:
            log.warning("%s: no se pudo consultar la orden %s: %s", r.ticker, r.order_id, ex)
            continue

        novedad = _evaluar(r, datos)
        if novedad is None:
            continue
        r.resultado, r.pnl, mensaje = novedad
        estado.guardar(revisiones)
        if notify.debe_avisar(r.resultado) and mensaje:
            enviar_telegram(mensaje)
        cambiadas.append(r)
        log.info("%s: trade ahora '%s' (pnl=%s)", r.ticker, r.resultado, r.pnl)

    return cambiadas
