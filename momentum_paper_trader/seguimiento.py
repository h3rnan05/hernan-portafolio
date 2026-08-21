"""Seguimiento del ciclo de vida de cada trade paper -- la otra mitad de
"que me avise qué hizo": colocar la orden es el principio de la historia,
no el final. Alpaca resuelve las salidas solo (bracket OCO), pero sin
esto el usuario tendría que abrir el dashboard para enterarse de si la
entrada se llenó, si tocó el objetivo o si lo sacó el stop -- exactamente
la verificación manual que este sistema existe para eliminar.

En cada corrida, para cada revisión con orden viva (`entro=True` y
`resultado` no terminal), consulta el estado real de la orden en Alpaca
(`estado_orden`, solo lectura) y avisa por Telegram EXACTAMENTE UNA VEZ
por cada transición:

  - entrada llenada            -> "abierta"      (precio real de ejecución)
  - salida por take-profit     -> "objetivo"     (con ganancia realizada)
  - salida por stop-loss       -> "stop"         (con pérdida realizada)
  - entrada nunca llenada      -> "no_ejecutada" (la limit expiró/se canceló)
  - cierre por otra vía        -> "cerrada"

El anti-duplicado es la persistencia misma (`revisiones.json`): se guarda
el nuevo `resultado` ANTES de enviar el mensaje -- mismo orden
persistir-antes-de-enviar que ya usa momentum_hunter, con el mismo
compromiso documentado (un crash entre guardar y enviar pierde ese aviso,
nunca lo duplica).

Fallos por orden (red, orden vieja purgada por Alpaca, etc.) se loguean y
se sigue con las demás -- nunca tumban la corrida ni bloquean al executor."""

from __future__ import annotations

import logging

from momentum_hunter.run import enviar_telegram

from momentum_paper_trader import estado
from momentum_paper_trader.alpaca_client import AlpacaPaperClient

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
        return ("no_ejecutada", None, (
            f"🧪 [PAPER] ORDEN NO EJECUTADA -- {r.ticker}\n\n"
            f"La entrada límite a ${r.precio_entrada:,.2f} nunca se llenó y la orden "
            f"quedó {status}. Sin posición abierta, sin riesgo tomado.\n\n"
            f"Cuenta de práctica -- ningún dinero real se movió."
        ))

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
            resultado, titulo = "objetivo", "🎯 OBJETIVO ALCANZADO"
        else:
            resultado, titulo = "stop", "🛑 STOP EJECUTADO"
        linea_pnl = (
            f"Resultado: {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f}\n" if pnl is not None else "")
        salida_txt = f"${precio_salida:,.2f}" if precio_salida is not None else "precio no informado"
        return (resultado, pnl, (
            f"🧪 [PAPER] {titulo} -- {r.ticker}\n\n"
            f"Entró a ${precio_llenado:,.2f}, salió a {salida_txt} "
            f"({int(cantidad) if cantidad else '?'} acciones).\n"
            f"{linea_pnl}\n"
            f"Cuenta de práctica -- ningún dinero real se movió."
        ))

    if _patas_todas_muertas(datos):
        return ("cerrada", None, (
            f"🧪 [PAPER] POSICIÓN SIN SALIDAS ACTIVAS -- {r.ticker}\n\n"
            f"La entrada se llenó a ${precio_llenado:,.2f} pero las dos salidas del "
            f"bracket quedaron inactivas (expiradas/canceladas). Revisa la posición en "
            f"el dashboard de Alpaca -- este sistema no coloca salidas nuevas por su cuenta.\n\n"
            f"Cuenta de práctica -- ningún dinero real se movió."
        ))

    if r.resultado is None:
        cantidad = _num(datos.get("filled_qty")) or (r.cantidad or 0)
        return ("abierta", None, (
            f"🧪 [PAPER] ENTRADA EJECUTADA -- {r.ticker}\n\n"
            f"Se llenó la compra de {int(cantidad) if cantidad else '?'} acciones a "
            f"${precio_llenado:,.2f} (límite era ${r.precio_entrada:,.2f}).\n"
            f"Alpaca ya vigila las salidas: stop ${r.stop:,.2f} / objetivo ${r.objetivo:,.2f}.\n\n"
            f"Cuenta de práctica -- ningún dinero real se movió."
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
        enviar_telegram(mensaje)
        cambiadas.append(r)
        log.info("%s: trade ahora '%s' (pnl=%s)", r.ticker, r.resultado, r.pnl)

    return cambiadas
