"""Calendario económico (Fed, BLS, BEA) como columnas del backtest v2.

Cuatro anuncios que mueven el índice y con él la señal `indice_sobre_vwap`:
    FOMC     comunicado de la Fed, 14:00 NY, último día de la reunión.
             `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm`
    CPI      BLS, 08:30 NY. `https://www.bls.gov/schedule/news_release/cpi.htm`
    NOMINAS  BLS Employment Situation, 08:30 NY.
             `https://www.bls.gov/schedule/news_release/empsit.htm`
    PIB      BEA, 08:30 NY. `https://www.bea.gov/news/schedule`

Dos orígenes que se SUMAN:
  1. `fuentes/datos/calendario_economico.json`: fechas curadas a mano
     por año, con `cobertura` (qué años cubre cada tipo). Es lo que
     sirve al backtest: las páginas oficiales muestran el año en curso.
  2. Las páginas oficiales, descargadas y cacheadas un día, para el año
     en curso en vivo. Los parsers son tolerantes (regex sobre el HTML)
     y `python -m fuentes grabar calendario_economico` imprime lo que
     leyó para que una persona lo compare con la página.

Un día de un año que ningún origen cubre para un tipo es FALTANTE en
ese tipo: "no hay CPI ese día" solo se afirma con el calendario del año
a la vista. Las fechas del JSON marcadas `verificado: false` cuentan
igual (son la mejor información disponible) pero `grabar` las contrasta.

COLUMNAS:
    eco_fomc_dia, eco_cpi_dia, eco_nominas_dia, eco_pib_dia
                          True | False | FALTANTE
    eco_anuncio_dia       "FOMC" | "CPI,PIB" | None (día sin anuncio, con
                          los cuatro tipos cubiertos) | FALTANTE
    eco_fed_ventana_15min True si es día FOMC y el instante cae a ±15 min
                          de las 14:00 NY | False | FALTANTE
La ventana de 15 min existe SOLO para la Fed (decisión 2026-09-28): CPI,
nóminas y PIB salen antes de la apertura y no cortan la sesión.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fuentes import __main__ as cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import FALTANTE, ErrorFuente
from fuentes.http import Cliente, Limitador
from fuentes.tiempo import NY, fecha_ny, leer_fecha, ny

log = logging.getLogger("fuentes.calendario_economico")

TIPOS = ("FOMC", "CPI", "NOMINAS", "PIB")
URLS = {
    "FOMC": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "CPI": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "NOMINAS": "https://www.bls.gov/schedule/news_release/empsit.htm",
    "PIB": "https://www.bea.gov/news/schedule",
}
HORA = {"FOMC": time(14, 0), "CPI": time(8, 30), "NOMINAS": time(8, 30), "PIB": time(8, 30)}
VENTANA_FED = timedelta(minutes=15)
EDAD_S = 24 * 3600.0
RUTA_ARCHIVO = Path(__file__).resolve().parent / "datos" / "calendario_economico.json"

MESES = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"))}
MESES.update({m[:3]: i for m, i in list(MESES.items())})
MESES["sept"] = 9


@dataclass(frozen=True)
class Anuncio:
    tipo: str
    fecha: date
    hora: time


def cliente_calendario(transport=None, dormir=None) -> Cliente:
    kw = {"dormir": dormir} if dormir is not None else {}
    return Cliente("calendario_economico", "hernan-portafolio fuentes (contacto en FUENTES_SEC_USER_AGENT)",
                   limitador=Limitador(2, 1.0, **kw), transport=transport, **kw)


# ------------------------------------------------------------- parsers


def _mes(texto: str) -> int | None:
    return MESES.get(texto.strip().lower().rstrip("."))


def parsear_fomc(html: str) -> tuple[list[Anuncio], set[int]]:
    """Bloques `fomc-meeting__month` + `fomc-meeting__date` dentro de un
    panel cuyo título trae el año ("2026 FOMC Meetings"). El comunicado
    sale el ÚLTIMO día del rango ("27-28" -> 28; "Jan/Feb" "31-1" -> 1 de
    febrero). Reuniones marcadas como notation vote o unscheduled se
    omiten: no tienen comunicado a las 14:00."""
    out, anios = [], set()
    paneles = re.split(r'(?=(?:\d{4}) FOMC Meetings)', html)
    for panel in paneles:
        m_anio = re.match(r"(\d{4}) FOMC Meetings", panel)
        if not m_anio:
            continue
        anio = int(m_anio.group(1))
        anios.add(anio)
        for mes_txt, dia_txt in re.findall(
                r'fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z/]+)\s*(?:</strong>)?\s*</div>\s*'
                r'<div class="fomc-meeting__date[^>]*>\s*([^<]+?)\s*</div>', panel):
            if re.search(r"notation|unscheduled", dia_txt, re.I):
                continue
            dias = re.findall(r"\d{1,2}", dia_txt)
            if not dias:
                continue
            meses = [_mes(x) for x in mes_txt.split("/")]
            if any(m is None for m in meses):
                continue
            mes = meses[-1] if len(meses) > 1 and len(dias) > 1 else meses[0]
            try:
                out.append(Anuncio("FOMC", date(anio, mes, int(dias[-1])), HORA["FOMC"]))
            except ValueError:
                continue
    return out, anios


_FECHA_LARGA = re.compile(r"([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})")


def parsear_bls(html: str, tipo: str) -> tuple[list[Anuncio], set[int]]:
    """Filas de la tabla de fechas de publicación: 'Oct. 14, 2026' + '08:30 AM'."""
    out, anios = [], set()
    for m in _FECHA_LARGA.finditer(html):
        mes = _mes(m.group(1))
        if mes is None:
            continue
        try:
            f = date(int(m.group(3)), mes, int(m.group(2)))
        except ValueError:
            continue
        anios.add(f.year)
        out.append(Anuncio(tipo, f, HORA[tipo]))
    return sorted(set(out), key=lambda a: a.fecha), anios


def parsear_bea(html: str) -> tuple[list[Anuncio], set[int]]:
    """Filas que nombran 'Gross Domestic Product' con una fecha larga en la misma fila."""
    out, anios = [], set()
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        if "gross domestic product" not in fila.lower():
            continue
        m = _FECHA_LARGA.search(re.sub(r"<[^>]+>", " ", fila))
        if not m or _mes(m.group(1)) is None:
            continue
        try:
            f = date(int(m.group(3)), _mes(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        anios.add(f.year)
        out.append(Anuncio("PIB", f, HORA["PIB"]))
    return sorted(set(out), key=lambda a: a.fecha), anios


PARSERS = {"FOMC": parsear_fomc, "CPI": lambda h: parsear_bls(h, "CPI"), "NOMINAS": lambda h: parsear_bls(h, "NOMINAS"),
           "PIB": parsear_bea}


# ------------------------------------------------------------- archivo


def cargar_archivo(ruta: Path = RUTA_ARCHIVO) -> tuple[list[Anuncio], dict[str, set[int]]]:
    if not ruta.exists():
        return [], {t: set() for t in TIPOS}
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    cobertura = {t: {int(a) for a in doc.get("cobertura", {}).get(t, [])} for t in TIPOS}
    out = []
    for e in doc.get("eventos", []):
        tipo, f = e.get("tipo"), leer_fecha(e.get("fecha"))
        if tipo not in TIPOS or f is None:
            continue
        h = e.get("hora")
        hora = time.fromisoformat(h) if isinstance(h, str) else HORA[tipo]
        out.append(Anuncio(tipo, f, hora))
    return out, cobertura


# -------------------------------------------------------------- fuente


class CalendarioEconomico:
    nombre = "calendario_economico"
    _NOMBRES = ["eco_fomc_dia", "eco_cpi_dia", "eco_nominas_dia", "eco_pib_dia", "eco_anuncio_dia", "eco_fed_ventana_15min"]

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None, ruta_archivo: Path = RUTA_ARCHIVO,
                 descargar: bool = True) -> None:
        self.cache = cache or Cache()
        self._cliente = cliente
        self._descargar = descargar
        self.anuncios, self.cobertura = cargar_archivo(ruta_archivo)
        self._web_cargada = False

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def cliente(self) -> Cliente:
        if self._cliente is None:
            self._cliente = cliente_calendario()
        return self._cliente

    def cargar_web(self) -> None:
        """Una vez por proceso. Un tipo que falla no suma cobertura (queda
        lo del archivo); los demás sí."""
        if self._web_cargada or not self._descargar:
            return
        self._web_cargada = True
        for tipo, url in URLS.items():
            try:
                html = self.cache.obtener("calendario_economico", url, lambda u=url: self.cliente().get(u).texto, EDAD_S)
                anuncios, anios = PARSERS[tipo](html)
            except ErrorFuente as ex:
                log.warning("calendario_economico: %s (%s)", ex.codigo, tipo)
                continue
            if not anuncios:
                log.warning("calendario_economico: la página de %s no dio fechas; no se afirma cobertura", tipo)
                continue
            self.anuncios.extend(anuncios)
            self.cobertura[tipo] |= anios

    def del_dia(self, tipo: str, dia: date) -> object:
        if dia.year not in self.cobertura[tipo]:
            return FALTANTE
        return any(a.tipo == tipo and a.fecha == dia for a in self.anuncios)

    def columnas(self, ticker: str, momento: datetime) -> dict:
        self.cargar_web()
        dia = fecha_ny(momento)
        por_tipo = {t: self.del_dia(t, dia) for t in TIPOS}
        out = {"eco_fomc_dia": por_tipo["FOMC"], "eco_cpi_dia": por_tipo["CPI"], "eco_nominas_dia": por_tipo["NOMINAS"],
               "eco_pib_dia": por_tipo["PIB"]}
        con = [t for t in TIPOS if por_tipo[t] is True]
        if con:
            out["eco_anuncio_dia"] = ",".join(con)
        elif any(v is FALTANTE for v in por_tipo.values()):
            out["eco_anuncio_dia"] = FALTANTE
        else:
            out["eco_anuncio_dia"] = None
        fomc = por_tipo["FOMC"]
        if fomc is FALTANTE:
            out["eco_fed_ventana_15min"] = FALTANTE
        elif fomc:
            hora = next(a.hora for a in self.anuncios if a.tipo == "FOMC" and a.fecha == dia)
            centro = ny(dia, hora)
            out["eco_fed_ventana_15min"] = centro - VENTANA_FED <= momento <= centro + VENTANA_FED
        else:
            out["eco_fed_ventana_15min"] = False
        return out


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar calendario_economico`: guarda las cuatro
    páginas e imprime las fechas leídas para compararlas a ojo con la
    página y con el JSON del repo."""
    from fuentes.grabar import grabar_get

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar calendario_economico")
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    cliente = cliente_calendario()
    archivo, _ = cargar_archivo()
    rc = 0
    for tipo, url in URLS.items():
        _, html = grabar_get(cliente, "calendario_economico", tipo.lower(), url, directorio=args.dir)
        anuncios, anios = PARSERS[tipo](html)
        print(f"{tipo}: {len(anuncios)} fechas, años {sorted(anios)}")
        if not anuncios:
            print(f"  PARSER A REVISAR: la página de {tipo} no dio fechas")
            rc = 3
        web = {a.fecha for a in anuncios}
        for a in archivo:
            if a.tipo == tipo and a.fecha.year in anios and a.fecha not in web:
                print(f"  {tipo} {a.fecha} está en el JSON y NO en la página: revisar")
                rc = 3
        for a in anuncios[:12]:
            print(f"  {a.fecha} {a.hora:%H:%M} NY")
    return rc


cli.registrar("calendario_economico", _grabar)
