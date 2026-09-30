"""Acciones en circulación según la SEC y la regla del 20 % contra Yahoo.

Fuente: `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/dei/EntityCommonStockSharesOutstanding.json`
(el dato `dei` que trae la portada de cada 10-K/10-Q). Cada entrada
trae `val`, `end` (fecha del dato), `filed` (cuándo se presentó) y
`form`. Para un instante se toma la última entrada PRESENTADA antes de
ese día: lo que un lector de EDGAR sabía entonces, no lo que se supo
después.

Decisiones (2026-09-28, no se reabren aquí):
  - El float sigue siendo el de Yahoo. Si Yahoo falla, el float es
    FALTANTE y el símbolo se excluye; NO se sustituye con las acciones
    en circulación de la SEC.
  - La regla del 20 % compara acciones en circulación EDGAR vs acciones
    en circulación Yahoo (`sharesOutstanding`): una discrepancia mayor
    dice que uno de los dos está viejo (un split, una emisión grande).
    Quién trae el dato de Yahoo es el enriquecedor (`acciones_yahoo`).

COLUMNAS:
    edgar_acciones               acciones en circulación | FALTANTE
    edgar_acciones_fecha         `end` del dato (ISO) | FALTANTE
    edgar_acciones_presentado    `filed` (ISO) | FALTANTE
    edgar_acciones_dias          días entre `end` y el instante | FALTANTE
    acciones_yahoo               lo que dio el enriquecedor | FALTANTE
    acciones_discrepancia_pct    |edgar − yahoo| / yahoo | FALTANTE
    acciones_discrepancia_20     True si > 20 % | False | FALTANTE
No hay None en estas columnas: un emisor sin este dato en XBRL (ADR,
emisores pequeños sin inline XBRL) es FALTANTE, no cero.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from fuentes import cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import FALTANTE, ErrorFuente, numero
from fuentes.edgar import LectorEdgar
from fuentes.tiempo import fecha_ny, leer_fecha

log = logging.getLogger("fuentes.edgar_acciones")

URL_CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/"
CONCEPTO = "dei/EntityCommonStockSharesOutstanding"
EDAD_S = 24 * 3600.0
UMBRAL = 0.20


@dataclass(frozen=True)
class Dato:
    valor: float
    fin: date
    presentado: date
    form: str


def url_concepto(cik: int) -> str:
    return f"{URL_CONCEPT}CIK{cik:010d}/{CONCEPTO}.json"


def leer_concepto(cuerpo: object) -> list[Dato]:
    if not isinstance(cuerpo, dict):
        raise ErrorFuente("cuerpo", "edgar_acciones")
    units = cuerpo.get("units")
    serie = units.get("shares") if isinstance(units, dict) else None
    if not isinstance(serie, list):
        raise ErrorFuente("cuerpo", "edgar_acciones")
    out = []
    for e in serie:
        if not isinstance(e, dict):
            continue
        v, fin, filed = numero(e.get("val")), leer_fecha(e.get("end")), leer_fecha(e.get("filed"))
        if v is None or v <= 0 or fin is None or filed is None:
            continue   # una entrada incompleta no aporta; no invalida las demás
        out.append(Dato(v, fin, filed, str(e.get("form", ""))))
    return out


def dato_vigente(datos: list[Dato], dia: date) -> Dato | None:
    """La última entrada presentada hasta `dia` (inclusive); entre las
    presentadas el mismo día, la de `end` más reciente."""
    validos = [d for d in datos if d.presentado <= dia]
    if not validos:
        return None
    return max(validos, key=lambda d: (d.presentado, d.fin))


def discrepancia(edgar: float, yahoo: float) -> float | None:
    if yahoo <= 0:
        return None
    return abs(edgar - yahoo) / yahoo


class EdgarAcciones:
    nombre = "edgar_acciones"
    _NOMBRES = ["edgar_acciones", "edgar_acciones_fecha", "edgar_acciones_presentado", "edgar_acciones_dias",
                "acciones_yahoo", "acciones_discrepancia_pct", "acciones_discrepancia_20"]

    def __init__(self, lector: LectorEdgar | None = None, acciones_yahoo: Callable[[str], object] | None = None,
                 cache: Cache | None = None) -> None:
        self.lector = lector or LectorEdgar()
        self.cache = cache or self.lector.cache
        # Sin enriquecedor no hay Yahoo: las columnas de comparación quedan FALTANTE.
        self._yahoo = acciones_yahoo or (lambda t: FALTANTE)
        self._serie: dict[int, list[Dato] | None] = {}

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def serie(self, cik: int) -> list[Dato] | None:
        if cik not in self._serie:
            url = url_concepto(cik)
            try:
                cuerpo = self.cache.obtener("edgar_acciones", url, lambda: self.lector.cliente().get_json(url), EDAD_S)
                self._serie[cik] = leer_concepto(cuerpo)
            except ErrorFuente as ex:
                # Un 404 aquí es "el emisor no reporta el concepto": tampoco es cero.
                log.warning("edgar_acciones: %s (CIK %d)", ex.codigo, cik)
                self._serie[cik] = None
        return self._serie[cik]

    def columnas(self, ticker: str, momento: datetime) -> dict:
        try:
            cik = self.lector.cik(ticker)
        except ErrorFuente:
            cik = None
        if cik is None:
            return todas_faltantes(self._NOMBRES)
        serie = self.serie(cik)
        dia = fecha_ny(momento)
        dato = dato_vigente(serie, dia) if serie else None
        if dato is None:
            return todas_faltantes(self._NOMBRES)
        yahoo = numero(self._yahoo(ticker))
        out = {"edgar_acciones": dato.valor, "edgar_acciones_fecha": dato.fin.isoformat(),
               "edgar_acciones_presentado": dato.presentado.isoformat(), "edgar_acciones_dias": (dia - dato.fin).days,
               "acciones_yahoo": FALTANTE, "acciones_discrepancia_pct": FALTANTE, "acciones_discrepancia_20": FALTANTE}
        if yahoo is None:
            return out
        pct = discrepancia(dato.valor, yahoo)
        out["acciones_yahoo"] = yahoo
        if pct is not None:
            out["acciones_discrepancia_pct"] = round(pct, 4)
            out["acciones_discrepancia_20"] = pct > UMBRAL
        return out


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar edgar_acciones TICKER`."""
    from fuentes.grabar import grabar_get

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar edgar_acciones")
    ap.add_argument("ticker")
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    lector = LectorEdgar(Cache(Path(os.environ.get("TMPDIR", "/tmp")) / "fuentes_grabar"))
    cik = lector.cik(args.ticker)
    if cik is None:
        print(f"{args.ticker}: sin CIK")
        return 1
    import json
    _, texto = grabar_get(lector.cliente(), "edgar", f"acciones_{args.ticker.upper()}", url_concepto(cik),
                          directorio=args.dir)
    datos = leer_concepto(json.loads(texto))
    d = dato_vigente(datos, date.today())
    print(f"{args.ticker}: {len(datos)} entradas; vigente {d}")
    return 0


cli.registrar("edgar_acciones", _grabar)
