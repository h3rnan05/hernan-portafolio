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
