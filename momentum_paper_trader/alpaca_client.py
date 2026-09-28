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

# Una pata `held` no está muerta: es el stop del bracket esperando.
# Estos estados sí: no protegen y no hace falta volver a cancelarlos.
_ESTADOS_ORDEN_TERMINAL = frozenset({
    "filled", "canceled", "cancelled", "expired", "rejected",
    "replaced", "done_for_day", "suspended",
})


# Los únicos estados en los que una pata de venta todavía protege.
# `held` es el stop del bracket esperando a que el precio lo dispare;
# `new` / `accepted` / `pending_new` son una orden ya aceptada y viva.
# Cualquier otro valor (o un status ausente) no es evidencia de
# protección: el listado `status=all` mezcla historial muerto.
_ESTADOS_ORDEN_VIVA = frozenset({"held", "new", "accepted", "pending_new"})


def orden_ya_terminada(orden: dict) -> bool:
    """True si el status dice que la orden ya no trabaja. Sin status no
    se inventa un terminal: el listado `open` a veces no lo trae en los
    dobles de prueba, y un ausente no es evidencia de que murió."""
    status = orden.get("status")
    if status is None:
        return False
    return str(status).lower() in _ESTADOS_ORDEN_TERMINAL


def orden_sigue_viva(orden: dict) -> bool:
    """True solo con un status que Alpaca usa para una orden que todavía
    trabaja. Un campo ausente no cuenta: no es lo mismo que `held`."""
    status = orden.get("status")
    if not isinstance(status, str) or not status.strip():
        return False
    return status.strip().lower() in _ESTADOS_ORDEN_VIVA


