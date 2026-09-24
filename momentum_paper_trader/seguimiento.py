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
NO se manda a Telegram -- no hubo trade. Ver `notify.py`. Excepción
(2026-09-22): si es ESTE módulo el que cancela la entrada por vencida
(`cfg.minutos_maximos_entrada_sin_llenar`), sí avisa CANCELADA, porque
antes salió COLOCADA y la historia tiene que cerrarse.

El anti-duplicado es la persistencia misma (`revisiones.json`): se guarda
el nuevo `resultado` ANTES de enviar el mensaje -- mismo orden
persistir-antes-de-enviar que ya usa momentum_hunter, con el mismo
compromiso documentado (un crash entre guardar y enviar pierde ese aviso,
nunca lo duplica).

Fallos por orden (red, orden vieja purgada por Alpaca, etc.) se loguean y
se sigue con las demás -- nunca tumban la corrida ni bloquean al executor."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from momentum_paper_trader import estado, notify
from momentum_paper_trader.alpaca_client import AlpacaPaperClient
from momentum_paper_trader.config import PaperTraderConfig
from momentum_paper_trader.notify import enviar as enviar_telegram

log = logging.getLogger("momentum_paper_trader.seguimiento")

_ESTADOS_ORDEN_MUERTA = frozenset({"canceled", "expired", "rejected", "done_for_day"})
# Estados en los que la ENTRADA todavía no tocó el mercado: cancelarla no
# deja nada a medias. `partially_filled` queda fuera a propósito: hay
# acciones compradas con sus patas vivas, y cancelar el resto es otra
# decisión (limitación anotada, no resuelta).
_ESTADOS_ENTRADA_ESPERANDO = frozenset({"new", "accepted", "pending_new", "held"})


def _parse_ts(valor) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=UTC)


def entrada_vencida(r: estado.RevisionIA, datos: dict, cfg: PaperTraderConfig, ahora: datetime) -> float | None:
    """Minutos que lleva esperando una entrada que ya superó el tope, o
    None si todavía no toca cancelarla (o no se puede saber). Función
    pura: sin timestamp legible no se cancela nada (regla 6)."""
    if datos.get("status") not in _ESTADOS_ENTRADA_ESPERANDO:
        return None
    if (_num(datos.get("filled_qty")) or 0) > 0:
        return None
    colocada = _parse_ts(r.timestamp)
    if colocada is None:
        return None
    minutos = (ahora - colocada).total_seconds() / 60
    return minutos if minutos > cfg.minutos_maximos_entrada_sin_llenar else None


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


def _evaluar(r: estado.RevisionIA, datos: dict, cierre_datos: dict | None = None) -> tuple[str, float | None, str] | None:
    """(nuevo resultado, pnl, mensaje) para esta revisión según el estado
    real de la orden en Alpaca -- None si no hay ninguna novedad que
    avisar. `cierre_datos` es el estado de la orden de liquidación de fin
    de día (`r.cierre_order_id`), si la hay. Función pura: toda la lógica
    de transición en un solo lugar, testeable sin red."""
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
        if r.cierre_order_id:
            # Cierre deliberado de fin de día (`cierre.py`). Se confirma el
            # llenado REAL de la orden de liquidación antes de dar el trade
            # por cerrado: aceptar no es llenar.
            estado_c = (cierre_datos or {}).get("status")
            precio_c = _num((cierre_datos or {}).get("filled_avg_price"))
            if estado_c == "filled" and precio_c is not None:
                cantidad = _num(datos.get("filled_qty")) or (r.cantidad or 0)
                pnl = round((precio_c - precio_llenado) * cantidad, 2) if cantidad else None
                # Sin Telegram: el resumen de fin de día ya lo mandó `cierre.py`.
                return ("cerrada", pnl, "")
            if estado_c in _ESTADOS_ORDEN_MUERTA:
                # La liquidación murió SIN llenarse: la posición sigue abierta
                # y desprotegida -> el ERROR de seguridad debe salir.
                return ("cerrada", None, notify.formatear_error(
                    tipo="liquidación de cierre no llenada",
                    ticker=r.ticker,
                    signal_id=r.creado_en,
                    detalle="El cierre de fin de día se aceptó pero la orden de liquidación no se llenó. La posición puede seguir abierta y sin salidas. Revisar en Alpaca -- no se repone sola.",
                ))
            # Todavía pendiente de llenarse: se reintenta en la próxima pasada.
            return None
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


def _cancelar_vencida(client: AlpacaPaperClient, r: estado.RevisionIA, minutos: float) -> bool:
    """Cancela la entrada vencida en Alpaca. True solo si de verdad se
    canceló algo: un fallo leyendo o cancelando deja la orden como está y
    se reintenta en la pasada siguiente (nunca se marca no_ejecutada una
    orden que sigue viva)."""
    try:
        abiertas = client.ordenes_abiertas()
    except Exception as ex:
        log.warning("%s: no se pudieron leer las órdenes abiertas para cancelar la entrada vencida: %s", r.ticker, ex)
        return False
    vivas = [o for o in abiertas if o.get("symbol") == r.ticker and o.get("id") == r.order_id]
    if not vivas:
        # Ya no está entre las abiertas (se llenó o murió hace un instante):
        # la próxima consulta de `estado_orden` lo dirá. No se toca nada.
        return False
    canceladas = client.cancelar_ordenes_de(r.ticker, vivas)
    if canceladas < 1:
        return False
    log.info("%s: entrada sin llenar en %.0f min -- cancelada", r.ticker, minutos)
    return True


def revisar(
    client: AlpacaPaperClient, cfg: PaperTraderConfig | None = None, ahora: datetime | None = None,
) -> list[estado.RevisionIA]:
    """Devuelve las revisiones que cambiaron de estado en esta pasada.
    Guarda ANTES de enviar cada aviso (ver docstring del módulo)."""
    cfg = cfg or PaperTraderConfig()
    ahora = ahora or datetime.now(UTC)
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

        cierre_datos = None
        if r.cierre_order_id:
            try:
                cierre_datos = client.estado_orden(r.cierre_order_id)
            except Exception as ex:
                log.warning("%s: no se pudo consultar la orden de liquidación %s: %s", r.ticker, r.cierre_order_id, ex)
                # Sin poder confirmar el fill, no se da por cerrado este pase.
                continue
        novedad = _evaluar(r, datos, cierre_datos)
        if novedad is None:
            # Entrada todavía esperando: ¿ya venció? (2026-09-22, revisión
            # de riesgo). Si sí y se canceló de verdad, se cierra la
            # historia: no_ejecutada + CANCELADA por Telegram.
            minutos = entrada_vencida(r, datos, cfg, ahora)
            if minutos is None or not _cancelar_vencida(client, r, minutos):
                continue
            novedad = ("no_ejecutada", None, notify.formatear_cancelada(
                ticker=r.ticker, signal_id=r.creado_en, minutos=minutos, precio_limite=r.precio_entrada))
        r.resultado, r.pnl, mensaje = novedad
        estado.guardar(revisiones)
        if mensaje and (notify.debe_avisar(r.resultado) or r.resultado == "no_ejecutada"):
            enviar_telegram(mensaje)
        cambiadas.append(r)
        log.info("%s: trade ahora '%s' (pnl=%s)", r.ticker, r.resultado, r.pnl)

    return cambiadas
