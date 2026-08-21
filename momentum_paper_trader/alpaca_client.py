"""Cliente delgado sobre la API REST de Alpaca -- SOLO paper trading.

El endpoint está hardcodeado acá abajo (`_BASE_URL`) -- no es un
parámetro configurable por variable de entorno ni por argumento, a
propósito: ningún error de configuración puede apuntar esto a una
cuenta real. Cambiarlo requeriría editar este archivo a mano (ver
README del módulo, sección "qué requeriría ir a real")."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger("momentum_paper_trader.alpaca_client")

# NUNCA "https://api.alpaca.markets" (esa es la cuenta real) -- ver
# docstring del módulo.
_BASE_URL = "https://paper-api.alpaca.markets/v2"


@dataclass(frozen=True)
class OrdenBracket:
    """Solo lo que el resto del sistema necesita para registrar y avisar
    -- nunca el payload crudo completo que devuelve Alpaca."""
    order_id: str
    ticker: str
    cantidad: int
    precio_entrada: float
    stop: float
    objetivo: float
    estado: str   # el "status" que devuelve Alpaca (ej. "accepted", "pending_new")


class AlpacaPaperClient:
    """`api_key`/`api_secret` los lee `run.py` de variables de entorno --
    esta clase nunca los hardcodea ni los persiste en ningún archivo."""

    def __init__(self, api_key: str, api_secret: str, timeout: float = 15.0) -> None:
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }
        self._timeout = timeout

    def info_cuenta(self) -> dict:
        """Consulta de solo lectura (`GET /v2/account`) -- nunca coloca
        ni modifica nada, solo confirma que las credenciales conectan de
        verdad con el entorno paper. Pensada para verificar la conexión
        sin depender de que exista una señal TRIGGERED real (ver
        `run.py --verificar-conexion`)."""
        r = requests.get(f"{_BASE_URL}/account", headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def posiciones(self) -> list[dict]:
        """Posiciones abiertas de la cuenta paper (`GET /v2/positions`)
        -- solo lectura. El executor las usa como guardarraíl determinista
        (no duplicar ticker, no exceder el máximo de posiciones) y como
        contexto para la IA ("con qué está cargada la cuenta ahora")."""
        r = requests.get(f"{_BASE_URL}/positions", headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def ordenes_abiertas(self) -> list[dict]:
        """Órdenes todavía vivas (`GET /v2/orders?status=open`) -- solo
        lectura. Complementa `posiciones()`: una orden límite de entrada
        que aún no se llenó no es una posición, pero SÍ compromete el
        ticker (colocar otra sería duplicar la apuesta)."""
        r = requests.get(
            f"{_BASE_URL}/orders", params={"status": "open", "limit": 100},
            headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def estado_orden(self, order_id: str) -> dict:
        """Estado actual de una orden y sus patas OCO (`GET /v2/orders/
        {id}?nested=true`) -- solo lectura. `nested=true` trae las dos
        patas del bracket (`legs`), que es como `seguimiento.py` sabe si
        la salida fue por objetivo o por stop y a qué precio real."""
        r = requests.get(
            f"{_BASE_URL}/orders/{order_id}", params={"nested": "true"},
            headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def cerrar_todas_las_posiciones(self) -> list[dict]:
        """Liquida TODAS las posiciones abiertas a mercado y cancela las
        órdenes vivas (`DELETE /v2/positions?cancel_orders=true`).

        `cancel_orders=true` importa: las dos patas del bracket
        (take-profit y stop-loss) siguen vivas mientras haya posición, y
        cerrar sin cancelarlas primero puede rebotar por cantidad
        insuficiente -- Alpaca hace las dos cosas en el orden correcto
        con este parámetro.

        A MERCADO, no limitada: el objetivo es no quedarse con una
        posición desprotegida de un día para otro (ver `cierre.py`), y
        una orden limitada podría no llenarse justo cuando lo que se
        necesita es salir sí o sí. Es la única parte del sistema que usa
        órdenes a mercado, y solo para SALIR -- nunca para entrar."""
        r = requests.delete(
            f"{_BASE_URL}/positions", params={"cancel_orders": "true"},
            headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        datos = r.json()
        return datos if isinstance(datos, list) else []

    def cerrar_posicion(self, ticker: str) -> dict:
        """Liquida UNA posición a mercado (`DELETE /v2/positions/{symbol}`).

        Existe además de `cerrar_todas_las_posiciones` porque el cierre
        del día se decide posición por posición (ver `cierre.py`): la IA
        puede querer cerrar una y aguantar otra."""
        r = requests.delete(
            f"{_BASE_URL}/positions/{ticker}", headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def cancelar_ordenes_de(self, ticker: str, ordenes_abiertas: list[dict]) -> int:
        """Cancela las órdenes vivas de un ticker. Devuelve cuántas
        canceló. Necesario antes de reemplazar las salidas: las patas del
        bracket siguen vivas y colocar otra orden de venta encima
        rebotaría por cantidad insuficiente.

        Un fallo cancelando una orden concreta no aborta el resto -- se
        cuenta solo lo que de verdad se canceló."""
        canceladas = 0
        for o in ordenes_abiertas:
            if o.get("symbol") != ticker or not o.get("id"):
                continue
            try:
                r = requests.delete(
                    f"{_BASE_URL}/orders/{o['id']}", headers=self._headers, timeout=self._timeout)
                r.raise_for_status()
                canceladas += 1
            except Exception as ex:
                log.warning("%s: no se pudo cancelar la orden %s: %s", ticker, o["id"], ex)
        return canceladas

    def colocar_stop_protector(self, ticker: str, cantidad: int, stop: float) -> str:
        """Stop de venta que SOBREVIVE a la noche (`time_in_force: "gtc"`).

        Las patas del bracket son órdenes "del día" y mueren al cerrar el
        mercado. Cuando la IA decide aguantar una posición hasta mañana
        (ver `cierre.py`), aguantar sin stop sería la peor de las dos
        opciones: este stop es la condición para poder hacerlo.

        AVISO HONESTO, documentado también en el README: un stop NO
        protege contra un hueco de apertura. Si la acción cierra en $50
        con stop en $48 y abre en $40, la venta se ejecuta cerca de $40,
        no de $48. Reduce el riesgo nocturno, no lo elimina."""
        payload = {
            "symbol": ticker,
            "qty": str(cantidad),
            "side": "sell",
            "type": "stop",
            "stop_price": f"{stop:.2f}",
            "time_in_force": "gtc",
        }
        r = requests.post(
            f"{_BASE_URL}/orders", json=payload, headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json().get("id", "")

    def colocar_orden_bracket(
        self, ticker: str, cantidad: int, entrada: float, stop: float, objetivo: float,
    ) -> OrdenBracket:
        """Compra `cantidad` acciones de `ticker` con una orden LIMIT en
        `entrada` (nunca persigue el precio de mercado -- el mismo
        principio de "no perseguir" que ya aplica todo momentum_hunter),
        más las dos patas de salida (`take_profit`/`stop_loss`) en el
        mismo pedido -- Alpaca las maneja como OCO automáticamente, sin
        que este sistema tenga que vigilar la posición después."""
        payload = {
            "symbol": ticker,
            "qty": str(cantidad),
            "side": "buy",
            "type": "limit",
            "limit_price": f"{entrada:.2f}",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{objetivo:.2f}"},
            "stop_loss": {"stop_price": f"{stop:.2f}"},
        }
        r = requests.post(
            f"{_BASE_URL}/orders", json=payload, headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        data = r.json()
        return OrdenBracket(
            order_id=data["id"], ticker=ticker, cantidad=cantidad,
            precio_entrada=entrada, stop=stop, objetivo=objetivo,
            estado=data.get("status", "desconocido"),
        )
