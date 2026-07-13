"""Universo de acciones a escanear. Por defecto el S&P 500, obtenido de
Wikipedia (fuente gratis y estable) y cacheado a un JSON local para no
depender de la red en cada corrida. Fallback: una semilla de nombres muy
líquidos si Wikipedia no responde."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests

log = logging.getLogger("screener.universe")

CACHE = Path(__file__).resolve().parent / "sp500_cache.json"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Semilla de respaldo (mega caps líquidas) si todo lo demás falla.
SEMILLA = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM",
    "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "COST", "ORCL", "MRK",
    "ABBV", "CVX", "KO", "PEP", "BAC", "AVGO", "LLY", "AMD", "NFLX", "ADBE",
]


def _de_wikipedia() -> list[str]:
    r = requests.get(WIKI, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    # Los símbolos son enlaces a la cotización en NYSE/NASDAQ. Este patrón es
    # estable ante cambios de estructura de la tabla (Wikipedia reescribió su
    # HTML y la regex anterior dejó de matchear: por eso se valida el conteo).
    tickers = re.findall(
        r'href="https://www\.(?:nyse|nasdaq)\.com/[^"]*"[^>]*>'
        r'([A-Z][A-Z.\-]{0,6})</a>', r.text)
    # Yahoo usa '-' donde S&P usa '.', p. ej. BRK.B -> BRK-B
    limpios = sorted({t.replace(".", "-") for t in tickers if t})
    if len(limpios) < 400:
        raise ValueError(f"Wikipedia devolvió solo {len(limpios)} símbolos")
    return limpios


def cargar_sp500(refrescar: bool = False) -> list[str]:
    if not refrescar and CACHE.exists():
        try:
            return json.loads(CACHE.read_text())["tickers"]
        except Exception:
            pass
    try:
        tickers = _de_wikipedia()
        CACHE.write_text(json.dumps({"tickers": tickers}, indent=0))
        log.info("S&P 500 desde Wikipedia: %d símbolos", len(tickers))
        return tickers
    except Exception as e:
        if CACHE.exists():
            log.warning("Wikipedia falló (%s); uso caché en disco", e)
            return json.loads(CACHE.read_text())["tickers"]
        log.warning("Wikipedia falló (%s) y no hay caché; uso semilla", e)
        return SEMILLA


def cargar(universo: str = "SP500") -> list[str]:
    if universo == "SP500":
        return cargar_sp500()
    # Un archivo con un ticker por línea también sirve como universo.
    p = Path(universo)
    if p.exists():
        return [ln.strip().upper() for ln in p.read_text().splitlines()
                if ln.strip() and not ln.startswith("#")]
    raise ValueError(f"Universo desconocido: {universo}")
