"""Remanente sin stop tras un take-profit parcial: se vuelve a proteger.

QUÉ PASÓ (FCEL, 2026-10-02). Bracket de compra 42 @ 17.30, stop 17.05,
objetivo 17.90. A las 13:45:03 UTC el take-profit se llenó EN PARTE
(37 @ 17.90) y Alpaca canceló la pata stop del OCO en ese mismo
instante: en un OCO, cualquier ejecución de una pata cancela la otra,
aunque sea parcial. Las 5 acciones restantes quedaron solo con el resto
del take-profit (un `limit` por encima del precio): sin stop. Nadie hizo
nada durante 67 minutos. `seguimiento.py` solo mira patas `filled` y
`reconciliacion.py` solo avisa (por diseño, no coloca órdenes). Se
salvó porque el resto del objetivo se llenó a las 14:52:18 UTC.

QUÉ HACE ESTE MÓDULO, en cada tick del vigía (después del cierre diario
y antes de la reconciliación):

  1. Para cada revisión viva con orden, lee la orden padre con sus patas.
     Sigue solo si `remanente_sin_stop`: padre lleno, una pata limit de
     venta con 0 < filled_qty < qty, y ninguna pata stop viva ni llena.
  2. Si el símbolo ya tiene una venta stop o a mercado viva (un stop que
     este módulo puso antes, la liquidación del cierre), no hace nada.
  3. Cancela el resto del take-profit (retiene la cantidad: sin soltarlo,
     Alpaca rechaza cualquier otra venta) y ESPERA un status terminal.
     Si el resto se llenó mientras tanto, ya no hay nada que proteger.
  4. Relee la posición y coloca un stop `gtc` al precio de stop ORIGINAL
     de la revisión, por min(qty, qty_available).
  5. Si el precio ya está en o bajo el stop, o Alpaca rechaza el stop,
     vende el remanente a mercado (fail-closed: nunca se deja sin salida).
  6. Cada acción sale por Telegram. Si nada de lo anterior se pudo
     (lecturas fallidas, pata que no suelta, venta rechazada), sale un
     ERROR URGENTE una vez por sesión y el tick siguiente reintenta.

No cambia umbrales, tamaño, límites ni la política de la IA: el stop es
el que ya decidió el pipeline (`RevisionIA.stop`). En la ventana de
cierre no actúa: ahí manda `cierre.py`, que liquida o pone su propio stop
nocturno. Un `client_order_id` determinista por orden padre impide que
un reintento coloque dos stops o dos ventas.

La orden que coloca se anota en `reprotecciones.json` (junto a
`posiciones_avisos.json`, fuera de git), no en `revisiones.json`: una
columna nueva en el libro haría que una versión anterior del código
descartara todas sus filas al cargarlo. `seguimiento.py` lee esa orden
para cerrar la historia del trade con el P&L de las dos partes."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from momentum_paper_trader import dedupe_avisos, estado, notify
from momentum_paper_trader.alpaca_client import (
    AlpacaPaperClient,
    orden_sigue_viva,
    orden_ya_terminada,
)
from momentum_paper_trader.config import PaperTraderConfig

log = logging.getLogger("momentum_paper_trader.reproteccion")

_TIPOS_STOP = frozenset({"stop", "stop_limit", "trailing_stop"})

# Desenlaces de `_reproteger_uno`. Los de `_SIN_AVISO_ERROR` no son
# fallos: no había nada que proteger, o ya estaba protegido.
STOP_REPUESTO = "stop_repuesto"
VENDIDO_MERCADO = "vendido_mercado"
YA_PROTEGIDA = "ya_protegida"
SIN_POSICION = "sin_posicion"
FALLO = "fallo"


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


def _texto_qty(n: float) -> str | None:
    if not n > 0:
        return None
    if n == int(n):
        return str(int(n))
    return f"{n:.9f}".rstrip("0").rstrip(".") or None


# -- detección (pura) ------------------------------------------------------

def remanente_sin_stop(datos: dict) -> bool:
    """True si la orden padre (con `legs`) dejó acciones sin stop por un
    take-profit parcial. Función pura, sin red.

    Exige evidencia, no la supone: padre `filled` con cantidad, una pata
    `limit` de venta con 0 < filled_qty < qty, y ninguna pata stop viva
    (`held`/`new`/...) ni llena. Un stop lleno es una salida por stop,
    no un remanente. Sin `legs` legibles, False: la reconciliación sigue
    avisando cualquier posición sin stop."""
    if not isinstance(datos, dict):
        return False
    if str(datos.get("status") or "").lower() != "filled":
        return False
    if not (_num(datos.get("filled_qty")) or 0) > 0:
        return False
    legs = datos.get("legs")
    if not isinstance(legs, list) or not legs:
        return False
    tp_parcial = False
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        if str(leg.get("side") or "").lower() != "sell":
            continue
        tipo = str(leg.get("type") or "").lower()
        status = str(leg.get("status") or "").lower()
        if tipo in _TIPOS_STOP:
            if orden_sigue_viva(leg) or status in ("filled", "partially_filled"):
                return False
            if not orden_ya_terminada(leg):
                # Status desconocido o ausente: no se afirma que falte.
                return False
        elif tipo == "limit":
            llenas = _num(leg.get("filled_qty")) or 0
            total = _num(leg.get("qty"))
            if total is not None and 0 < llenas < total:
                tp_parcial = True
    return tp_parcial


# -- registro de lo colocado -------------------------------------------------

def ruta() -> Path:
    base = os.environ.get("MOMENTUM_AVISOS_DIR", "/var/lib/momentum")
    return Path(base) / "reprotecciones.json"


def _cargar_registro() -> dict:
    try:
        data = json.loads(ruta().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def orden_de(order_id_padre: str | None) -> dict | None:
    """Lo que este módulo colocó para el remanente de ese padre, o None."""
    if not order_id_padre:
        return None
    fila = _cargar_registro().get(str(order_id_padre))
    return fila if isinstance(fila, dict) and fila.get("order_id") else None


def _anotar(order_id_padre: str, fila: dict) -> None:
    try:
        data = _cargar_registro()
        data[str(order_id_padre)] = fila
        path = ruta()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as ex:
        # La orden ya está en Alpaca y protege igual; lo que se pierde es
        # el P&L combinado en `seguimiento` (sale el ERROR de "sin salidas").
        log.warning("reprotección: no se pudo anotar la orden (%s)", type(ex).__name__)


def _id_cliente(prefijo: str, ticker: str, order_id_padre: str) -> str:
    limpio = "".join(c if c.isalnum() else "-" for c in ticker)
    padre = "".join(c for c in str(order_id_padre) if c.isalnum())[:12]
    return f"{prefijo}-{limpio}-{padre}"[:48]


# -- acción ------------------------------------------------------------------

def _en_ventana_de_cierre(ahora: datetime, cfg: PaperTraderConfig) -> bool:
    from momentum_paper_trader import cierre
    return cierre.en_ventana_de_cierre(ahora, cfg)


def _avisar_fallo(r: estado.RevisionIA, detalle: str, ahora: datetime) -> None:
    marca = dedupe_avisos.clave("reproteccion_fallo", r.ticker, ahora, r.creado_en)
    if dedupe_avisos.ya_avisada(marca):
        log.info("%s: el fallo de reprotección ya se avisó en esta sesión", r.ticker)
        return
    notify.enviar(notify.formatear_error(
        tipo="URGENTE: remanente sin stop tras take-profit parcial",
        ticker=r.ticker,
        signal_id=r.creado_en,
        detalle=detalle,
    ))
    dedupe_avisos.marcar(marca, ahora)


def _leer_posicion(client: AlpacaPaperClient, ticker: str) -> tuple[str, float | None, float | None]:
    """('ausente'|'fallo'|'ok', qty vendible, precio actual)."""
    try:
        p = client.posicion(ticker)
    except Exception as ex:
        log.warning("%s: reprotección: no se pudo leer la posición (%s)", ticker, type(ex).__name__)
        return "fallo", None, None
    if p is None:
        return "ausente", None, None
    if not isinstance(p, dict):
        return "fallo", None, None
    lado = p.get("side")
    if isinstance(lado, str) and lado.strip().lower() not in ("long", ""):
        log.warning("%s: reprotección: la posición no es long; no se toca", ticker)
        return "fallo", None, None
    qty = _num(p.get("qty"))
    if qty is None:
        return "fallo", None, None
    if qty == 0:
        return "ausente", None, None
    disp = _num(p.get("qty_available"))
    vendible = qty if disp is None else min(qty, disp)
    return "ok", vendible, _num(p.get("current_price"))


def _vender(
    client: AlpacaPaperClient, r: estado.RevisionIA, qty: str, motivo: str, ahora: datetime,
) -> str:
    try:
        resp = client.vender_a_mercado(
            r.ticker, qty, client_order_id=_id_cliente("rpm", r.ticker, r.order_id or ""),
        )
    except Exception as ex:
        log.error("%s: reprotección: la venta a mercado del remanente falló (%s)", r.ticker, type(ex).__name__)
        _avisar_fallo(r, (
            f"Take-profit parcial; quedan {qty} acc sin stop. {motivo} y la venta a mercado "
            "también fue rechazada. Revisar en Alpaca ya; el próximo tick reintenta."
        ), ahora)
        return FALLO
    oid = resp.get("id") if isinstance(resp, dict) else None
    if not oid:
        _avisar_fallo(r, (
            f"Take-profit parcial; quedan {qty} acc sin stop. La venta a mercado no devolvió id. "
            "Revisar en Alpaca."
        ), ahora)
        return FALLO
    _anotar(r.order_id or "", {
        "ticker": r.ticker, "order_id": str(oid), "tipo": "market",
        "cantidad": qty, "stop": r.stop, "ts": ahora.astimezone(UTC).isoformat(),
    })
    log.warning("%s: remanente de %s acc vendido a mercado (%s)", r.ticker, qty, motivo)
    notify.enviar(notify.formatear_reprotegida(
        ticker=r.ticker, signal_id=r.creado_en, cantidad=_num(qty),
        accion="Remanente vendido a mercado",
        detalle=f"El take-profit se llenó en parte y Alpaca canceló el stop. {motivo}.",
    ))
    return VENDIDO_MERCADO


def _reproteger_uno(client: AlpacaPaperClient, r: estado.RevisionIA, ahora: datetime) -> str:
    from momentum_paper_trader import cierre
    from momentum_paper_trader.reconciliacion import cobertura

    ticker = r.ticker
    try:
        ordenes = client.ordenes_de_simbolos([ticker])
    except Exception as ex:
        log.warning("%s: reprotección: no se pudieron leer las órdenes (%s)", ticker, type(ex).__name__)
        ordenes = None
    cubre = cobertura(ordenes if isinstance(ordenes, list) else None)
    if cubre is None:
        _avisar_fallo(r, (
            "Take-profit parcial y la pata stop quedó cancelada. No se pudieron leer las órdenes "
            "del símbolo para reponer el stop. El próximo tick reintenta."
        ), ahora)
        return FALLO
    stops, mercados = cubre
    if ticker in stops or ticker in mercados:
        return YA_PROTEGIDA

    estado_pos, _qty, _precio = _leer_posicion(client, ticker)
    if estado_pos == "ausente":
        return SIN_POSICION
    if estado_pos != "ok":
        _avisar_fallo(r, (
            "Take-profit parcial y la pata stop quedó cancelada. No se pudo leer la posición; "
            "no se colocó nada. El próximo tick reintenta."
        ), ahora)
        return FALLO

    # El resto del take-profit retiene la cantidad: hay que soltarlo y
    # esperar el terminal (un 204 no es `canceled`).
    patas = cierre._patas_de_salida(ticker, ordenes)
    if patas:
        try:
            client.cancelar_ordenes_de(ticker, ordenes)
        except Exception as ex:
            log.warning("%s: reprotección: no se pudo cancelar el take-profit (%s)", ticker, type(ex).__name__)
        espera = cierre._esperar_patas(client, ticker, patas)
        if espera not in ("listas", "filled"):
            _avisar_fallo(r, (
                "Take-profit parcial y la pata stop quedó cancelada. El resto del take-profit no "
                "llegó a un estado terminal al cancelarlo, así que el stop no se pudo colocar "
                "todavía. El próximo tick reintenta."
            ), ahora)
            return FALLO

    estado_pos, qty, precio = _leer_posicion(client, ticker)
    if estado_pos == "ausente":
        log.info("%s: el resto del take-profit se llenó; no queda remanente", ticker)
        return SIN_POSICION
    qty_texto = _texto_qty(qty) if qty is not None else None
    if estado_pos != "ok" or qty_texto is None:
        _avisar_fallo(r, (
            "Take-profit parcial: se soltó el resto del objetivo pero la posición releída no trae "
            "una cantidad vendible. No se colocó el stop. El próximo tick reintenta."
        ), ahora)
        return FALLO

    stop = r.stop
    if not (isinstance(stop, (int, float)) and stop > 0):
        return _vender(client, r, qty_texto, "La revisión no tiene un stop original utilizable", ahora)
    if precio is not None and precio <= stop:
        return _vender(client, r, qty_texto, f"El precio ya está en o bajo el stop original ${stop:,.2f}", ahora)

    try:
        resp = client.colocar_stop_remanente(
            ticker, qty_texto, float(stop), _id_cliente("rps", ticker, r.order_id or ""),
        )
    except Exception as ex:
        log.warning("%s: reprotección: Alpaca no aceptó el stop (%s); se vende a mercado", ticker, type(ex).__name__)
        return _vender(client, r, qty_texto, "Alpaca rechazó el stop de reposición", ahora)
    oid = resp.get("id") if isinstance(resp, dict) else None
    if not oid:
        return _vender(client, r, qty_texto, "El stop de reposición no devolvió id", ahora)

    _anotar(r.order_id or "", {
        "ticker": ticker, "order_id": str(oid), "tipo": "stop",
        "cantidad": qty_texto, "stop": float(stop), "ts": ahora.astimezone(UTC).isoformat(),
    })
    log.warning("%s: remanente de %s acc re-protegido con stop en $%.2f", ticker, qty_texto, stop)
    notify.enviar(notify.formatear_reprotegida(
        ticker=ticker, signal_id=r.creado_en, cantidad=qty,
        accion="Stop repuesto al precio original", precio=float(stop),
        detalle="El take-profit se llenó en parte y Alpaca canceló el stop del bracket.",
    ))
    return STOP_REPUESTO


def reproteger(
    client: AlpacaPaperClient, cfg: PaperTraderConfig | None = None, ahora: datetime | None = None,
) -> dict[str, str]:
    """{ticker: desenlace} de los remanentes tratados en esta pasada.
    Nunca lanza: un fallo acá no puede tumbar el tick."""
    try:
        return _reproteger(client, cfg or PaperTraderConfig(), ahora or datetime.now(UTC))
    except Exception as ex:
        log.warning("reprotección no completada (%s)", type(ex).__name__)
        return {}


def _reproteger(client: AlpacaPaperClient, cfg: PaperTraderConfig, ahora: datetime) -> dict[str, str]:
    try:
        revisiones = estado.cargar()
    except Exception as ex:
        log.warning("reprotección: no se pudieron leer las revisiones (%s)", type(ex).__name__)
        return {}
    vivas = [
        r for r in revisiones
        if r.entro and r.order_id and r.resultado not in estado.RESULTADOS_TERMINALES
    ]
    if not vivas:
        return {}
    if _en_ventana_de_cierre(ahora, cfg):
        # El cierre diario ya decidió liquidar o aguantar con su stop.
        return {}
    salida: dict[str, str] = {}
    for r in vivas:
        try:
            datos = client.estado_orden(r.order_id)
        except Exception as ex:
            log.warning("%s: reprotección: no se pudo leer la orden (%s)", r.ticker, type(ex).__name__)
            continue
        if not remanente_sin_stop(datos):
            continue
        salida[r.ticker] = _reproteger_uno(client, r, ahora)
    return salida
