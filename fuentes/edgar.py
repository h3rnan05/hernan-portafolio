"""EDGAR (SEC): presentaciones por emisor y el 8-K como columna de nivel.

Fuente pública y gratis. Dos endpoints:

  - `https://www.sec.gov/files/company_tickers.json`: ticker -> CIK.
  - `https://data.sec.gov/submissions/CIK##########.json`: las últimas
    ~1.000 presentaciones (`filings.recent`, columnar) y, si hay más,
    `filings.files[]` con archivos `CIK##########-submissions-NNN.json`
    del mismo formato. Para un año de backtest hay que leerlos todos.

Reglas de la SEC que se cumplen aquí: `User-Agent` con nombre y correo
de contacto (`FUENTES_SEC_USER_AGENT`, obligatorio: sin él no se pide
nada) y como máximo 10 pedidos por segundo (se usan 8).

ZONA HORARIA. `acceptanceDateTime` viene como `2026-07-30T20:30:28.000Z`
y la `Z` ES UTC de verdad: el índice de sec.gov de ese filing dice
"Accepted 2026-07-30 16:30:28" (ET). Verificado en el VPS el 2026-09-28
contra NTLA, AAPL, TSLA y MRNA (la primera versión de este módulo lo leía
como hora de NY y desplazaba cada aceptación +4 h; corregido). El
comando `grabar` sigue comprobándolo con las presentaciones reales: un
filing aceptado después de las 17:30 ET lleva `filingDate` del día hábil
siguiente; se prueban las dos hipótesis (Z = UTC, Z = NY) sobre los
filings donde predicen fechas distintas, y solo si la lectura UTC
explica el `filingDate` casi siempre imprime "ZONA OK".

NIVEL POR ÍTEM (decisión 2026-09-28). Solo dice cuán fuerte es el hecho,
no su dirección: la dirección la decide la clasificación de la IA sobre
el titular, no el ítem.
    1: 2.02 (resultados), 1.01 (acuerdo material)
    2: 8.01 (otros eventos), 7.01 (Reg FD), 2.01 (adquisición/venta)
    sin nivel: 5.02, 5.03, 5.07 y un 9.01 solo (anexos sin hecho).
Un 8-K con varios ítems toma el mejor nivel (1 < 2).

COLUMNAS (ventana: las 24 h anteriores al instante, mismas horas que el
catalizador de la v2; nada aceptado DESPUÉS del instante entra):
    edgar_8k_nivel        1 | 2 | None (hubo 8-K sin nivel o ninguno) | FALTANTE
    edgar_8k_items        "2.02,9.01" del 8-K elegido | None | FALTANTE
    edgar_8k_horas        horas desde la aceptación del elegido | None | FALTANTE
    edgar_8k_cantidad_24h cuántos 8-K en la ventana | FALTANTE
FALTANTE cuando: sin User-Agent, el ticker no está en el mapa de la SEC,
la descarga falló, o alguna aceptación de la ventana es ilegible.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fuentes import cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import FALTANTE, ErrorFuente
from fuentes.grabar import grabar_get
from fuentes.http import Cliente, Limitador
from fuentes.tiempo import NY, fmt_utc_mty, leer_fecha, leer_iso_utc

log = logging.getLogger("fuentes.edgar")

ENV_USER_AGENT = "FUENTES_SEC_USER_AGENT"
URL_TICKERS = "https://www.sec.gov/files/company_tickers.json"
URL_SUBMISSIONS = "https://data.sec.gov/submissions/"
EDAD_TICKERS_S = 24 * 3600.0
EDAD_SUBMISSIONS_S = 24 * 3600.0
HORAS_VENTANA = 24
CORTE_SEC = (17, 30)   # después de esta hora ET la presentación se fecha al día hábil siguiente

NIVEL_POR_ITEM = {"2.02": 1, "1.01": 1, "8.01": 2, "7.01": 2, "2.01": 2}
ITEMS_SIN_NIVEL = {"5.02", "5.03", "5.07", "9.01"}


@dataclass(frozen=True)
class Presentacion:
    form: str
    fecha: date                     # filingDate
    aceptada: datetime | None       # UTC; None si el campo era ilegible
    items: tuple[str, ...]
    accession: str
    documento: str


# ------------------------------------------------------------- cliente


def user_agent_configurado() -> str:
    ua = os.environ.get(ENV_USER_AGENT, "").strip()
    if not ua or "@" not in ua:
        # La SEC bloquea sin contacto; y sin contacto tampoco se pide.
        raise ErrorFuente("sin_user_agent", "edgar")
    return ua


def cliente_edgar(transport=None, dormir=None) -> Cliente:
    kw = {"dormir": dormir} if dormir is not None else {}
    return Cliente("edgar", user_agent_configurado(), limitador=Limitador(8, 1.0), transport=transport,
                   headers={"Accept-Encoding": "gzip, deflate"}, **kw)


def leer_aceptacion(texto: object) -> datetime | None:
    """`2026-07-30T20:30:28.000Z` es UTC (la Z es real) -> aware en UTC.
    Sin zona o ilegible -> None: no se adivina."""
    return leer_iso_utc(texto)


def _cik_de(valor: object) -> int | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if valor > 0 else None
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    return None


def mapa_tickers(cliente: Cliente, cache: Cache) -> dict[str, int]:
    def pedir():
        cuerpo = cliente.get_json(URL_TICKERS)
        if not isinstance(cuerpo, dict) or not cuerpo:
            raise ErrorFuente("cuerpo", "edgar")
        out: dict[str, int] = {}
        for fila in cuerpo.values():
            if not isinstance(fila, dict):
                continue
            cik, t = _cik_de(fila.get("cik_str")), fila.get("ticker")
            if cik is not None and isinstance(t, str) and t.strip():
                out[t.strip().upper()] = cik
        if not out:
            raise ErrorFuente("cuerpo", "edgar")
        return out
    return cache.obtener("edgar_tickers", URL_TICKERS, pedir, EDAD_TICKERS_S)


def _presentaciones_de(bloque: dict) -> list[Presentacion]:
    columnas = ("form", "filingDate", "acceptanceDateTime", "items", "accessionNumber", "primaryDocument")
    series = [bloque.get(c) for c in columnas]
    if any(not isinstance(s, list) for s in series):
        raise ErrorFuente("cuerpo", "edgar")
    n = len(series[0])
    if any(len(s) != n for s in series):
        raise ErrorFuente("cuerpo", "edgar")
    out = []
    for i in range(n):
        form, fecha, acc, items, num, doc = (s[i] for s in series)
        f = leer_fecha(fecha)
        if not isinstance(form, str) or f is None:
            continue
        lista = tuple(x.strip() for x in items.split(",") if x.strip()) if isinstance(items, str) else ()
        out.append(Presentacion(form.strip(), f, leer_aceptacion(acc), lista,
                                num if isinstance(num, str) else "", doc if isinstance(doc, str) else ""))
    return out


def submissions(cliente: Cliente, cache: Cache, cik: int, desde: date | None = None) -> list[Presentacion]:
    """Presentaciones del emisor: `filings.recent` más los archivos extra
    que cubran fechas >= `desde` (sin `desde`, todos). Un emisor grande
    trae archivos que terminan en 2015: para un backtest de un año no
    hace falta pedirlos."""
    url = f"{URL_SUBMISSIONS}CIK{cik:010d}.json"

    def pedir():
        cuerpo = cliente.get_json(url)
        filings = cuerpo.get("filings") if isinstance(cuerpo, dict) else None
        if not isinstance(filings, dict) or not isinstance(filings.get("recent"), dict):
            raise ErrorFuente("cuerpo", "edgar")
        bloques = [filings["recent"]]
        for extra in filings.get("files") or []:
            nombre = extra.get("name") if isinstance(extra, dict) else None
            if not isinstance(nombre, str) or not nombre.endswith(".json") or "/" in nombre:
                raise ErrorFuente("cuerpo", "edgar")
            hasta_extra = leer_fecha(extra.get("filingTo"))
            if desde is not None and hasta_extra is not None and hasta_extra < desde:
                continue
            b = cliente.get_json(URL_SUBMISSIONS + nombre)
            if not isinstance(b, dict):
                raise ErrorFuente("cuerpo", "edgar")
            bloques.append(b)
        return bloques

    clave = url if desde is None else f"{url}?desde={desde.isoformat()}"
    bloques = cache.obtener("edgar_submissions", clave, pedir, EDAD_SUBMISSIONS_S)
    out: list[Presentacion] = []
    for b in bloques:
        out.extend(_presentaciones_de(b))
    return out


# --------------------------------------------------------------- nivel


def nivel_de_items(items: tuple[str, ...] | list[str]) -> int | None:
    niveles = [NIVEL_POR_ITEM[i] for i in items if i in NIVEL_POR_ITEM]
    return min(niveles) if niveles else None


def es_8k(p: Presentacion) -> bool:
    return p.form.upper() in ("8-K", "8-K/A")


def ochok_en_ventana(pres: list[Presentacion], momento: datetime, horas: int = HORAS_VENTANA) -> list[Presentacion]:
    """8-K aceptados en (momento - horas, momento]. Levanta ErrorFuente
    si alguno de esa vecindad (por fecha) tiene aceptación ilegible: sin
    hora no se sabe si entra o no, y adivinar es lo prohibido."""
    desde = momento - timedelta(hours=horas)
    out = []
    for p in pres:
        if not es_8k(p):
            continue
        if p.aceptada is None:
            if desde.date() - timedelta(days=2) <= p.fecha <= momento.date() + timedelta(days=1):
                raise ErrorFuente("aceptacion_ilegible", "edgar")
            continue
        if desde < p.aceptada <= momento:
            out.append(p)
    return sorted(out, key=lambda p: p.aceptada, reverse=True)


# --------------------------------------------------------------- fuente


class LectorEdgar:
    """Mapa ticker→CIK y presentaciones por emisor, compartido por las
    columnas EDGAR (8-K, veto, Form 4, acciones). Un fallo de descarga se
    recuerda en el proceso para no martillar a la SEC."""

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None,
                 desde: date | None = None) -> None:
        self.cache = cache or Cache()
        self._cliente = cliente
        # Hasta dónde atrás hacen falta presentaciones (archivos extra de
        # submissions). Default: 400 días, un backtest de 12 meses.
        self.desde = desde if desde is not None else datetime.now(UTC).date() - timedelta(days=400)
        self._mapa: dict[str, int] | None = None
        self._pres: dict[int, list[Presentacion] | None] = {}

    def cliente(self) -> Cliente:
        if self._cliente is None:
            self._cliente = cliente_edgar()
        return self._cliente

    def cik(self, ticker: str) -> int | None:
        if self._mapa is None:
            self._mapa = mapa_tickers(self.cliente(), self.cache)
        return self._mapa.get(ticker.upper().replace("-", "").replace(".", ""))  # BRK-B/BRK.B -> BRKB

    def presentaciones(self, ticker: str) -> list[Presentacion] | None:
        """None = no se pudo (sin CIK, sin User-Agent o descarga caída)."""
        try:
            cik = self.cik(ticker)
        except ErrorFuente as ex:
            log.warning("edgar: %s (%s)", ex.codigo, ticker)
            return None
        if cik is None:
            log.info("edgar: %s no está en el mapa de la SEC", ticker)
            return None
        if cik not in self._pres:
            try:
                self._pres[cik] = submissions(self.cliente(), self.cache, cik, self.desde)
            except ErrorFuente as ex:
                log.warning("edgar: submissions de %s falló (%s)", ticker, ex.codigo)
                self._pres[cik] = None
        return self._pres[cik]


class Edgar8K:
    nombre = "edgar_8k"
    _NOMBRES = ["edgar_8k_nivel", "edgar_8k_items", "edgar_8k_horas", "edgar_8k_cantidad_24h"]

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None,
                 lector: LectorEdgar | None = None) -> None:
        self.lector = lector or LectorEdgar(cache, cliente)

    @property
    def cache(self) -> Cache:
        return self.lector.cache

    def cliente(self) -> Cliente:
        return self.lector.cliente()

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def columnas(self, ticker: str, momento: datetime) -> dict:
        pres = self.lector.presentaciones(ticker)
        if pres is None:
            return todas_faltantes(self._NOMBRES)
        try:
            en_ventana = ochok_en_ventana(pres, momento)
        except ErrorFuente:
            return todas_faltantes(self._NOMBRES)
        if not en_ventana:
            return {"edgar_8k_nivel": None, "edgar_8k_items": None, "edgar_8k_horas": None,
                    "edgar_8k_cantidad_24h": 0}
        con_nivel = [(nivel_de_items(p.items), p) for p in en_ventana]
        operables = [(n, p) for n, p in con_nivel if n is not None]
        # El mejor nivel; a igual nivel, el más reciente (la lista ya viene así).
        nivel, elegido = min(operables, key=lambda x: x[0]) if operables else (None, en_ventana[0])
        return {
            "edgar_8k_nivel": nivel,
            "edgar_8k_items": ",".join(elegido.items) if elegido.items else None,
            "edgar_8k_horas": round((momento - elegido.aceptada).total_seconds() / 3600.0, 2),
            "edgar_8k_cantidad_24h": len(en_ventana),
        }


# --------------------------------------------------------------- grabar


def _fecha_presentacion_esperada(aceptada_et: datetime) -> date:
    """La SEC fecha al día hábil siguiente lo aceptado después de las
    17:30 ET (fines de semana sí; feriados no se descuentan: ruido chico)."""
    d = aceptada_et.date()
    if (aceptada_et.hour, aceptada_et.minute) >= CORTE_SEC:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def verificar_zona(recent: dict) -> tuple[str, dict]:
    """Evidencia con presentaciones reales de que la Z es UTC.

    Sobre las filas crudas de `filings.recent` (por índice, sin descartar
    ninguna para no desalinear), se prueban las dos hipótesis: leer la
    hora como UTC o como NY. Cada una predice un `filingDate`; solo
    cuentan las filas donde las predicciones difieren. "ZONA OK" si la
    lectura UTC (la del código) acierta en >= 95 % de esas filas y más
    que la lectura NY; si no, "ZONA A REVISAR"."""
    fechas, crudos = recent.get("filingDate") or [], recent.get("acceptanceDateTime") or []
    utc_ok = ny_ok = discriminantes = 0
    for fecha_txt, crudo in zip(fechas, crudos):
        fecha = leer_fecha(fecha_txt)
        if fecha is None or not isinstance(crudo, str) or "T" not in crudo:
            continue
        como_utc = leer_iso_utc(crudo)
        if como_utc is None:
            continue
        naive = como_utc.replace(tzinfo=None)
        pred_utc = _fecha_presentacion_esperada(como_utc.astimezone(NY))
        pred_ny = _fecha_presentacion_esperada(naive.replace(tzinfo=NY))
        if pred_utc == pred_ny:
            continue
        discriminantes += 1
        utc_ok += pred_utc == fecha
        ny_ok += pred_ny == fecha
    detalle = {"discriminantes": discriminantes, "consistentes_utc": utc_ok, "consistentes_ny": ny_ok}
    if discriminantes and utc_ok > ny_ok and utc_ok >= 0.95 * discriminantes:
        return "ZONA OK", detalle
    return "ZONA A REVISAR", detalle


def _grabar(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m fuentes grabar edgar")
    ap.add_argument("ticker")
    ap.add_argument("--dir", type=Path, default=None, help="dónde guardar (default fuentes/tests/respuestas)")
    args = ap.parse_args(argv)
    cliente = cliente_edgar()
    cache = Cache(Path(os.environ.get("TMPDIR", "/tmp")) / "fuentes_grabar")
    grabar_get(cliente, "edgar", "company_tickers", URL_TICKERS, directorio=args.dir)
    mapa = mapa_tickers(cliente, cache)
    cik = mapa.get(args.ticker.upper())
    if cik is None:
        print(f"{args.ticker}: no está en company_tickers.json", file=sys.stderr)
        return 1
    url = f"{URL_SUBMISSIONS}CIK{cik:010d}.json"
    _, texto = grabar_get(cliente, "edgar", f"submissions_{args.ticker.upper()}", url, directorio=args.dir)
    import json
    cuerpo = json.loads(texto)
    recent = cuerpo["filings"]["recent"]
    pres = _presentaciones_de(recent)
    veredicto, detalle = verificar_zona(recent)
    print(f"{veredicto} {detalle}")
    for p in [p for p in pres if es_8k(p)][:5]:
        hora = fmt_utc_mty(p.aceptada) if p.aceptada else "aceptación ilegible"
        print(f"  8-K {p.fecha} items={','.join(p.items) or '-'} nivel={nivel_de_items(p.items)} aceptado {hora}")
    return 0 if veredicto == "ZONA OK" else 3


cli.registrar("edgar", _grabar)
