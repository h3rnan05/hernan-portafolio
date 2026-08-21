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

import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
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


class YahooProvider(DataProvider):
    """Precios vía la API de chart de Yahoo (misma robusta usada en
    `screener/`). Metadata vía yfinance si está instalado; si no, degrada
    a `Metadata` vacías (el pipeline sigue con solo factores de precio)."""

    CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
    HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Mozilla/5.0"}

    def __init__(self, pausa: float = 0.15, reintentos: int = 3) -> None:
        self.pausa = pausa
        self.reintentos = reintentos

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
                r = requests.get(
                    self.CHART.format(t=ticker),
                    params={"interval": "1d", "range": rango},
                    headers=self.HEADERS, timeout=15,
                )
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
                if c:
                    return Barras(ticker, fechas, o, c, h, lo, vol)
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

    def _intradia_una(self, ticker: str, intervalo: str, periodo: str) -> BarraIntradia | None:
        for intento in range(self.reintentos):
            try:
                r = requests.get(
                    self.CHART.format(t=ticker),
                    # includePrePost=true: sin esto, Yahoo solo devuelve la
                    # sesión regular -- y el pre-market high es un nivel
                    # clave para Gap and Go / Opening Range Breakout.
                    params={"interval": intervalo, "range": periodo, "includePrePost": "true"},
                    headers=self.HEADERS, timeout=15,
                )
                res = r.json()["chart"]["result"][0]
                ts = res["timestamp"]
                q = res["indicators"]["quote"][0]
                marcas, o, c, h, lo, vol = [], [], [], [], [], []
                for i, epoch in enumerate(ts):
                    op, cl, hi, low, v = (
                        q["open"][i], q["close"][i], q["high"][i], q["low"][i], q["volume"][i]
                    )
                    # Mismo bug real que en `_barras_una` (ver ese
                    # comentario) -- la vela en formación de la sesión
                    # actual llega con `volume=None` seguido, y
                    # `float(v or 0)` lo convertía en CERO real. Esto
                    # dejaba `rvol_actual` (usa SOLO la última vela) en
                    # 0.0 de forma sistemática, para todo ticker, todos
                    # los días -- la pregunta "¿está entrando dinero
                    # ahora?" del evaluador nunca podía pasar, nunca.
                    if None in (op, cl, hi, low, v):
                        continue
                    marcas.append(_epoch_a_iso(epoch))
                    o.append(float(op))
                    c.append(float(cl))
                    h.append(float(hi))
                    lo.append(float(low))
                    vol.append(float(v))
                if c:
                    return BarraIntradia(ticker, marcas, o, c, h, lo, vol)
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