def ordenes_con_patas(ordenes: list) -> list[dict]:
    """La fila de arriba y, un nivel más, sus `legs`.

    Alpaca no anida más allá de un nivel. La pata hereda el símbolo del
    padre si ella no lo trae: sin eso un stop `held` sin `symbol` se
    pierde. No muta el dict original (le pone el símbolo resuelto en
    una copia, clave `_symbol`)."""
    salida: list[dict] = []
    if not isinstance(ordenes, list):
        return salida
    for orden in ordenes:
        if not isinstance(orden, dict):
            continue
        padre = orden.get("symbol")
        fila = dict(orden)
        fila["_symbol"] = padre
        salida.append(fila)
        legs = orden.get("legs")
        if not isinstance(legs, list):
            continue
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            pata = dict(leg)
            pata["_symbol"] = leg.get("symbol") or padre
            salida.append(pata)
    return salida

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
        """Órdenes todavía vivas (`GET /v2/orders?status=open&nested=true`).

        Complementa `posiciones()`: una orden límite de entrada que aún
        no se llenó no es una posición, pero SÍ compromete el ticker.

        No sirve para ver el stop de un bracket ya lleno. Tras el fill
        del padre, `status=open` devuelve solo el take-profit (`new`) y
        ese objeto trae `legs: null`. El stop `held` cuelga del padre,
        que ya no está en `open`. Para eso está `ordenes_de_simbolos`."""
        r = requests.get(
            f"{_BASE_URL}/orders",
            params={"status": "open", "limit": 100, "nested": "true"},
            headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def ordenes_de_simbolos(self, simbolos: list[str]) -> list[dict]:
        """Órdenes de esos símbolos, padre filled incluido
        (`GET /v2/orders?status=all&nested=true&symbols=...`).

        El 2026-09-28 MNST tenía el take-profit `bb5baab2` (limit 42.36,
        `new`) como única fila de `status=open`, con `legs` vacío. El
        stop `f2d920f1` (stop 41.62, `held`) solo aparecía anidado bajo
        la compra ya `filled` `265e093f`, o como fila propia en
        `status=all`. Sin este listado la reconciliación decía que no
        había stop y el cierre no podía cancelar esa pata por id.

        `direction=desc` y `limit=500` (el tope de Alpaca) se quedan con
        lo más reciente. Una orden más vieja que esas 500, en esos
        símbolos, no entra: en esta cuenta no se acerca, y preferimos
        no paginar a ciegas. Sin símbolos no se llama: `status=all` sin
        filtro vaciaría el historial de la cuenta en el chequeo."""
        limpios: list[str] = []
        vistos: set[str] = set()
        for simbolo in simbolos:
            if not isinstance(simbolo, str):
                continue
            texto = simbolo.strip()
            if not texto or texto in vistos:
                continue
            vistos.add(texto)
            limpios.append(texto)
        if not limpios:
            return []
        r = requests.get(
            f"{_BASE_URL}/orders",
            params={
                "status": "all",
                "nested": "true",
                "symbols": ",".join(limpios),
                "limit": 500,
                "direction": "desc",
            },
            headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def reloj_mercado(self) -> dict:
        """Estado del mercado según Alpaca (`GET /v2/clock`) -- solo
        lectura. Devuelve `is_open`, `next_open` y `next_close`.

        Es la ÚNICA fuente de verdad de calendario que este proyecto
        tiene: el resto del sistema deduce el horario de constantes
        hardcodeadas en horario de verano (ver `momentum_hunter.factors.
        intradia.HORA_CIERRE_UTC`), que en invierno se corren una hora, y
        no sabe nada de feriados ni de medias sesiones. El executor lo
        usa para no colocar órdenes de entrada con el mercado cerrado
        (ver `executor._mercado_cerrado`)."""
        r = requests.get(f"{_BASE_URL}/clock", headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def activo(self, ticker: str) -> dict:
        """Ficha del instrumento (`GET /v2/assets/{symbol}`) -- solo
        lectura. Interesa `tradable`: Alpaca no expone un campo de
        "halted" intradía, así que esto es lo más cerca que se puede
        estar de "¿se puede operar este símbolo ahora?" sin una fuente
        externa de halts. Ver `executor._activo_no_operable`."""
        r = requests.get(
            f"{_BASE_URL}/assets/{ticker}", headers=self._headers, timeout=self._timeout)
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

    def cerrar_posicion(self, ticker: str, *, cancel_orders: bool = False) -> dict:
        """Liquida UNA posición a mercado (`DELETE /v2/positions/{symbol}`).

        Existe además de `cerrar_todas_las_posiciones` porque el cierre
        del día se decide posición por posición (ver `cierre.py`): la IA
        puede querer cerrar una y aguantar otra.

        `cancel_orders=True` le pide a Alpaca que cancele las órdenes
        vivas del símbolo ANTES de armar la venta. Sin eso el DELETE
        responde 403: las patas del bracket ya reservan toda la cantidad
        y la venta ve disponible 0 (CTAS, TWST, NBIS y DLB, 2026-09-25).
        El cierre diario además cancela de forma explícita y reintenta;
        este parámetro es la red del propio endpoint por si alguna pata
        sigue viva. Un cuerpo que no sea la orden se devuelve vacío: no
        se inventa un id."""
        params = {"cancel_orders": "true"} if cancel_orders else None
        r = requests.delete(
            f"{_BASE_URL}/positions/{ticker}", params=params,
            headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        datos = r.json()
        return datos if isinstance(datos, dict) else {}

    def vender_a_mercado(self, ticker: str, cantidad: str) -> dict:
        """Venta a mercado de la cantidad que el broker reporta.

        Solo para SALIR, y solo cuando `DELETE /v2/positions/{symbol}`
        fue rechazado (ver `cierre.py`). Nunca para entrar: el lado va
        fijo en `sell`. `cantidad` es el `qty` de la posición, como
        string, sin recortar ni recalcular -- una cantidad ausente o no
        positiva no se convierte en cero, se rechaza acá."""
        try:
            n = float(cantidad)
        except (TypeError, ValueError):
            raise ValueError(f"{ticker}: cantidad de venta ilegible") from None
        if not n > 0:
            raise ValueError(f"{ticker}: cantidad de venta debe ser > 0, se recibió {cantidad!r}")
        payload = {
            "symbol": ticker,
            "qty": str(cantidad),
            "side": "sell",
            "type": "market",
            "time_in_force": "day",
            "extended_hours": False,
        }
        r = requests.post(
            f"{_BASE_URL}/orders", json=payload, headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        datos = r.json()
        return datos if isinstance(datos, dict) else {}

    def cancelar_ordenes_de(self, ticker: str, ordenes_abiertas: list[dict]) -> int:
        """Cancela las órdenes vivas de un ticker, patas `held` incluidas.

        Necesario antes de reemplazar las salidas: las patas del bracket
        siguen vivas y colocar otra venta encima rebota por cantidad
        insuficiente. Tras el fill, el stop `held` no es una fila de
        `status=open`: va en `legs` del padre `filled` (o como fila de
        `status=all`). El listado que hay que pasar es el de
        `ordenes_de_simbolos`. El padre ya `filled` no se cancela; sus
        patas vivas sí, por id, porque cancelar solo el take-profit de
        la lista `open` deja el stop fuera de este bucle.

        El cierre además manda `cancel_orders=true` en el DELETE de la
        posición. Ese flag es el que cancela en el servidor lo que este
        listado no vio -- incluidas las patas `held` que retienen la
        cantidad y provocan el 403. Las dos cosas van juntas: el DELETE
        no se queda esperando a que este bucle haya visto cada pata.

        Una pata ya terminal no se toca. Un 404/422 (la hermana ya cayó
        al cancelar la otra) no es un fallo. Cualquier otro error no
        aborta el resto, y el log lleva el tipo, no el texto de la
        excepción (puede traer la URL)."""
        canceladas = 0
        vistos: set[str] = set()
        for o in ordenes_con_patas(ordenes_abiertas):
            oid = o.get("id")
            simbolo = o.get("_symbol")
            if simbolo != ticker or not oid or oid in vistos:
                continue
            if orden_ya_terminada(o):
                continue
            vistos.add(str(oid))
            try:
                r = requests.delete(
                    f"{_BASE_URL}/orders/{oid}", headers=self._headers, timeout=self._timeout)
                if r.status_code in (404, 422):
                    # La pata hermana del OCO ya no está: cancelar una
                    # cancela la otra. No es que el id estuviera mal.
                    continue
                r.raise_for_status()
                canceladas += 1
            except Exception as ex:
                log.warning(
                    "%s: no se pudo cancelar la orden %s (%s)",
                    ticker, oid, type(ex).__name__,
                )
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

    @staticmethod
    def _precio(v: float) -> str:
        """Alpaca exige como máximo 2 decimales para precios >= $1 y
        admite 4 para sub-dólar. Formatear todo con .2f rompía el
        segundo caso: un stop calculado en $0,7512 se enviaba como
        "0.75" -- un precio DISTINTO del que el pipeline decidió, y en
        acciones baratas esa diferencia es grande en porcentaje. Este
        bot opera desde $0,75 (ver `momentum_hunter.config.precio_min`),
        así que el caso es real, no teórico."""
        return f"{v:.4f}" if v < 1 else f"{v:.2f}"

    def colocar_orden_bracket(
        self, ticker: str, cantidad: int, entrada: float, stop: float, objetivo: float,
        client_order_id: str | None = None,
    ) -> OrdenBracket:
        """Compra `cantidad` acciones de `ticker` con una orden LIMIT en
        `entrada` (nunca persigue el precio de mercado -- el mismo
        principio de "no perseguir" que ya aplica todo momentum_hunter),
        más las dos patas de salida (`take_profit`/`stop_loss`) en el
        mismo pedido -- Alpaca las maneja como OCO automáticamente, sin
        que este sistema tenga que vigilar la posición después."""
        # Validación previa: Alpaca rechaza un bracket cuyos niveles no
        # estén ordenados, y ese rechazo llega como un error HTTP opaco
        # que el executor solo puede loguear. Comprobarlo acá convierte
        # un fallo remoto confuso en uno local y explícito.
        if not (objetivo > entrada > stop > 0):
            raise ValueError(
                f"{ticker}: niveles inconsistentes para un bracket de compra "
                f"(objetivo {objetivo} > entrada {entrada} > stop {stop} > 0)")
        if cantidad < 1:
            raise ValueError(f"{ticker}: cantidad debe ser >= 1, se recibió {cantidad}")

        payload = {
            "symbol": ticker,
            "qty": str(cantidad),
            "side": "buy",
            "type": "limit",
            "limit_price": self._precio(entrada),
            "time_in_force": "day",
            "order_class": "bracket",
            # Explícito aunque sea el default: un bracket NO puede
            # operar en extended hours, y dejarlo implícito invita a que
            # alguien lo ponga en `true` alguna vez y se lleve un rechazo
            # incomprensible.
            "extended_hours": False,
            "take_profit": {"limit_price": self._precio(objetivo)},
            "stop_loss": {"stop_price": self._precio(stop)},
        }
        # IDEMPOTENCIA (2026-08-25). El ejecutor corre dentro de un
        # workflow que puede morir y reintentarse, y un `POST` que sufre
        # un timeout de red puede haber llegado igual. Sin esto, el
        # reintento coloca una SEGUNDA orden sobre el mismo ticker --
        # justo lo que los guardarraíles de cartera existen para
        # impedir, por un camino que no ven. Con un id derivado de la
        # señal, Alpaca rechaza el duplicado en vez de ejecutarlo.
        if client_order_id:
            payload["client_order_id"] = client_order_id[:128]
        r = requests.post(
            f"{_BASE_URL}/orders", json=payload, headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        data = r.json()
        return OrdenBracket(
            order_id=data["id"], ticker=ticker, cantidad=cantidad,
            precio_entrada=entrada, stop=stop, objetivo=objetivo,
            estado=data.get("status", "desconocido"),
        )
