"""Short interest consolidado de FINRA (quincenal) como columnas del backtest v2.

Fuente: FINRA API Query, dataset público `otcMarket/consolidatedShortInterest`
(`POST https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest`
con `{"limit", "offset", "compareFilters": [{"fieldName": "settlementDate",
"compareType": "EQUAL", "fieldValue": "AAAA-MM-DD"}]}` y `Accept: text/csv`).
Sin credenciales para los datasets públicos; se paginan 5.000 filas por
pedido y se cachea la fecha de corte entera (no cambia una vez publicada).
Columnas del CSV: settlementDate, symbolCode, issueName, marketClassCode,
currentShortPositionQuantity, previousShortPositionQuantity, changePercent,
averageDailyVolumeQuantity, daysToCoverQuantity, stockSplitFlag, revisionFlag.

FECHA DE PUBLICACIÓN, NO DE CORTE (decisión 2026-09-28). El corte es el
15 y el último día hábil del mes, pero el mercado ve el dato ~8 días
hábiles después, tras el cierre. Para un instante se usa el último
reporte cuya PUBLICACIÓN (día de publicación a las 16:00 NY) es anterior
al instante. Usar la fecha de corte sería mirar el futuro.

Cómo se calcula la publicación: `fuentes/datos/finra_calendario.json`
(corte -> publicación, copiado del calendario que FINRA publica) manda;
si un corte no está ahí, corte + 8 días hábiles (lunes a viernes, SIN
feriados: en semana con feriado la publicación real cae un día después
y el dato se consideraría visible un día antes de tiempo; por eso el
calendario del archivo tiene prioridad y `grabar` recuerda completarlo).

COLUMNAS:
    short_interest_acciones     posición corta reportada | None (el símbolo
                                no aparece en el reporte) | FALTANTE
    short_interest_dtc          días para cubrir (FINRA) | None | FALTANTE
    short_interest_cambio_pct   cambio vs el reporte previo, en % | None | FALTANTE
    short_interest_corte        fecha de corte (ISO) | FALTANTE
    short_interest_publicado    fecha de publicación (ISO) | FALTANTE
    short_interest_dias         días desde la publicación | FALTANTE
    short_interest_pct_float    posición / float de Yahoo | FALTANTE (sin float)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from fuentes import __main__ as cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import FALTANTE, ErrorFuente, numero
from fuentes.http import Cliente, Limitador
from fuentes.tiempo import dias_habiles_despues, leer_fecha, ny

log = logging.getLogger("fuentes.finra_short")

URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
LIMITE = 5000
MAX_PAGINAS = 20
DIAS_HABILES_PUBLICACION = 8
HORA_PUBLICACION = time(16, 0)
RUTA_CALENDARIO = Path(__file__).resolve().parent / "datos" / "finra_calendario.json"


@dataclass(frozen=True)
class Reporte:
    corte: date
    publicado: date
    acciones: float
    dtc: float | None
    cambio_pct: float | None


def cliente_finra(transport=None, transport_post=None, dormir=None) -> Cliente:
    kw = {"dormir": dormir} if dormir is not None else {}
    return Cliente("finra", "hernan-portafolio fuentes", limitador=Limitador(10, 60.0), transport=transport,
                   transport_post=transport_post, headers={"Accept": "text/csv"}, **kw)


def cortes_entre(desde: date, hasta: date) -> list[date]:
    """Los cortes (15 y fin de mes, corridos al día hábil previo) entre dos fechas."""
    out = []
    d = date(desde.year, desde.month, 1)
    while d <= hasta:
        fin_mes = (d + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        for c in (d.replace(day=15), fin_mes):
            while c.weekday() >= 5:
                c -= timedelta(days=1)
            if desde <= c <= hasta and c not in out:
                out.append(c)
        d = (d + timedelta(days=32)).replace(day=1)
    return sorted(out)


def cargar_calendario(ruta: Path = RUTA_CALENDARIO) -> dict[date, date]:
    if not ruta.exists():
        return {}
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    out = {}
    for corte, pub in doc.get("publicacion", {}).items():
        c, p = leer_fecha(corte), leer_fecha(pub)
        if c and p:
            out[c] = p
    return out


def publicacion_de(corte: date, calendario: dict[date, date]) -> date:
    return calendario.get(corte) or dias_habiles_despues(corte, DIAS_HABILES_PUBLICACION)


def leer_csv(texto: str, corte: date) -> dict[str, tuple[float, float | None, float | None]]:
    """símbolo -> (posición, días para cubrir, cambio %). Una fila sin
    posición legible se descarta; si el CSV no tiene las columnas, levanta."""
    lector = csv.DictReader(io.StringIO(texto))
    if not lector.fieldnames or "symbolCode" not in lector.fieldnames or "currentShortPositionQuantity" not in lector.fieldnames:
        raise ErrorFuente("cuerpo", "finra")
    out = {}
    for fila in lector:
        f = leer_fecha(fila.get("settlementDate"))
        if f is not None and f != corte:
            continue
        sim = (fila.get("symbolCode") or "").strip().upper()
        pos = _num(fila.get("currentShortPositionQuantity"))
        if not sim or pos is None:
            continue
        out[sim] = (pos, _num(fila.get("daysToCoverQuantity")), _num(fila.get("changePercent")))
    return out


def _num(texto: object) -> float | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    try:
        return numero(float(texto.replace(",", "")))
    except ValueError:
        return None


class ShortInterestFinra:
    nombre = "finra_short"
    _NOMBRES = ["short_interest_acciones", "short_interest_dtc", "short_interest_cambio_pct", "short_interest_corte",
                "short_interest_publicado", "short_interest_dias", "short_interest_pct_float"]

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None,
                 float_yahoo: Callable[[str], object] | None = None, calendario: dict[date, date] | None = None) -> None:
        self.cache = cache or Cache()
        self._cliente = cliente
        self._float = float_yahoo or (lambda t: FALTANTE)
        self.calendario = calendario if calendario is not None else cargar_calendario()
        self._cortes: dict[date, dict | None] = {}

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def cliente(self) -> Cliente:
        if self._cliente is None:
            self._cliente = cliente_finra()
        return self._cliente

    def reporte_del_corte(self, corte: date) -> dict | None:
        if corte in self._cortes:
            return self._cortes[corte]
        clave = f"{URL}?settlementDate={corte.isoformat()}"

        def pedir():
            textos, offset = [], 0
            for _ in range(MAX_PAGINAS):
                cuerpo = {"limit": LIMITE, "offset": offset, "compareFilters": [
                    {"fieldName": "settlementDate", "compareType": "EQUAL", "fieldValue": corte.isoformat()}]}
                r = self.cliente().post(URL, cuerpo)
                filas = leer_csv(r.texto, corte)
                textos.append(r.texto)
                if len(filas) < LIMITE:
                    return textos
                offset += LIMITE
            raise ErrorFuente("paginacion", "finra")

        try:
            textos = self.cache.obtener("finra_short", clave, pedir)
            out: dict = {}
            for t in textos:
                out.update(leer_csv(t, corte))
            self._cortes[corte] = out
        except ErrorFuente as ex:
            log.warning("finra_short: %s (corte %s)", ex.codigo, corte)
            self._cortes[corte] = None
        return self._cortes[corte]

    def ultimo_publicado(self, momento: datetime) -> tuple[date, date] | None:
        """(corte, publicación) del último reporte visible en el instante."""
        dia = momento.date()
        candidatos = cortes_entre(dia - timedelta(days=60), dia)
        visibles = [(c, publicacion_de(c, self.calendario)) for c in candidatos]
        visibles = [(c, p) for c, p in visibles if ny(p, HORA_PUBLICACION) <= momento]
        return max(visibles, key=lambda cp: cp[1]) if visibles else None

    def columnas(self, ticker: str, momento: datetime) -> dict:
        cp = self.ultimo_publicado(momento)
        if cp is None:
            return todas_faltantes(self._NOMBRES)
        corte, publicado = cp
        rep = self.reporte_del_corte(corte)
        if rep is None:
            return todas_faltantes(self._NOMBRES)
        fila = rep.get(ticker.upper().replace("-", "."))
        out = {"short_interest_acciones": None, "short_interest_dtc": None, "short_interest_cambio_pct": None,
               "short_interest_corte": corte.isoformat(), "short_interest_publicado": publicado.isoformat(),
               "short_interest_dias": (momento.date() - publicado).days, "short_interest_pct_float": FALTANTE}
        if fila is None:
            return out
        pos, dtc, cambio = fila
        out.update({"short_interest_acciones": pos, "short_interest_dtc": dtc, "short_interest_cambio_pct": cambio})
        fl = numero(self._float(ticker))
        if fl is not None and fl > 0:
            out["short_interest_pct_float"] = round(pos / fl, 4)
        return out


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar finra_short AAAA-MM-DD`: guarda la primera
    página real de ese corte."""
    from fuentes.grabar import guardar

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar finra_short")
    ap.add_argument("corte", type=date.fromisoformat)
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    cuerpo = {"limit": LIMITE, "offset": 0, "compareFilters": [
        {"fieldName": "settlementDate", "compareType": "EQUAL", "fieldValue": args.corte.isoformat()}]}
    r = cliente_finra().post(URL, cuerpo)
    guardar("finra_short", f"corte_{args.corte:%Y%m%d}", URL, cuerpo, r.status, r.headers, r.texto, ficticio=False,
            directorio=args.dir, metodo="POST")
    filas = leer_csv(r.texto, args.corte)
    cal = cargar_calendario()
    print(f"{args.corte}: {len(filas)} símbolos; publicación estimada {publicacion_de(args.corte, cal)}"
          f" ({'del calendario' if args.corte in cal else 'corte + 8 días hábiles; anotar la real en finra_calendario.json'})")
    return 0


cli.registrar("finra_short", _grabar)
