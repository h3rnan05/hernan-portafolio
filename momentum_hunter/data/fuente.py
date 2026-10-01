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

`MOMENTUM_SIP_STREAM` (default `sombra`) no cambia este camino. Solo
`primario`, y solo con feed `sip`, intenta servir el minuto desde el
almacén del stream; si no cubre, sigue el REST de abajo. Ver
`data/sip_stream.py`.

SIP RETRASADO (`MOMENTUM_SIP_RETRASADO`, solo con feed `sip`). El plan
de las claves no da SIP de los últimos 15 min (HTTP 403). Ver
`alpaca_datos.py` para la medición.
  - `off`: como antes del 1-oct. Se pide SIP hasta ahora, da 403 y el
    pedido entero va a Yahoo (cuenta como fallback).
  - `sombra` (default): las DECISIONES salen de Yahoo, sin pasar por el
    403, porque mover la fila diaria de hoy 16 min atrás cambia el RVOL
    diario y la shortlist de v1. Además, cada pedido DIARIO se repite
    contra el SIP retrasado y se registra una comparación (`sombra sip
    retrasado: ...`). Esa respuesta nunca llega al hunter.
  - `on`: barras diarias del SIP hasta ahora-16 min (historia completa,
    volumen consolidado; la fila de hoy llega hasta el corte), Yahoo solo
    para los lotes que fallen. El minuto (VWAP, patrones, precio de
    entrada) sigue en Yahoo: el tramo reciente necesita una cinta en vivo
    y la única de Alpaca sin plan es IEX, con 0.2-10 % del volumen.
    Mezclarla cambiaría cómo se evalúan los filtros de volumen.
