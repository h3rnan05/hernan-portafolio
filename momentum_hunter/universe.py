"""Universo candidato: TODO NYSE + NASDAQ + AMEX, no solo el S&P 500 --
la premisa entera del Opportunity Hunter es que las oportunidades viven
fuera de las 500 empresas grandes que ya cubre el screener.

Fuente: los directorios de símbolos de NASDAQ Trader (gratis, sin API
key, actualizados a diario) -- son el mismo archivo que usan la mayoría
de los brokers para poblar su universo de tickers:
  - nasdaqlisted.txt: todo lo listado en NASDAQ, con flag ETF/Test Issue.
  - otherlisted.txt: todo lo listado en NYSE/NYSE American (AMEX)/NYSE
    Arca/otros, con el mismo flag ETF y un código de bolsa por columna.

Se descartan ahí mismo los "Test Issue" (símbolos de prueba del
exchange, no operables) y los ETF marcados -- así el pipeline nunca
gasta una sola llamada de datos por-ticker en algo que igual se iba a
excluir.

Limitación honesta (mismo espíritu que screener/README.md): esto da
~8,000-11,000 símbolos. Pedir barras+metadata por-ticker a Yahoo para
TODOS ellos en cada corrida es lento con datos gratis (igual que el
screener documenta para "todo el mercado" vs. S&P 500). `run.py` expone
`--limit` para pruebas rápidas; una corrida de producción real, varias
veces al día, necesitaría un proveedor de cotizaciones masivas de pago
(Polygon, Finnhub, IEX Cloud) que devuelva precio/volumen de miles de
tickers en una sola llamada -- no un `DataProvider` nuevo por ticker.
Anotado, no resuelto: mismo tipo de límite que ya acepta `screener/`."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

log = logging.getLogger("momentum_hunter.universe")

CACHE = Path(__file__).resolve().parent / "universo_cache.json"
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDirectory/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDirectory/otherlisted.txt"

# Respaldo -- descubierto el 2026-07-27: NASDAQ Trader empezó a devolver
# 404 en los .txt de arriba (confirmado en la corrida real de GitHub
# Actions, no solo localmente), y sin caché en disco el pipeline caía
# derecho a la semilla de 16 tickers. Se probó primero api.nasdaq.com
# como respaldo, pero ESE también devolvió 0 filas al correr en el
# runner real de GitHub Actions (funcionaba en local -- huele a que
# nasdaq.com bloquea/limita IPs de datacenter que no local/residenciales).
# El listado de tickers de la SEC es infraestructura de gobierno pensada
# para acceso automatizado (solo pide un User-Agent identificable, sin
# API key ni bloqueo de IP conocido) -- mucho más confiable para correr
# desde un runner de CI.
SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
# La SEC exige el formato exacto "Nombre correo@dominio" -- probado en
# vivo: cualquier otra forma (paréntesis, sin correo con @) devuelve 403
# "Request Rate Threshold Exceeded" aunque no haya rate limit real de
# por medio. Ver https://www.sec.gov/os/webmaster-faq#developers.
_SEC_USER_AGENT = "momentum-opportunity-hunter hernanlv2005@gmail.com"

# Código de bolsa de otherlisted.txt -> nuestro nombre. Solo se conservan
# NYSE (N) y NYSE American / AMEX (A); NYSE Arca (P) y el resto de tapes
# (BATS, IEX...) son casi enteramente ETFs y quedan fuera del universo
# de este bot.
_CODIGO_BOLSA = {"N": "NYSE", "A": "AMEX"}

# Semilla de respaldo -- solo para que el pipeline nunca se caiga si
# NASDAQ Trader no responde. Deliberadamente pequeña y NO es una
# recomendación de nada: cualquiera de estos puede haber dejado de
# cumplir los filtros de precio/liquidez para cuando se lea esto.
SEMILLA = [
    "SNDL", "NAKD", "GEVO", "CTRM", "SOS", "AMC", "BBIG", "PHUN",
    "MULN", "ATER", "PROG", "COSM", "IMPP", "GNS", "BTCS", "TOP",
]


@dataclass(frozen=True)
class Simbolo:
    ticker: str
    nombre: str | None
    bolsa: str
    es_etf: bool


def _parsear_nasdaqlisted(texto: str) -> list[Simbolo]:
    lineas = texto.strip().splitlines()
    out = []
    for ln in lineas[1:]:  # primera línea es el header
        campos = ln.split("|")
        if len(campos) < 7 or campos[0] == "Symbol" or ln.startswith("File Creation Time"):
            continue
        simbolo, nombre, _cat, test_issue, _fin_status, _lote, etf = campos[:7]
        if test_issue == "Y" or not simbolo:
            continue
        out.append(Simbolo(simbolo.strip().upper(), nombre.strip() or None, "NASDAQ", etf == "Y"))
    return out


def _parsear_otherlisted(texto: str) -> list[Simbolo]:
    lineas = texto.strip().splitlines()
    out = []
    for ln in lineas[1:]:
        campos = ln.split("|")
        if len(campos) < 7 or campos[0] == "ACT Symbol" or ln.startswith("File Creation Time"):
            continue
        simbolo, nombre, bolsa_cod, _cqs, etf, _lote, test_issue = campos[:7]
        bolsa = _CODIGO_BOLSA.get(bolsa_cod.strip())
        if bolsa is None or test_issue == "Y" or not simbolo:
            continue
        out.append(Simbolo(simbolo.strip().upper(), nombre.strip() or None, bolsa, etf == "Y"))
    return out


def _descargar() -> list[Simbolo]:
    r1 = requests.get(NASDAQ_LISTED, timeout=20)
    r1.raise_for_status()
    r2 = requests.get(OTHER_LISTED, timeout=20)
    r2.raise_for_status()
    simbolos = _parsear_nasdaqlisted(r1.text) + _parsear_otherlisted(r2.text)
    if len(simbolos) < 3000:
        raise ValueError(f"NASDAQ Trader devolvió solo {len(simbolos)} símbolos")
    return simbolos


_SEC_BOLSA = {"Nasdaq": "NASDAQ", "NYSE": "NYSE"}


def _parsear_sec_tickers(payload: dict) -> list[Simbolo]:
    """`company_tickers_exchange.json` no distingue NYSE American (AMEX)
    de NYSE -- ambos llegan como "NYSE" (confirmado con tickers AMEX
    conocidos como GORO/NAK). Se acepta esa pérdida de detalle porque
    `bolsa` solo se usa para trazabilidad en la auditoría, no para
    filtrar candidatos. Se descartan OTC/CBOE (pink sheets y BZX) para
    quedarse con el mismo universo NYSE+NASDAQ que ya documenta este
    módulo -- ETFs no aparecen aquí porque `company_tickers_exchange.json`
    solo cubre emisores que reportan 10-K, no fondos."""
    out = []
    for _cik, nombre, simbolo, bolsa_sec in payload.get("data") or []:
        bolsa = _SEC_BOLSA.get(bolsa_sec)
        simbolo = (simbolo or "").strip().upper()
        if bolsa is None or not simbolo:
            continue
        out.append(Simbolo(simbolo, (nombre or "").strip() or None, bolsa, False))
    return out


def _descargar_respaldo() -> list[Simbolo]:
    """Fuente de respaldo cuando los .txt de NASDAQ Trader fallan (ver
    SEC_TICKERS arriba)."""
    r = requests.get(SEC_TICKERS, headers={"User-Agent": _SEC_USER_AGENT}, timeout=20)
    r.raise_for_status()
    simbolos = _parsear_sec_tickers(r.json())
    if len(simbolos) < 3000:
        raise ValueError(f"listado de la SEC devolvió solo {len(simbolos)} símbolos")
    return simbolos


def _descargar_con_respaldo() -> list[Simbolo]:
    """Intenta NASDAQ Trader primero; si falla (por ejemplo el 404 visto
    el 2026-07-27), intenta el respaldo antes de rendirse. Propaga el
    error del respaldo si ambos fallan -- `cargar()` decide ahí si cae a
    caché o a la semilla."""
    try:
        simbolos = _descargar()
        log.info("universo NYSE+NASDAQ+AMEX: %d símbolos (%d ETFs excluidos por defecto)",
                  len(simbolos), sum(s.es_etf for s in simbolos))
        return simbolos
    except Exception as e_primario:
        simbolos = _descargar_respaldo()
        log.warning("NASDAQ Trader falló (%s); uso respaldo del listado de la SEC -- %d símbolos",
                    e_primario, len(simbolos))
        return simbolos


def cargar(refrescar: bool = False, excluir_etf: bool = True) -> list[Simbolo]:
    """Universo completo NYSE+NASDAQ+AMEX, cacheado a disco (mismo patrón
    que `screener.universe.cargar_sp500`) para no depender de la red en
    cada corrida el mismo día."""
    if not refrescar and CACHE.exists():
        try:
            data = json.loads(CACHE.read_text())
            simbolos = [Simbolo(**s) for s in data["simbolos"]]
            return [s for s in simbolos if not (excluir_etf and s.es_etf)]
        except Exception as e:
            log.warning("caché de universo corrupta (%s); refresco", e)

    try:
        simbolos = _descargar_con_respaldo()
        CACHE.write_text(json.dumps({
            "generado": datetime.now(UTC).isoformat(timespec="seconds"),
            "simbolos": [s.__dict__ for s in simbolos],
        }, ensure_ascii=False))
    except Exception as e:
        if CACHE.exists():
            log.warning("NASDAQ Trader y respaldo fallaron (%s); uso caché en disco", e)
            data = json.loads(CACHE.read_text())
            simbolos = [Simbolo(**s) for s in data["simbolos"]]
        else:
            log.warning("NASDAQ Trader falló (%s) y no hay caché; uso semilla", e)
            simbolos = [Simbolo(t, None, "NASDAQ", False) for t in SEMILLA]

    return [s for s in simbolos if not (excluir_etf and s.es_etf)]


def tickers(refrescar: bool = False) -> list[str]:
    return [s.ticker for s in cargar(refrescar)]


def desde_archivo(path: str) -> list[str]:
    """Universo alternativo: un archivo con un ticker por línea (mismo
    patrón que `screener.universe.cargar`). Útil para correr sobre una
    watchlist curada en vez del mercado completo -- ver la limitación de
    rendimiento documentada arriba."""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Archivo de universo no encontrado: {path}")
    return [ln.strip().upper() for ln in p.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]
