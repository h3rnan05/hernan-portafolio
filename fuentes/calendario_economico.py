"""Calendario económico (Fed, BLS, BEA) como columnas del backtest v2.

Cuatro anuncios que mueven el índice y con él la señal `indice_sobre_vwap`:
    FOMC     comunicado de la Fed, 14:00 NY, último día de la reunión.
             `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm`
    CPI      BLS, 08:30 NY. `https://www.bls.gov/schedule/news_release/cpi.htm`
    NOMINAS  BLS Employment Situation, 08:30 NY.
             `https://www.bls.gov/schedule/news_release/empsit.htm`
    PIB      BEA, 08:30 NY. `https://www.bea.gov/news/schedule/full` (el
             año completo; `/news/schedule` solo muestra lo que falta).

USER-AGENT. BLS responde 403 a cualquier UA sin correo real (probado el
2026-09-28: el texto "contacto en …" no basta; uno de navegador tampoco).
Se usa `FUENTES_USER_AGENT` o, si no está, `FUENTES_SEC_USER_AGENT`
("nombre correo@dominio"); sin ninguno de los dos no se descarga nada y
queda solo el archivo.

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
Las páginas oficiales muestran el año en curso y la cola del anterior;
para 2025 (la mitad del backtest) hacen falta las tablas archivadas, y
mientras no se carguen, 2025 queda sin cobertura (FALTANTE), no
estimado.

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

from fuentes import cli
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
    "PIB": "https://www.bea.gov/news/schedule/full",
}
ENV_USER_AGENT = "FUENTES_USER_AGENT"
ENV_USER_AGENT_SEC = "FUENTES_SEC_USER_AGENT"
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


def user_agent_configurado() -> str:
    """Nombre + correo real. BLS bloquea lo demás."""
    import os
    for var in (ENV_USER_AGENT, ENV_USER_AGENT_SEC):
        ua = os.environ.get(var, "").strip()
        if ua and "@" in ua:
            return ua
    raise ErrorFuente("sin_user_agent", "calendario_economico")


def cliente_calendario(transport=None, dormir=None) -> Cliente:
    kw = {"dormir": dormir} if dormir is not None else {}
    return Cliente("calendario_economico", user_agent_configurado(), limitador=Limitador(2, 1.0, **kw),
                   transport=transport, **kw)


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
_TABLA_BLS = re.compile(r'<table class="release-list">(.*?)</table>', re.S)
_FILA_BLS = re.compile(r"<tr[^>]*>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>", re.S)


def _fecha_larga(texto: str) -> date | None:
    m = _FECHA_LARGA.search(texto)
    if not m:
        return None
    mes = _mes(m.group(1))
    if mes is None:
        return None
    try:
        return date(int(m.group(3)), mes, int(m.group(2)))
    except ValueError:
        return None


def parsear_bls(html: str, tipo: str) -> tuple[list[Anuncio], set[int]]:
    """Solo las filas de `table.release-list` (Reference Month | Release
    Date | Release Time). La página trae además un <script> con fechas de
    OTRAS publicaciones (PPI, productividad...): recorrer el HTML entero
    las mezclaba (visto el 2026-09-28)."""
    out, anios = [], set()
    tabla = _TABLA_BLS.search(html)
    if not tabla:
        return [], set()
    for _ref, fecha_txt, hora_txt in _FILA_BLS.findall(tabla.group(1)):
        f = _fecha_larga(fecha_txt)
        if f is None:
            continue
        anios.add(f.year)
        out.append(Anuncio(tipo, f, _hora_de(hora_txt, HORA[tipo])))
    return sorted(set(out), key=lambda a: a.fecha), anios


def _hora_de(texto: str, default: time) -> time:
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(AM|PM)", texto, re.I)
    if not m:
        return default
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return time(h, int(m.group(2)))


_ANIO_BEA = re.compile(r"<th[^>]*>\s*Year (\d{4})")
_FILA_BEA = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_FECHA_BEA = re.compile(r'class="release-date">\s*([A-Za-z]+)\s+(\d{1,2})\s*<')
_TITULO_BEA = re.compile(r'class="release-title[^"]*"[^>]*>\s*([^<]*?)\s*<')
_TITULOS_PIB = ("GDP (", "Gross Domestic Product,")


def parsear_bea(html: str) -> tuple[list[Anuncio], set[int]]:
    """`/news/schedule/full`: el año está en el encabezado ("Year 2026"),
    cada fila trae `div.release-date` sin año ("October 29") y un título.
    Cuenta como PIB un título que empieza con "GDP (" o "Gross Domestic
    Product," (las estimaciones trimestrales, incluida la "Updated" de
    enero); "GDP by County/State/Industry" no es la publicación del PIB."""
    m_anio = _ANIO_BEA.search(html)
    if not m_anio:
        return [], set()
    anio = int(m_anio.group(1))
    out = []
    for fila in _FILA_BEA.findall(html):
        t = _TITULO_BEA.search(fila)
        if not t or not t.group(1).startswith(_TITULOS_PIB):
            continue
        d = _FECHA_BEA.search(fila)
        if not d or _mes(d.group(1)) is None:
            continue
        try:
            f = date(anio, _mes(d.group(1)), int(d.group(2)))
        except ValueError:
            continue
        hora = re.search(r'text-muted">\s*([^<]*?)\s*<', fila)
        out.append(Anuncio("PIB", f, _hora_de(hora.group(1) if hora else "", HORA["PIB"])))
    return sorted(set(out), key=lambda a: a.fecha), ({anio} if out else set())


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
        try:
            _, html = grabar_get(cliente, "calendario_economico", tipo.lower(), url, directorio=args.dir)
        except ErrorFuente as ex:
            print(f"{tipo}: FUENTE CAÍDA ({ex.codigo})")
            rc = 3
            continue
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
