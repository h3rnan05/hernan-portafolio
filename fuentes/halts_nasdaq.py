"""Halts de Nasdaq Trader con código de motivo, como columnas del backtest v2.

Fuente: `https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts&haltdate=MM/DD/YYYY`
(RSS con namespace `ndaq`). Sin credenciales. Nasdaq pide como máximo
UNA consulta por minuto: el limitador es 1/60 s y por eso la corrida
histórica se hace UNA sola vez, día por día, y queda en caché para
siempre (un día pasado no cambia). El día en curso se recachea cada
5 min. Sin caché caliente, un backtest de 250 sesiones tarda >4 h solo
en esto: `python -m fuentes grabar halts_nasdaq --precargar DESDE HASTA`
lo deja hecho desde el VPS.

Campos que se leen de cada `<item>`: IssueSymbol, ReasonCode, HaltDate +
HaltTime, ResumptionDate + ResumptionTradeTime (hora de Nueva York).
Un halt sin hora de reanudación sigue abierto hasta que la fuente diga
otra cosa.

Códigos que importan al backtest (los demás se cuentan en `otros`):
    T1    noticia pendiente (halt regulatorio de Nasdaq)
    LUDP  pausa por volatilidad (Limit Up-Limit Down)
    T2, T5, T6, T8, T12, H4, H9, H10, H11, M, LUDS, MWC*: otros.

COLUMNAS (para el día NY del instante, contando solo halts que
EMPEZARON antes del instante):
    halts_t1_dia          cuántos T1 | FALTANTE
    halts_ludp_dia        cuántos LUDP | FALTANTE
    halts_otros_dia       otros códigos | FALTANTE
    halt_activo           True si en el instante el símbolo está detenido
                          | False | FALTANTE
    halt_ultimo_motivo    código del último halt del día antes del instante | None | FALTANTE
    halt_reanudado_hace_min minutos desde la última reanudación antes del
                          instante | None (ninguna hoy) | FALTANTE
Un día sin ítems para el símbolo, con el RSS leído completo, es 0/False:
eso sí es "no hubo halt", afirmado con la lista del día.
"""

from __future__ import annotations

import argparse
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fuentes import __main__ as cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import ErrorFuente
from fuentes.http import Cliente, Limitador
from fuentes.tiempo import NY, fecha_ny, ny

log = logging.getLogger("fuentes.halts_nasdaq")

URL = "https://www.nasdaqtrader.com/rss.aspx"
NS = "http://www.nasdaqtrader.com/"
EDAD_HOY_S = 300.0
CODIGOS_T1 = {"T1"}
CODIGOS_LUDP = {"LUDP"}


@dataclass(frozen=True)
class Halt:
    simbolo: str
    motivo: str
    inicio: datetime               # UTC
    reanudacion: datetime | None   # UTC; None = sin reanudación publicada


def cliente_nasdaq(transport=None, dormir=None) -> Cliente:
    kw = {"dormir": dormir} if dormir is not None else {}
    return Cliente("halts_nasdaq", "hernan-portafolio fuentes", limitador=Limitador(1, 60.0, **kw),
                   transport=transport, **kw)


def _fecha_hora(fecha: str | None, hora: str | None) -> datetime | None:
    if not fecha or not hora:
        return None
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", fecha)
    h = re.match(r"\s*(\d{1,2}):(\d{2}):(\d{2})\s*$", hora)
    if not m or not h:
        return None
    try:
        d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        return ny(d, time(int(h.group(1)), int(h.group(2)), int(h.group(3))))
    except ValueError:
        return None


