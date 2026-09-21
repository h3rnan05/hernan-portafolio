"""Capa de abstracción de datos -- mismo principio que
`screener/data/provider.py` (dependency injection: el resto del pipeline
solo conoce `DataProvider`, nunca a Yahoo directamente), pero una
implementación completamente separada: este bot necesita `open` (para
gap%), float, short interest y pre/after-market -- datos que el screener
del S&P 500 ni siquiera pide.

`YahooProvider` es la implementación gratis de hoy. Limitaciones
honestas (mismo espíritu que screener/README.md):

- **Pre-market / after-hours**: yfinance expone `preMarketPrice` /
  `postMarketPrice` de forma inconsistente (a veces ausentes fuera de la
  ventana extendida, o desfasados varios minutos). Se usan best-effort;
  si no están, quedan en `None` -- nunca se inventa un cambio.
- **Borrow fee**: no existe una fuente gratis confiable. `borrow_fee_pct`
  queda siempre en `None` -- ver `models.Metadata`.
- **Clasificación ETF/SPAC/CEF**: `quoteType` de Yahoo distingue ETFs de
  forma confiable; SPAC y closed-end fund se detectan con heurísticas de
  nombre (`_parece_spac`, `_parece_cef`) porque Yahoo no expone un flag
  dedicado -- pueden fallar en casos raros, documentado como best-effort.

`barras_intradia` (Yahoo temporal, pedido explícito del dueño del
producto 2026-07-26: "el algoritmo nunca debe depender de Yahoo
específicamente") -- la interfaz `DataProvider` es la única frontera que
conoce la palabra "Yahoo". `factors/intradia.py`, `classification.py`,
`early_opportunity.py` y `evaluator.py` solo importan `BarraIntradia`
(genérica, en `models.py`). Conectar Polygon/Alpaca/Tradier más adelante
es escribir OTRA clase que herede de `DataProvider`, nunca tocar esos
cuatro módulos. `intervalo`/`periodo` son strings genéricos ("1m"/"5m",
"1d"/"5d") que cada implementación traduce a su propia API -- Yahoo los
usa tal cual porque así los espera su endpoint de chart, pero eso es un
detalle de `YahooProvider`, no del contrato."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import requests

from momentum_hunter.models import Barras, BarraIntradia, Metadata

log = logging.getLogger("momentum_hunter.data")

_SPAC_KEYWORDS = re.compile(
    r"\bacquisition\s+(corp|corporation|company|holdings)\b|\bspac\b|"
    r"\bblank\s+check\b", re.IGNORECASE,
)
_CEF_KEYWORDS = re.compile(
    r"\bclosed-?end\b|\b(municipal|income|opportunities?)\s+(fund|trust)\b",
    re.IGNORECASE,
)


def _parece_spac(nombre: str | None) -> bool:
    return bool(nombre) and bool(_SPAC_KEYWORDS.search(nombre))


def _parece_cef(nombre: str | None, quote_type: str | None) -> bool:
    if quote_type and quote_type.upper() in ("CLOSEDEND", "MUTUALFUND"):
        return True
    return bool(nombre) and bool(_CEF_KEYWORDS.search(nombre))


_EXCHANGE_MAP = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",
    "NYQ": "NYSE", "NYSE": "NYSE",
    "ASE": "AMEX", "AMEX": "AMEX", "PCX": "AMEX",
}


def _epoch_a_iso(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=UTC).isoformat(timespec="seconds")


def parsear_chart_intradia(ticker: str, cuerpo: dict) -> BarraIntradia | None:
    """Velas intradía a partir del JSON crudo del chart de Yahoo. Lanza si
    el cuerpo no tiene la forma esperada (quien llama decide qué hacer).
    Extraído de `_intradia_una` sin cambiar una regla: es LA definición
    de qué vela cuenta y cuál no, y el panel la reutiliza para mostrar
    exactamente lo que vio el hunter."""
    res = cuerpo["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    marcas, o, c, h, lo, vol = [], [], [], [], [], []
    for i, epoch in enumerate(ts):
        op, cl, hi, low, v = (
            q["open"][i], q["close"][i], q["high"][i], q["low"][i], q["volume"][i]
        )
        # Mismo bug real que en `_barras_una` (ver ese comentario) -- la
        # vela en formación de la sesión actual llega con `volume=None`
        # seguido, y `float(v or 0)` lo convertía en CERO real. Esto
        # dejaba `rvol_actual` (usa SOLO la última vela) en 0.0 de forma
        # sistemática, para todo ticker, todos los días -- la pregunta
        # "¿está entrando dinero ahora?" del evaluador nunca podía pasar.
        if None in (op, cl, hi, low, v):
            continue
        marcas.append(_epoch_a_iso(epoch))
        o.append(float(op))
        c.append(float(cl))
        h.append(float(hi))
        lo.append(float(low))
        vol.append(float(v))
    # Corrección 2026-08-21 ("por qué no ha metido ningún trade"): el fix
    # de arriba (None -> se descarta) no bastaba -- confirmado contra la
    # respuesta cruda de Yahoo, la vela en curso casi siempre llega con
    # volumen 0 EXPLÍCITO (no None): el minuto todavía no acumuló ningún
    # trade en el instante exacto de la consulta. `rvol_actual` (usa solo
    # la última vela) seguía en 0.0 siempre, para todo ticker -- ver
    # `_velas_finales_en_formacion`.
    recortar = _velas_finales_en_formacion(vol)
    if recortar:
        marcas, o, c, h, lo, vol = (
            marcas[:-recortar], o[:-recortar], c[:-recortar], h[:-recortar],
            lo[:-recortar], vol[:-recortar],
        )
    if c:
        return BarraIntradia(ticker, marcas, o, c, h, lo, vol)
    return None


def _velas_finales_en_formacion(vol: list[float]) -> int:
    """Cuántas velas al FINAL de la lista tienen volumen exactamente 0.0
    -- bug real (2026-08-21, "por qué no ha metido ningún trade"): la
    vela en curso de Yahoo casi siempre llega así, un 0 REAL (no
    `None`) -- el minuto todavía no terminó de acumular ningún trade en
    el instante exacto de la consulta. Confirmado contra la respuesta
    cruda de Yahoo: la vela anterior a esa suele venir enteramente
    `None` (ya se descarta antes de llegar acá) y la última con precio
    ya confirmado pero volumen en 0 -- ambas son la MISMA vela en
    formación, solo en distintos instantes de esa formación. Un 0 real
    en medio de la sesión (una acción líquida que de verdad no operó un
    minuto completo) es rarísimo pero posible -- por eso solo se
    recortan las del FINAL, nunca las de adentro, y siempre se deja al
    menos una vela aunque todas sean 0 (mejor un dato raro que ninguno)."""
    n = 0
    while n < len(vol) - 1 and vol[-1 - n] == 0.0:
        n += 1
    return n


def _num(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
        return f if f == f else None  # descarta NaN
    except (TypeError, ValueError):
        return None


class DataProvider(ABC):
    """Contrato que cualquier fuente de datos debe cumplir."""

    @abstractmethod
    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        """Barras diarias (con `open`, para gap%) por ticker. Omite los que fallen."""

    @abstractmethod
    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        """Snapshot no-técnico por ticker (best-effort)."""

    @abstractmethod
    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        """Velas intradía recientes, incluyendo pre-market cuando el
        proveedor lo soporte. Se llama SOLO sobre el puñado de candidatos
        que ya pasaron el filtro grueso diario (ver `run.py`) -- pedir
        esto para el universo completo no es viable con ningún proveedor
        gratis. Omite los tickers que fallen."""


ENV_PAUSA_YAHOO = "MOMENTUM_YAHOO_PAUSA_ARCHIVO"
SEGUNDOS_PAUSA_429 = 900.0


class PausaYahoo:
    """Freno del BOT ante un 429 de Yahoo, en un archivo propio.

    POR QUÉ. En el VPS el escaneo (~1.000 tickers por corrida) y el panel
    (velas de los tickers en operación) salen de la misma IP. Si Yahoo
    limita, insistir empeora: hasta hoy el proveedor reintentaba tres veces
    cualquier excepción sin distinguir un 429. Ahora, ante un 429, escribe
    `{"hasta": ...}` en el archivo que nombra `MOMENTUM_YAHOO_PAUSA_ARCHIVO`
    y deja de pedir hasta esa hora (15 min): el siguiente slot llega en 30.

    El archivo es del bot y solo del bot. El panel lo LEE para frenarse
    él también (dashboard/velas.py), pero el bot nunca obedece la pausa
    que el panel escribe en el suyo: una racha de 429 provocada por el
    panel no puede apagar el escaneo. Sin la variable no hay archivo y
    el comportamiento es el de siempre (salvo que un 429 ya no se
    reintenta). Nunca lanza: la pausa es una cortesía, no una fuente."""

    def __init__(self, ruta: str | None = None) -> None:
        raw = ruta if ruta is not None else os.environ.get(ENV_PAUSA_YAHOO, "")
        self.ruta = raw.strip() or None
        self._avisado = False

    def hasta(self) -> datetime | None:
        if not self.ruta:
            return None
        try:
            crudo = json.loads(open(self.ruta, encoding="utf-8").read())
            momento = datetime.fromisoformat(crudo.get("hasta"))
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        return momento if momento.tzinfo is not None else None

    def activa(self, ahora: datetime | None = None) -> bool:
        limite = self.hasta()
        if limite is None:
            return False
        vigente = (ahora or datetime.now(UTC)) < limite
        if vigente and not self._avisado:
            log.warning("Yahoo en pausa por un 429 previo hasta las %s UTC: no se piden más datos "
                        "en esta corrida", limite.strftime("%H:%M"))
            self._avisado = True
        return vigente

    def pausar(self, ahora: datetime | None = None, segundos: float = SEGUNDOS_PAUSA_429) -> None:
        ahora = ahora or datetime.now(UTC)
        limite = ahora + timedelta(seconds=segundos)
        log.warning("Yahoo respondió 429: el bot deja de pedir hasta las %s UTC", limite.strftime("%H:%M"))
        if not self.ruta:
            return
        try:
            os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
            temporal = self.ruta + ".tmp"
            with open(temporal, "w", encoding="utf-8") as fh:
                json.dump({"hasta": limite.isoformat(timespec="seconds"),
                           "desde": ahora.isoformat(timespec="seconds"), "motivo": "429", "origen": "bot"}, fh)
            os.replace(temporal, self.ruta)
        except OSError as e:
            log.warning("no se pudo escribir la pausa de Yahoo (%s)", type(e).__name__)


class LimiteDePeticionesYahoo(Exception):
    """Yahoo respondió 429. Interna al proveedor: no sale de él."""


class YahooProvider(DataProvider):
    """Precios vía la API de chart de Yahoo (misma robusta usada en
    `screener/`). Metadata vía yfinance si está instalado; si no, degrada
    a `Metadata` vacías (el pipeline sigue con solo factores de precio)."""

    CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
    HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Mozilla/5.0"}

    def __init__(self, pausa: float = 0.15, reintentos: int = 3, pausa_429: PausaYahoo | None = None) -> None:
        self.pausa = pausa
        self.reintentos = reintentos
        self.pausa_429 = pausa_429 if pausa_429 is not None else PausaYahoo()

    def _get_chart(self, ticker: str, params: dict):
        """Una petición al chart. Un 429 escribe la pausa y corta sin
        reintentos; con la pausa activa no se pide nada."""
        if self.pausa_429.activa():
            raise LimiteDePeticionesYahoo("pausa activa")
        r = requests.get(self.CHART.format(t=ticker), params=params, headers=self.HEADERS, timeout=15)
        # getattr: las pruebas viejas usan respuestas falsas sin status_code.
        if getattr(r, "status_code", None) == 429:
            self.pausa_429.pausar()
            raise LimiteDePeticionesYahoo("429")
        return r

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        rango = "2y" if dias > 365 else "1y"
        out: dict[str, Barras] = {}
        for t in tickers:
            b = self._barras_una(t, rango)
            if b and len(b) >= 20:
                out[t] = b
            time.sleep(self.pausa)
        log.info("barras obtenidas: %d/%d tickers", len(out), len(tickers))
        return out

    def _barras_una(self, ticker: str, rango: str) -> Barras | None:
        for intento in range(self.reintentos):
            try:
                r = self._get_chart(ticker, {"interval": "1d", "range": rango})
                res = r.json()["chart"]["result"][0]
                ts = res["timestamp"]
                q = res["indicators"]["quote"][0]
                fechas, o, c, h, lo, vol = [], [], [], [], [], []
                for i, epoch in enumerate(ts):
                    op, cl, hi, low, v = (
                        q["open"][i], q["close"][i], q["high"][i], q["low"][i], q["volume"][i]
                    )
                    # Bug real (2026-08-20, "por qué no avisó de MRNA a
                    # tiempo"): la vela de HOY todavía en formación suele
                    # llegar con precio ya confirmado pero `volume=None`
                    # (el agregado de volumen de Yahoo va con retraso) --
                    # `float(v or 0)` lo convertía en CERO real en vez de
                    # dato faltante, contaminando `rvol`/`volumen_promedio`
                    # con un "no entró nada de dinero" inventado. Se
                    # descarta la vela entera, igual que ya se hace con
                    # OHLC ausente -- nunca se inventa un volumen de cero.
                    if None in (op, cl, hi, low, v):
                        continue
                    fechas.append(str(epoch))
                    o.append(float(op))
                    c.append(float(cl))
                    h.append(float(hi))
                    lo.append(float(low))
                    vol.append(float(v))
                recortar = _velas_finales_en_formacion(vol)
                if recortar:
                    fechas, o, c, h, lo, vol = (
                        fechas[:-recortar], o[:-recortar], c[:-recortar], h[:-recortar],
                        lo[:-recortar], vol[:-recortar],
                    )
                if c:
                    return Barras(ticker, fechas, o, c, h, lo, vol)
                return None
            except LimiteDePeticionesYahoo:
                return None
            except Exception as e:
                if intento == self.reintentos - 1:
                    log.debug("barras %s falló: %s", ticker, e)
                time.sleep(1.5 * (intento + 1))
        return None

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        out: dict[str, BarraIntradia] = {}
        for t in tickers:
            b = self._intradia_una(t, intervalo, periodo)
            if b and len(b) >= 5:
                out[t] = b
            time.sleep(self.pausa)
        log.info("barras intradía obtenidas: %d/%d tickers", len(out), len(tickers))
        return out

    def params_intradia(self, intervalo: str, periodo: str) -> dict[str, str]:
        """Parámetros exactos de la consulta intradía. Expuestos para que
        quien necesite pedir LO MISMO que el hunter (el panel, ver
        `dashboard/velas.py`) no tenga que copiarlos."""
        # includePrePost=true: sin esto, Yahoo solo devuelve la sesión
        # regular -- y el pre-market high es un nivel clave para Gap and
        # Go / Opening Range Breakout.
        return {"interval": intervalo, "range": periodo, "includePrePost": "true"}

    def _intradia_una(self, ticker: str, intervalo: str, periodo: str) -> BarraIntradia | None:
        for intento in range(self.reintentos):
            try:
                r = self._get_chart(ticker, self.params_intradia(intervalo, periodo))
                return parsear_chart_intradia(ticker, r.json())
            except LimiteDePeticionesYahoo:
                return None
            except Exception as e:
                if intento == self.reintentos - 1:
                    log.debug("barras intradía %s falló: %s", ticker, e)
                time.sleep(1.5 * (intento + 1))
        return None

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        try:
            import yfinance as yf
        except ImportError:
            log.warning("yfinance no instalado: metadata queda vacía "
                        "(el pipeline usa solo factores de precio)")
            return {t: Metadata(t) for t in tickers}
        out: dict[str, Metadata] = {}
        for t in tickers:
            out[t] = self._metadata_una(t, yf)
            time.sleep(self.pausa)
        return out

    def _metadata_una(self, ticker: str, yf) -> Metadata:
        try:
            info = yf.Ticker(ticker).info
        except Exception as e:
            log.debug("metadata %s falló: %s", ticker, e)
            return Metadata(ticker)

        nombre = info.get("longName") or info.get("shortName")
        quote_type = info.get("quoteType")
        precio_regular = _num(info.get("regularMarketPrice"))
        pre = _num(info.get("preMarketPrice"))
        post = _num(info.get("postMarketPrice"))
        prev_close = _num(info.get("regularMarketPreviousClose"))

        cambio_pre = None
        if pre is not None and prev_close:
            cambio_pre = (pre - prev_close) / prev_close
        cambio_post = None
        if post is not None and precio_regular:
            cambio_post = (post - precio_regular) / precio_regular

        short_pct = _num(info.get("shortPercentOfFloat"))
        days_to_cover = _num(info.get("shortRatio"))

        return Metadata(
            ticker=ticker,
            nombre=nombre,
            bolsa=_EXCHANGE_MAP.get(info.get("exchange", ""), info.get("exchange")),
            es_etf=(quote_type or "").upper() == "ETF",
            es_spac=_parece_spac(nombre),
            es_cef=_parece_cef(nombre, quote_type),
            es_adr=bool(info.get("fromCurrency")) or "ADR" in (nombre or "").upper(),
            market_cap=_num(info.get("marketCap")),
            shares_float=_num(info.get("floatShares")),
            short_pct_float=short_pct,
            days_to_cover=days_to_cover,
            borrow_fee_pct=None,  # no disponible gratis -- ver docstring del módulo
            cambio_premarket_pct=cambio_pre,
            cambio_afterhours_pct=cambio_post,
        )
