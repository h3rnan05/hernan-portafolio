"""Qué fuente de precios usa el hunter en esta corrida.

`MOMENTUM_DATA_PROVIDER=alpaca` (el default desde el 2026-10-01, también
si la variable no está o está vacía) pide velas diarias y de minuto al
feed SIP de `data.alpaca.markets`, con `feed=sip` explícito en cada
llamada. `yahoo` vuelve a Yahoo sin tocar código.

SIN RESPALDO SILENCIOSO (2026-10-01, pedido del dueño). Antes, si el
feed fallaba, el pedido se completaba con Yahoo y la corrida seguía con
una cinta mezclada. Ahora, si faltan las llaves o el ciclo del feed cae
(auth, red, 429 agotado tras los reintentos, paginación), se lanza
`FuentePreciosCaida` y el hunter termina la corrida SIN entradas nuevas,
dejándolo en el log y en la telemetría. Un valor desconocido en
`MOMENTUM_DATA_PROVIDER` tampoco cae a Yahoo: es la misma falla.

Un lote suelto que no respondió dentro de un ciclo que sí funciona
(`fallidos`, p. ej. un 400 de un símbolo) deja a esos símbolos SIN
datos: no se evalúan, no se rellenan con otra fuente ni con ceros, y
el aviso nombra cuántos y el código.

`ALPACA_DATA_FEED` ya no cambia el feed del hunter: siempre es `sip`
(el plan de pago; IEX es ~2,5 % del volumen). Si dice otra cosa, se
avisa y se ignora.

RESPALDO IEX (2026-10-06, aprobado por el dueño). La única excepción a
"sin respaldo": si SIP responde 403 de suscripción, `AlpacaProvider`
repite el mismo pedido con `feed=iex` y el resto del ciclo sigue por IEX
(ver `alpaca_datos`). La telemetría lo dice: `feed_usado` (`sip`, `iex`
o `sip+iex`) y `fallback_iex`. Un 401 u otro error sigue cortando la
corrida. `MOMENTUM_FALLBACK_IEX=0` lo apaga.

La telemetría (`informe_datos`) dice qué fuente contestó de verdad, no
cuál estaba configurada. `metadata` (float, ETF, nombre) no es precio:
sigue en Yahoo y no entra en esa cuenta. Noticias, aparte.

`MOMENTUM_SIP_STREAM` (default `sombra`) no cambia este camino. Solo
`primario` intenta servir el minuto desde el almacén del stream; si no
cubre, sigue el REST de abajo. Ver `data/sip_stream.py`.
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
# Solo la metadata (nombre, bolsa, market cap). `yahoo` (default) deja
# todo como hoy; `finnhub` la pide a Finnhub y las barras siguen saliendo
# del proveedor de precios. Antes de poner `finnhub` en el VPS corre la
# sombra de cinco sesiones (`momentum_hunter/sombra_metadata.py`).
ENV_METADATA = "MOMENTUM_METADATA_PROVIDER"


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
    # Qué feed contestó de verdad (`sip`, `iex`, `sip+iex`) y si hubo
    # respaldo IEX por un 403 de plan. None/False si no se pidió nada.
    feed_usado: str | None = None
    fallback_iex: bool = False


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
        "feed_usado": datos.feed_usado,
        "fallback_iex": bool(datos.fallback_iex),
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


class FuentePreciosCaida(Exception):
    """El feed de precios no puede atender este ciclo (o está mal
    configurado). El hunter corta la corrida sin entradas nuevas. `codigo`
    es la etiqueta corta de `ErrorDatosAlpaca` (o `configuracion`), nunca
    el cuerpo de la respuesta ni una URL."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


def etiqueta_feeds(feeds) -> str | None:
    """`sip`, `iex`, `sip+iex` o None (no contestó ninguno)."""
    usados = {f for f in (feeds or ()) if isinstance(f, str) and f}
    if not usados:
        return None
    return "+".join(sorted(usados, key=lambda f: (f != "sip", f)))