En `sombra` y `on` el minuto va directo a Yahoo: es lo mismo que hoy
(el 403 mandaba el pedido entero a Yahoo), sin el pedido que falla. Ese
uso es de diseño, no fallback: no suma a `fallbacks`.
"""

from __future__ import annotations

import logging
import os
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from momentum_hunter.data.alpaca_datos import (
    RETRASO_SIP_MIN,
    AlpacaProvider,
    ErrorDatosAlpaca,
    modo_sip_retrasado,
)
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


_NY = ZoneInfo("America/New_York")


def _fecha_ny(marca: str) -> str | None:
    """Epoch en segundos (texto, contrato de las diarias) -> fecha de NY."""
    try:
        return datetime.fromtimestamp(int(marca), UTC).astimezone(_NY).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _cuantiles(valores: list[float]) -> dict | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    n = len(ordenados)
    return {
        "n": n,
        "p10": round(ordenados[int(0.1 * (n - 1))], 4),
        "mediana": round(statistics.median(ordenados), 4),
        "p90": round(ordenados[int(0.9 * (n - 1))], 4),
    }


def comparar_diarias(yahoo: dict, sip: dict, hoy: str) -> dict:
    """Yahoo (lo que decidió) contra SIP retrasado, por fecha de NY.

    - `vol_ayer_sip_sobre_yahoo`: volumen de la última sesión completa
      común (no hoy). Mide si las dos cintas son el mismo consolidado.
    - `vol_hoy_sip_sobre_yahoo`: la fila de hoy; el SIP corta 16 min
      antes, así que se espera < 1.
    - `cierre_hoy_dif_pct`: |cierre SIP - cierre Yahoo| / Yahoo, fila de hoy.
    Un ticker sin la fecha en una de las dos no entra en esa cuenta: un
    faltante no es un 0 ni una razón de 0."""
    comunes = sorted(set(yahoo) & set(sip))
    vol_ayer, vol_hoy, cierre_hoy = [], [], []
    sin_hoy_sip = 0
    for t in comunes:
        y, s = yahoo[t], sip[t]
        fy = {f: i for i, m in enumerate(y.fechas) if (f := _fecha_ny(m))}
        fs = {f: i for i, m in enumerate(s.fechas) if (f := _fecha_ny(m))}
        previas = sorted(f for f in set(fy) & set(fs) if f < hoy)
        if previas:
            vy, vs = y.volume[fy[previas[-1]]], s.volume[fs[previas[-1]]]
            if vy and vy > 0 and vs is not None and vs >= 0:
                vol_ayer.append(vs / vy)
        if hoy in fy and hoy not in fs:
            sin_hoy_sip += 1
        if hoy in fy and hoy in fs:
            vy, vs = y.volume[fy[hoy]], s.volume[fs[hoy]]
            if vy and vy > 0 and vs is not None and vs >= 0:
                vol_hoy.append(vs / vy)
            cy, cs = y.close[fy[hoy]], s.close[fs[hoy]]
            if cy and cy > 0 and cs is not None:
                cierre_hoy.append(abs(cs - cy) / cy * 100.0)
    return {
        "n_yahoo": len(yahoo),
        "n_sip": len(sip),
        "n_comunes": len(comunes),
        "solo_yahoo": len(set(yahoo) - set(sip)),
        "solo_sip": len(set(sip) - set(yahoo)),
        "hoy_sin_fila_sip": sin_hoy_sip,
        "vol_ayer_sip_sobre_yahoo": _cuantiles(vol_ayer),
        "vol_hoy_sip_sobre_yahoo": _cuantiles(vol_hoy),
        "cierre_hoy_dif_pct": _cuantiles(cierre_hoy),
    }


class ProveedorConRespaldo(DataProvider):
    """Primero el feed; Yahoo solo para los símbolos (o el ciclo) que
    no respondieron. Un símbolo ausente en una respuesta 200 no es un
    fallo: el feed dijo que no hay velas, y inventarle las de otra
    fuente mezclaría dos cintas en el mismo cálculo."""

    def __init__(
        self, primario: AlpacaProvider, respaldo: DataProvider, feed: str,
        modo: str = "off", sombra: AlpacaProvider | None = None, ahora=None,
    ) -> None:
        self._primario = primario
        self._respaldo = respaldo
        self._feed = feed
        # Fuera de SIP no hay límite de 15 min: siempre `off`.
        self._modo = modo if feed == "sip" and modo in ("sombra", "on") else "off"
        self._sombra = sombra if self._modo == "sombra" else None
        self._ahora = ahora or (lambda: datetime.now(UTC))
        self.ultima_comparacion: dict | None = None
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
                nombres = [str(t) for t in tickers if isinstance(t, str) and t.strip()]
                log.warning(
                    "datos: el ciclo del feed falló (%s); este pedido entero va al respaldo (%s)",
                    ex.codigo,
                    ", ".join(nombres[:8]),
                )
                self._fallbacks += len([t for t in tickers if isinstance(t, str) and t.strip()])
                self._uso_respaldo = True
                return pedir_respaldo(tickers)
            fallidos = list(dict.fromkeys(getattr(self._primario, "fallidos", [])))
            if fallidos:
                # Se cuentan aunque el respaldo tampoco tenga vela: el
                # intento existió y es lo que hay que poder ver después.
                # El código vive en el log, no en el dict de telemetría:
                # esa forma ya la leen el reporte y las pruebas.
                codigo = getattr(self._primario, "ultimo_codigo", None)
                if not isinstance(codigo, str) or not codigo:
                    codigo = "sin_codigo"
                log.warning(
                    "datos: %d símbolo(s) del feed no respondieron (%s); se piden al respaldo (%s)",
                    len(fallidos), codigo, ", ".join(str(t) for t in fallidos[:8]),
                )
                self._fallbacks += len(fallidos)
                self._uso_respaldo = True
                extra = pedir_respaldo(fallidos)
                if isinstance(extra, dict) and isinstance(out, dict):
                    out.update(extra)
            return out
        finally:
            self._marcar_tiempo(t0)

    def _solo_respaldo(self, pedir_respaldo):
        """Yahoo por diseño (`sombra`/`on`): no es fallback."""
        t0 = time.perf_counter()
        try:
            out = pedir_respaldo()
            self._uso_respaldo = True
            return out
        finally:
            self._marcar_tiempo(t0)

    def _comparar_en_sombra(self, tickers: list[str], dias: int, decididas: dict) -> None:
        """SIP retrasado del mismo pedido, solo para el log. Nada de esto
        vuelve al caller ni cuenta en `informe_datos`; un fallo acá solo
        se registra (el tipo o el código, nunca el cuerpo)."""
        if self._sombra is None:
            return
        t0 = time.perf_counter()
        try:
            sip = self._sombra.barras(tickers, dias)
            hoy = self._ahora().astimezone(_NY).date().isoformat()
            resumen = comparar_diarias(decididas if isinstance(decididas, dict) else {}, sip, hoy)
            resumen["fallidos_sip"] = len(getattr(self._sombra, "fallidos", []) or [])
            corte = getattr(self._sombra, "corte", None)
            resumen["corte"] = corte.isoformat(timespec="seconds") if isinstance(corte, datetime) else None
            resumen["latencia_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
            self.ultima_comparacion = resumen
            log.info("sombra sip retrasado: %s", resumen)
        except ErrorDatosAlpaca as ex:
            log.warning("sombra sip retrasado: no disponible (%s)", ex.codigo)
        except Exception as ex:   # noqa: BLE001 -- la sombra nunca tumba el escaneo
            log.warning("sombra sip retrasado: no disponible (%s)", type(ex).__name__)

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        if self._modo == "sombra":
            out = self._solo_respaldo(lambda: self._respaldo.barras(tickers, dias))
            self._comparar_en_sombra(tickers, dias, out)
            return out
        return self._completar(
            tickers,
            lambda ts: self._primario.barras(ts, dias),
            lambda ts: self._respaldo.barras(ts, dias),
        )

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        # `primario` solo sustituye el minuto SIP, y solo si el almacén
        # cubre el pedido entero. Sombra (el default) ni entra. Un None
        # de `barras_si_cubren` no es "sin velas": es "seguí por REST".
        if self._feed == "sip":
            from momentum_hunter.data.sip_stream import barras_si_cubren
            t0 = time.perf_counter()
            try:
                servidas = barras_si_cubren(tickers, intervalo, periodo)
            except Exception as ex:
                log.warning(
                    "stream SIP ilegible (%s); este pedido de minuto va al REST",
                    type(ex).__name__,
                )
                servidas = None
            if servidas is not None:
                self._uso_primario = True
                self._marcar_tiempo(t0)
                return servidas
        if self._modo in ("sombra", "on"):
            # El SIP de ahora da 403 con este plan; el retrasado no sirve
            # para el minuto (16 min tarde) e IEX no es el consolidado.
            return self._solo_respaldo(
                lambda: self._respaldo.barras_intradia(tickers, intervalo, periodo),
            )
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
    if feed != "sip":
        log.info("datos: feed de precios alpaca/%s, con respaldo yahoo", feed)
        return ProveedorConRespaldo(AlpacaProvider(feed=feed), construir(), feed=feed)
    modo = modo_sip_retrasado()
    if modo == "on":
        log.info(
            "datos: diarias alpaca/sip retrasado %d min (respaldo yahoo); minuto de yahoo",
            RETRASO_SIP_MIN,
        )
        return ProveedorConRespaldo(
            AlpacaProvider(feed="sip", retraso_min=RETRASO_SIP_MIN), construir(),
            feed="sip", modo="on",
        )
    if modo == "sombra":
        log.info(
            "datos: precios de yahoo; sip retrasado %d min solo en sombra (diarias)",
            RETRASO_SIP_MIN,
        )
        return ProveedorConRespaldo(
            AlpacaProvider(feed="sip"), construir(), feed="sip", modo="sombra",
            # Un intento y timeout corto: la sombra no puede alargar el
            # escaneo como lo haría un primario con reintentos.
            sombra=AlpacaProvider(feed="sip", retraso_min=RETRASO_SIP_MIN, reintentos=1, timeout=10.0),
        )
    log.info("datos: feed de precios alpaca/sip, con respaldo yahoo")
    return ProveedorConRespaldo(AlpacaProvider(feed="sip"), construir(), feed="sip")
