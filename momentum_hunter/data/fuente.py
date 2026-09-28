"""Qué fuente de precios usa el hunter en esta corrida.

`MOMENTUM_DATA_PROVIDER=yahoo` (el default, también si la variable no
está) deja el comportamiento de hoy. `alpaca` pide el feed de
`ALPACA_DATA_FEED` (`sip` por omisión, o `iex`) y, si un lote o el ciclo
entero falla, completa esos símbolos con Yahoo. El merge no enciende
nada: hay que exportar la variable en el VPS y reiniciar el vigía.

La telemetría (`informe_datos`) dice qué fuente contestó de verdad, no
cuál estaba configurada. `metadata` (float, ETF, nombre) no entra en
esa cuenta: el feed de precios no la tiene y seguir pidiéndola a Yahoo
no es un respaldo, es otro dato.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from momentum_hunter.data.alpaca_datos import AlpacaProvider, ErrorDatosAlpaca
from momentum_hunter.data.provider import DataProvider, YahooProvider
from momentum_hunter.models import Barras, BarraIntradia, Metadata

log = logging.getLogger("momentum_hunter.data.fuente")

ENV_PROVEEDOR = "MOMENTUM_DATA_PROVIDER"
ENV_FEED = "ALPACA_DATA_FEED"


@dataclass(frozen=True)
class InformeDatos:
    """Foto de los pedidos de PRECIO de este proceso (un ciclo).
    `fuente` es None si todavía no se pidió ninguna barra: no se
    afirma que se usó un feed que no se llamó. `latencia_ms` igual.
    `fallbacks` sí es un conteo: 0 significa que no hubo que ir al
    respaldo, no un dato que faltaba."""

    configurada: str
    fuente: str | None
    feed: str | None
    fallbacks: int
    latencia_ms: float | None


def informe_de(provider) -> dict | None:
    """Dict estable para la telemetría, o None si este objeto no mide
    (los dobles de prueba)."""
    fn = getattr(provider, "informe_datos", None)
    if not callable(fn):
        return None
    datos = fn()
    if not isinstance(datos, InformeDatos):
        return None
    return {
        "configurada": datos.configurada,
        "fuente": datos.fuente,
        "feed": datos.feed,
        "fallbacks": datos.fallbacks,
        "latencia_ms": datos.latencia_ms,
    }


class _Medido(DataProvider):
    """Yahoo (o un doble) con reloj. No cambia las barras."""

    def __init__(self, interno: DataProvider, configurada: str) -> None:
        self._interno = interno
        self._configurada = configurada
        self._llamadas = 0
        self._latencia_ms: float | None = None

    def _correr(self, fn, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._llamadas += 1
            dt = (time.perf_counter() - t0) * 1000.0
            self._latencia_ms = (self._latencia_ms or 0.0) + dt

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        return self._correr(self._interno.barras, tickers, dias)

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        return self._correr(self._interno.barras_intradia, tickers, intervalo, periodo)

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        return self._interno.metadata(tickers)

    def informe_datos(self) -> InformeDatos:
        return InformeDatos(
            configurada=self._configurada,
            fuente=self._configurada if self._llamadas else None,
            feed=None,
            fallbacks=0,
            latencia_ms=round(self._latencia_ms, 1) if self._latencia_ms is not None else None,
        )


class ProveedorConRespaldo(DataProvider):
    """Primero el feed; Yahoo solo para los símbolos (o el ciclo) que
    no respondieron. Un símbolo ausente en una respuesta 200 no es un
    fallo: el feed dijo que no hay velas, y inventarle las de otra
    fuente mezclaría dos cintas en el mismo cálculo."""

    def __init__(self, primario: AlpacaProvider, respaldo: DataProvider, feed: str) -> None:
        self._primario = primario
        self._respaldo = respaldo
        self._feed = feed
        self._llamadas = 0
        self._uso_primario = False
        self._uso_respaldo = False
        self._fallbacks = 0
        self._latencia_ms: float | None = None

    def _marcar_tiempo(self, t0: float) -> None:
        self._llamadas += 1
        self._latencia_ms = (self._latencia_ms or 0.0) + (time.perf_counter() - t0) * 1000.0

    def _completar(self, tickers: list[str], pedir_primario, pedir_respaldo) -> dict:
        t0 = time.perf_counter()
        try:
            try:
                out = pedir_primario(tickers)
                self._uso_primario = True
            except ErrorDatosAlpaca as ex:
                # Ciclo caído (auth, red, 429 agotado, paginación): no se
                # queda una serie a medias. Todo el pedido va a Yahoo.
                log.warning(
                    "datos: el ciclo del feed falló (%s); este pedido entero va al respaldo",
                    ex.codigo,
                )
                self._fallbacks += len([t for t in tickers if isinstance(t, str) and t.strip()])
                self._uso_respaldo = True
                return pedir_respaldo(tickers)
            fallidos = list(dict.fromkeys(getattr(self._primario, "fallidos", [])))
            if fallidos:
                # Se cuentan aunque el respaldo tampoco tenga vela: el
                # intento existió y es lo que hay que poder ver después.
                log.warning(
                    "datos: %d símbolo(s) del feed no respondieron; se piden al respaldo (%s)",
                    len(fallidos), ", ".join(str(t) for t in fallidos[:8]),
                )
                self._fallbacks += len(fallidos)
                self._uso_respaldo = True
                extra = pedir_respaldo(fallidos)
                if isinstance(extra, dict) and isinstance(out, dict):
                    out.update(extra)
            return out
        finally:
            self._marcar_tiempo(t0)

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        return self._completar(
            tickers,
            lambda ts: self._primario.barras(ts, dias),
            lambda ts: self._respaldo.barras(ts, dias),
        )

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        return self._completar(
            tickers,
            lambda ts: self._primario.barras_intradia(ts, intervalo, periodo),
            lambda ts: self._respaldo.barras_intradia(ts, intervalo, periodo),
        )

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        return self._respaldo.metadata(tickers)

    def informe_datos(self) -> InformeDatos:
        if not self._llamadas:
            fuente = None
        elif self._uso_primario and self._uso_respaldo:
            fuente = "mixto"
        elif self._uso_primario:
            fuente = "alpaca"
        else:
            fuente = "yahoo"
        return InformeDatos(
            configurada="alpaca",
            fuente=fuente,
            feed=self._feed if self._uso_primario else None,
            fallbacks=self._fallbacks,
            latencia_ms=round(self._latencia_ms, 1) if self._latencia_ms is not None else None,
        )


def proveedor_configurado(construir_yahoo=None) -> DataProvider:
    """Lee el entorno una vez por proceso. `construir_yahoo` existe para
    que el hunter pueda inyectar su `YahooProvider` (los tests lo
    parchean ahí) sin que este módulo importe el nombre al revés."""
    construir = construir_yahoo or YahooProvider
    nombre = os.environ.get(ENV_PROVEEDOR, "yahoo").strip().lower()
    if nombre in ("", "yahoo"):
        return _Medido(construir(), configurada="yahoo")
    if nombre != "alpaca":
        log.warning(
            "MOMENTUM_DATA_PROVIDER=%s no es yahoo ni alpaca; se queda en yahoo", nombre,
        )
        return _Medido(construir(), configurada="yahoo")
    feed = os.environ.get(ENV_FEED, "sip").strip().lower()
    if feed not in ("sip", "iex"):
        log.warning("ALPACA_DATA_FEED=%s no es sip ni iex; se queda en yahoo", feed)
        return _Medido(construir(), configurada="yahoo")
    log.info("datos: feed de precios alpaca/%s, con respaldo yahoo", feed)
    return ProveedorConRespaldo(AlpacaProvider(feed=feed), construir(), feed=feed)