class ProveedorAlpaca(DataProvider):
    """Precios del feed SIP; IEX solo si SIP da 403 de plan (lo resuelve
    `AlpacaProvider`). Sin Yahoo: un ciclo caído lanza
    `FuentePreciosCaida`. Un símbolo ausente en una respuesta 200 no es
    un fallo: el feed dijo que no hay velas. `metadata` (no es precio)
    sale de `metadata_de`, que es Yahoo."""

    def __init__(self, primario: AlpacaProvider, metadata_de, feed: str = "sip") -> None:
        self._primario = primario
        self._metadata_de = metadata_de
        self._meta = None
        self._feed = feed
        self._llamadas = 0
        self._uso_primario = False
        # Minuto servido por el almacén del stream SIP (no pasa por REST).
        self._feeds_stream: set[str] = set()
        self.sin_respuesta = 0
        self._latencia_ms: float | None = None

    def _marcar_tiempo(self, t0: float) -> None:
        self._llamadas += 1
        self._latencia_ms = (self._latencia_ms or 0.0) + (time.perf_counter() - t0) * 1000.0

    def _pedir(self, tickers: list[str], pedir) -> dict:
        t0 = time.perf_counter()
        try:
            try:
                out = pedir(tickers)
            except ErrorDatosAlpaca as ex:
                log.error(
                    "datos: el feed de Alpaca no respondió (%s; feeds probados: %s); sin "
                    "respaldo, la corrida termina sin entradas nuevas", ex.codigo,
                    "sip+iex" if self._fallback_iex() else "sip",
                )
                raise FuentePreciosCaida(ex.codigo) from None
            self._uso_primario = True
            fallidos = list(dict.fromkeys(getattr(self._primario, "fallidos", []) or []))
            if fallidos:
                codigo = getattr(self._primario, "ultimo_codigo", None)
                if not isinstance(codigo, str) or not codigo:
                    codigo = "sin_codigo"
                log.warning(
                    "datos: %d símbolo(s) sin respuesta del feed (%s); quedan sin datos y no "
                    "se evalúan (%s)", len(fallidos), codigo, ", ".join(str(t) for t in fallidos[:8]),
                )
                self.sin_respuesta += len(fallidos)
                for t in fallidos:
                    if isinstance(out, dict):
                        out.pop(t, None)
            return out
        finally:
            self._marcar_tiempo(t0)

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        return self._pedir(tickers, lambda ts: self._primario.barras(ts, dias))

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        # `primario` solo sustituye el minuto SIP, y solo si el almacén
        # cubre el pedido entero. Sombra (el default) ni entra. Un None
        # de `barras_si_cubren` no es "sin velas": es "seguí por REST".
        from momentum_hunter.data.sip_stream import barras_si_cubren
        t0 = time.perf_counter()
        try:
            servidas = barras_si_cubren(tickers, intervalo, periodo)
        except Exception as ex:
            log.warning("stream SIP ilegible (%s); este pedido de minuto va al REST", type(ex).__name__)
            servidas = None
        if servidas is not None:
            self._uso_primario = True
            self._feeds_stream.add("sip")
            self._marcar_tiempo(t0)
            return servidas
        return self._pedir(tickers, lambda ts: self._primario.barras_intradia(ts, intervalo, periodo))

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        if self._meta is None:
            self._meta = self._metadata_de()
        return self._meta.metadata(tickers)

    def _feeds_usados(self) -> set[str]:
        usados = getattr(self._primario, "feeds_usados", None)
        out = set(usados) if isinstance(usados, (set, frozenset, list, tuple)) else set()
        return out | self._feeds_stream

    def _fallback_iex(self) -> bool:
        if "iex" in self._feeds_usados():
            return True
        try:
            from momentum_hunter.data.alpaca_datos import fallback_iex_en_proceso
            return self._feed == "sip" and fallback_iex_en_proceso()
        except ImportError:
            return False

    def informe_datos(self) -> InformeDatos:
        usados = self._feeds_usados()
        feed_usado = etiqueta_feeds(usados) if self._uso_primario else None
        if not self._uso_primario:
            feed = None
        elif feed_usado is None:
            feed = self._feed
        elif "iex" in usados:
            # Si hubo IEX, no se rotula como SIP.
            feed = "iex"
        else:
            feed = feed_usado
        return InformeDatos(
            configurada="alpaca",
            fuente="alpaca" if self._uso_primario else None,
            feed=feed,
            fallbacks=0,
            latencia_ms=round(self._latencia_ms, 1) if self._latencia_ms is not None else None,
            feed_usado=feed_usado if feed_usado is not None else feed,
            fallback_iex="iex" in usados,
        )