def leer_rss(xml: str, dia: date) -> list[Halt]:
    """Todos los halts del RSS con inicio ese día NY. Un ítem sin símbolo,
    sin código o sin hora de inicio invalida el archivo (levanta): no se
    sabe qué se está omitiendo."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError:
        raise ErrorFuente("xml_ilegible", "halts_nasdaq") from None
    items = raiz.findall(".//item")
    if raiz.tag != "rss":
        raise ErrorFuente("xml_ilegible", "halts_nasdaq")
    out = []
    for it in items:
        def campo(nombre: str) -> str | None:
            e = it.find(f"{{{NS}}}{nombre}")
            return e.text.strip() if e is not None and e.text else None
        sim, motivo = campo("IssueSymbol"), campo("ReasonCode")
        inicio = _fecha_hora(campo("HaltDate"), campo("HaltTime"))
        if not sim or not motivo or inicio is None:
            raise ErrorFuente("item_ilegible", "halts_nasdaq")
        if fecha_ny(inicio) != dia:
            continue
        rean = _fecha_hora(campo("ResumptionDate"), campo("ResumptionTradeTime"))
        out.append(Halt(sim.upper(), motivo.upper(), inicio, rean))
    return out


class HaltsNasdaq:
    nombre = "halts_nasdaq"
    _NOMBRES = ["halts_t1_dia", "halts_ludp_dia", "halts_otros_dia", "halt_activo", "halt_ultimo_motivo",
                "halt_reanudado_hace_min"]

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None, hoy: date | None = None) -> None:
        self.cache = cache or Cache()
        self._cliente = cliente
        self._hoy = hoy
        self._dias: dict[date, list[Halt] | None] = {}

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def cliente(self) -> Cliente:
        if self._cliente is None:
            self._cliente = cliente_nasdaq()
        return self._cliente

    def halts_del_dia(self, dia: date) -> list[Halt] | None:
        if dia in self._dias:
            return self._dias[dia]
        params = {"feed": "tradehalts", "haltdate": f"{dia:%m/%d/%Y}"}
        clave = f"{URL}?feed=tradehalts&haltdate={dia.isoformat()}"
        hoy = self._hoy or fecha_ny(datetime.now(NY))
        edad = EDAD_HOY_S if dia >= hoy else None
        try:
            xml = self.cache.obtener("halts_nasdaq", clave, lambda: self.cliente().get(URL, params).texto, edad)
            self._dias[dia] = leer_rss(xml, dia)
        except ErrorFuente as ex:
            log.warning("halts_nasdaq: %s (%s)", ex.codigo, dia)
            self._dias[dia] = None
        return self._dias[dia]

    def precargar(self, desde: date, hasta: date) -> int:
        """Corrida histórica: un pedido por día hábil (1/min). Devuelve
        cuántos días quedaron sin dato."""
        sin = 0
        d = desde
        while d <= hasta:
            if d.weekday() < 5 and self.halts_del_dia(d) is None:
                sin += 1
            d += timedelta(days=1)
        return sin

    def columnas(self, ticker: str, momento: datetime) -> dict:
        dia = fecha_ny(momento)
        halts = self.halts_del_dia(dia)
        if halts is None:
            return todas_faltantes(self._NOMBRES)
        sim = ticker.upper().replace("-", ".")
        propios = sorted([h for h in halts if h.simbolo == sim and h.inicio <= momento], key=lambda h: h.inicio)
        t1 = sum(1 for h in propios if h.motivo in CODIGOS_T1)
        ludp = sum(1 for h in propios if h.motivo in CODIGOS_LUDP)
        activo = any(h.reanudacion is None or h.reanudacion > momento for h in propios)
        reanudados = [h.reanudacion for h in propios if h.reanudacion is not None and h.reanudacion <= momento]
        hace = round((momento - max(reanudados)).total_seconds() / 60.0, 1) if reanudados else None
        return {"halts_t1_dia": t1, "halts_ludp_dia": ludp, "halts_otros_dia": len(propios) - t1 - ludp,
                "halt_activo": activo, "halt_ultimo_motivo": propios[-1].motivo if propios else None,
                "halt_reanudado_hace_min": hace}


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar halts_nasdaq AAAA-MM-DD` guarda el RSS de
    un día; `--precargar DESDE HASTA` llena la caché (1 pedido/min)."""
    from fuentes.grabar import guardar

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar halts_nasdaq")
    ap.add_argument("dia", nargs="?", type=date.fromisoformat)
    ap.add_argument("--precargar", nargs=2, type=date.fromisoformat, metavar=("DESDE", "HASTA"))
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.precargar:
        f = HaltsNasdaq()
        sin = f.precargar(*args.precargar)
        print(f"precarga {args.precargar[0]}..{args.precargar[1]}: {sin} días sin dato")
        return 0 if sin == 0 else 3
    if args.dia is None:
        ap.error("falta el día o --precargar")
    params = {"feed": "tradehalts", "haltdate": f"{args.dia:%m/%d/%Y}"}
    r = cliente_nasdaq().get(URL, params)
    guardar("halts_nasdaq", f"halts_{args.dia:%Y%m%d}", URL, params, r.status, r.headers, r.texto, ficticio=False,
            directorio=args.dir)
    halts = leer_rss(r.texto, args.dia)
    print(f"{args.dia}: {len(halts)} halts; T1={sum(h.motivo == 'T1' for h in halts)} LUDP={sum(h.motivo == 'LUDP' for h in halts)}")
    return 0


cli.registrar("halts_nasdaq", _grabar)
