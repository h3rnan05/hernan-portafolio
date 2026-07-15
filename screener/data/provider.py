"""Capa de abstracción de datos (principio: 'API abstraction').

`DataProvider` es la interfaz que el resto del screener consume. Hoy la
implementa `YahooProvider` con datos gratis (API de chart de Yahoo por
requests plano + yfinance para fundamentales best-effort). Para pasar a
grado institucional, se escribe otra implementación (FMPProvider,
SharadarProvider, PolygonProvider...) SIN tocar los factores ni el ranking:
solo se cambia qué provider se inyecta en el screener (dependency injection).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import requests

log = logging.getLogger("screener.data")


@dataclass
class Barras:
    """Serie diaria de un ticker. Listas paralelas, orden cronológico."""
    ticker: str
    fechas: list[str]
    close: list[float]
    high: list[float]
    low: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.close)


@dataclass
class Fundamentales:
    """Snapshot fundamental. Cualquier campo puede ser None (datos gratis
    son parciales); los factores degradan con gracia ante None.

    `nombre`/`industria` son metadata puramente descriptiva para el reporte
    (report.py) — no entran a ningún cálculo de scoring/ranking."""
    ticker: str
    pe: float | None = None
    pb: float | None = None
    roe: float | None = None
    margen_operativo: float | None = None
    crecimiento_ingresos: float | None = None
    sector: str | None = None
    nombre: str | None = None
    industria: str | None = None


class DataProvider(ABC):
    """Contrato que cualquier fuente de datos debe cumplir."""

    @abstractmethod
    def barras(self, tickers: list[str], dias: int = 400) -> dict[str, Barras]:
        """Barras diarias para varios tickers. Omite los que fallen."""

    @abstractmethod
    def fundamentales(self, tickers: list[str]) -> dict[str, Fundamentales]:
        """Snapshot fundamental por ticker (best-effort)."""


class YahooProvider(DataProvider):
    """Implementación gratis. Precios vía la API de chart de Yahoo (robusta,
    la misma que ya usa el wizards bot). Fundamentales vía yfinance si está
    instalado; si no, regresa snapshots vacíos y el screener sigue con los
    factores de precio."""

    CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
    HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Mozilla/5.0"}

    def __init__(self, pausa: float = 0.15, reintentos: int = 3) -> None:
        self.pausa = pausa
        self.reintentos = reintentos

    def barras(self, tickers: list[str], dias: int = 400) -> dict[str, Barras]:
        rango = "2y" if dias > 365 else "1y"
        out: dict[str, Barras] = {}
        for t in tickers:
            b = self._barras_una(t, rango)
            if b and len(b) >= 30:
                out[t] = b
            time.sleep(self.pausa)  # cortesía: no ráfaga contra Yahoo
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
                fechas, close, high, low, vol = [], [], [], [], []
                for i, epoch in enumerate(ts):
                    c, h, lo, v = q["close"][i], q["high"][i], q["low"][i], q["volume"][i]
                    if None in (c, h, lo):
                        continue
                    fechas.append(str(epoch))
                    close.append(float(c))
                    high.append(float(h))
                    low.append(float(lo))
                    vol.append(float(v or 0))
                if close:
                    return Barras(ticker, fechas, close, high, low, vol)
                return None
            except Exception as e:
                if intento == self.reintentos - 1:
                    log.debug("barras %s falló: %s", ticker, e)
                time.sleep(1.5 * (intento + 1))
        return None

    def fundamentales(self, tickers: list[str]) -> dict[str, Fundamentales]:
        try:
            import yfinance as yf
        except ImportError:
            log.warning("yfinance no instalado: fundamentales quedan vacíos "
                        "(el screener usa solo factores de precio)")
            return {t: Fundamentales(t) for t in tickers}
        out: dict[str, Fundamentales] = {}
        for t in tickers:
            try:
                info = yf.Ticker(t).info
                out[t] = Fundamentales(
                    ticker=t,
                    pe=_num(info.get("trailingPE")),
                    pb=_num(info.get("priceToBook")),
                    roe=_num(info.get("returnOnEquity")),
                    margen_operativo=_num(info.get("operatingMargins")),
                    crecimiento_ingresos=_num(info.get("revenueGrowth")),
                    sector=info.get("sector"),
                    nombre=info.get("longName") or info.get("shortName"),
                    industria=info.get("industry"),
                )
            except Exception as e:
                log.debug("fundamentales %s falló: %s", t, e)
                out[t] = Fundamentales(t)
            time.sleep(self.pausa)
        return out


def _num(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
        return f if f == f else None  # descarta NaN
    except (TypeError, ValueError):
        return None