class _ConfiguracionInvalida(DataProvider):
    """`MOMENTUM_DATA_PROVIDER` con un valor que no es alpaca ni yahoo.
    No se adivina qué quiso decir ni se cae a Yahoo: cualquier pedido
    de precio corta la corrida."""

    def __init__(self, valor: str) -> None:
        self._valor = valor

    def _fallar(self):
        log.error("%s=%s no es alpaca ni yahoo; la corrida termina sin entradas nuevas",
                  ENV_PROVEEDOR, self._valor)
        raise FuentePreciosCaida("configuracion")

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        self._fallar()

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        self._fallar()

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        self._fallar()

    def informe_datos(self) -> InformeDatos:
        return InformeDatos(configurada="invalida", fuente=None, feed=None, fallbacks=0, latencia_ms=None)


class ConMetadataAparte(DataProvider):
    """Barras del proveedor de precios; `metadata` de otro objeto.
    `informe_datos` sigue midiendo solo los precios."""

    def __init__(self, precios: DataProvider, metadata) -> None:
        self._precios = precios
        self._metadata = metadata

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        return self._precios.barras(tickers, dias)

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        return self._precios.barras_intradia(tickers, intervalo, periodo)

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        return self._metadata.metadata(tickers)

    def informe_datos(self):
        fn = getattr(self._precios, "informe_datos", None)
        return fn() if callable(fn) else None


def proveedor_metadata_configurado() -> str:
    """`yahoo` o `finnhub`; cualquier otra cosa se queda en yahoo y avisa."""
    nombre = os.environ.get(ENV_METADATA, "yahoo").strip().lower()
    if nombre in ("", "yahoo"):
        return "yahoo"
    if nombre != "finnhub":
        log.warning("%s=%s no es yahoo ni finnhub; se queda en yahoo", ENV_METADATA, nombre)
        return "yahoo"
    return "finnhub"


def proveedor_configurado(construir_yahoo=None) -> DataProvider:
    """Lee el entorno una vez por proceso. `construir_yahoo` existe para
    que el hunter pueda inyectar su `YahooProvider` (los tests lo
    parchean ahí) sin que este módulo importe el nombre al revés."""
    precios = _proveedor_precios(construir_yahoo)
    if proveedor_metadata_configurado() == "finnhub":
        from momentum_hunter.data.finnhub_metadata import FinnhubMetadata
        log.info("datos: metadata por finnhub (sin float en el plan gratis); precios sin cambio")
        return ConMetadataAparte(precios, FinnhubMetadata())
    return precios


def _proveedor_precios(construir_yahoo=None) -> DataProvider:
    construir = construir_yahoo or YahooProvider
    nombre = os.environ.get(ENV_PROVEEDOR, "alpaca").strip().lower()
    if nombre == "yahoo":
        log.info("datos: precios por yahoo (%s=yahoo)", ENV_PROVEEDOR)
        return _Medido(construir(), configurada="yahoo")
    if nombre not in ("", "alpaca"):
        return _ConfiguracionInvalida(nombre)
    feed_env = os.environ.get(ENV_FEED, "").strip().lower()
    if feed_env not in ("", "sip"):
        log.warning("%s=%s se ignora: el hunter pide siempre feed=sip", ENV_FEED, feed_env)
    log.info("datos: precios por alpaca/sip, sin respaldo de Yahoo (IEX solo ante 403 de plan SIP)")
    return ProveedorAlpaca(AlpacaProvider(feed="sip"), construir, feed="sip")
