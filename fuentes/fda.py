"""Aprobaciones de la FDA (openFDA) como columnas del backtest v2.

Fuente: `https://api.fda.gov/drug/drugsfda.json` con
`search=submissions.submission_status_date:[AAAAMMDD TO AAAAMMDD] AND submissions.submission_status:AP`.
Sin clave: 240 pedidos/min y 1.000/día (se usan 2/s). Se pide por mes
calendario y se cachea el mes entero: un backtest de 12 meses son 12
pedidos (más páginas de 100).

LO QUE ESTA FUENTE NO SABE, y por eso las columnas lo dicen:
  - No hay hora. `submission_status_date` es un día. Si la aprobación
    salió antes o después de la señal no se puede saber desde aquí: la
    columna es "aprobación ESE día", no "aprobación antes de la señal".
  - El sponsor es un nombre de laboratorio, no un ticker. El mapa
    `fuentes/datos/fda_laboratorios.json` lo traduce a mano. Un ticker
    que no está en el mapa, o cuya entrada está marcada `dudosa`
    (subsidiaria, licencia, fusión), vale FALTANTE: no se afirma "sin
    aprobación" de un laboratorio que no se sabe cómo se llama en la FDA.

QUÉ ES UNA "APROBACIÓN" AQUÍ (verificado contra la API el 2026-09-28):
`submission_type` real solo vale `ORIG` (solicitud nueva) o `SUPPL`
(suplemento: etiquetado, manufactura, eficacia...). NDA/ANDA/BLA va en
el PREFIJO de `application_number`. Los suplementos son la gran mayoría
(106 de 125 en la primera página de septiembre 2026) y un `LABELING` de
un genérico no mueve ninguna acción: las columnas de aprobación cuentan
solo `ORIG`; los suplementos del día se exponen aparte para que el
backtest pueda mirarlos si quiere.

COLUMNAS:
    fda_aprobacion_dia      True (aprobación ORIG el día NY del instante)
                            | False | FALTANTE
    fda_aprobacion_vispera  ídem el día hábil anterior (una aprobación
                            tras el cierre se opera al día siguiente)
    fda_aplicacion          "BLA761463" | None | FALTANTE
    fda_tipo                "NDA" | "ANDA" | "BLA" (prefijo de la solicitud) | None | FALTANTE
    fda_clase               submission_class_code ("TYPE 1", "TYPE 5", ...) | None | FALTANTE
    fda_laboratorio         sponsor tal como lo escribe la FDA | None | FALTANTE
    fda_suplementos_dia     suplementos (SUPPL) aprobados ese día al laboratorio | FALTANTE
`meta.last_updated` de openFDA va con ~4 días de retraso: en vivo, la
aprobación de hoy puede no estar hasta la semana siguiente.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from fuentes import cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import ErrorFuente
from fuentes.http import Cliente, Limitador
from fuentes.tiempo import fecha_ny

log = logging.getLogger("fuentes.fda")

URL = "https://api.fda.gov/drug/drugsfda.json"
LIMITE = 100
MAX_PAGINAS = 50
EDAD_S = None     # una aprobación pasada no cambia; el mes en curso se pide con edad corta
EDAD_MES_EN_CURSO_S = 6 * 3600.0
RUTA_MAPA = Path(__file__).resolve().parent / "datos" / "fda_laboratorios.json"


@dataclass(frozen=True)
class Aprobacion:
    fecha: date
    sponsor: str
    aplicacion: str
    tipo: str            # NDA | ANDA | BLA | "" (prefijo desconocido)
    suplemento: bool     # SUPPL (True) u ORIG (False)
    clase: str


def tipo_de(aplicacion: str) -> str:
    for prefijo in ("ANDA", "NDA", "BLA"):
        if aplicacion.upper().startswith(prefijo):
            return prefijo
    return ""


def cliente_fda(transport=None, dormir=None) -> Cliente:
    kw = {"dormir": dormir} if dormir is not None else {}
    return Cliente("fda", "hernan-portafolio fuentes", limitador=Limitador(2, 1.0, **kw), transport=transport, **kw)


def normalizar(nombre: str) -> str:
    return " ".join(nombre.upper().replace(",", " ").replace(".", " ").split())


def cargar_mapa(ruta: Path = RUTA_MAPA) -> dict[str, list[str]]:
    """ticker -> nombres FDA (normalizados) con confianza alta. Los
    tickers `dudosa` quedan con lista vacía a propósito: existen en el
    mapa (no se pide más), pero valen FALTANTE."""
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for nombre, e in doc["laboratorios"].items():
        t = e["ticker"].upper()
        out.setdefault(t, [])
        if e.get("confianza") == "alta":
            out[t].append(normalizar(nombre))
    return out


def _fecha_fda(texto: object) -> date | None:
    if not isinstance(texto, str) or len(texto) != 8 or not texto.isdigit():
        return None
    try:
        return date(int(texto[:4]), int(texto[4:6]), int(texto[6:]))
    except ValueError:
        return None


def leer_pagina(cuerpo: object, desde: date, hasta: date) -> tuple[list[Aprobacion], int]:
    """(aprobaciones del rango, total declarado por la API)."""
    if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("results"), list):
        raise ErrorFuente("cuerpo", "fda")
    total = cuerpo.get("meta", {}).get("results", {}).get("total") if isinstance(cuerpo.get("meta"), dict) else None
    if not isinstance(total, int):
        raise ErrorFuente("cuerpo", "fda")
    out = []
    for r in cuerpo["results"]:
        if not isinstance(r, dict):
            continue
        sponsor, app = r.get("sponsor_name"), r.get("application_number")
        if not isinstance(sponsor, str) or not isinstance(app, str):
            continue
        for s in r.get("submissions") or []:
            if not isinstance(s, dict) or s.get("submission_status") != "AP":
                continue
            f = _fecha_fda(s.get("submission_status_date"))
            if f is None or not desde <= f <= hasta:
                continue
            tipo_sub = str(s.get("submission_type", "")).upper()
            if tipo_sub not in ("ORIG", "SUPPL"):
                continue   # un tipo desconocido no se cuenta como nada
            out.append(Aprobacion(f, normalizar(sponsor), app, tipo_de(app), tipo_sub == "SUPPL",
                                  str(s.get("submission_class_code") or "")))
    return out, total


def _mes(d: date) -> tuple[date, date]:
    ini = d.replace(day=1)
    fin = (ini + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return ini, fin


class AprobacionesFDA:
    nombre = "fda"
    _NOMBRES = ["fda_aprobacion_dia", "fda_aprobacion_vispera", "fda_aplicacion", "fda_tipo", "fda_clase",
                "fda_laboratorio", "fda_suplementos_dia"]

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None, mapa: dict[str, list[str]] | None = None,
                 hoy: date | None = None) -> None:
        self.cache = cache or Cache()
        self._cliente = cliente
        self.mapa = mapa if mapa is not None else cargar_mapa()
        self._hoy = hoy or date.today()
        self._meses: dict[date, list[Aprobacion] | None] = {}

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def cliente(self) -> Cliente:
        if self._cliente is None:
            self._cliente = cliente_fda()
        return self._cliente

    def aprobaciones_del_mes(self, d: date) -> list[Aprobacion] | None:
        ini, fin = _mes(d)
        if ini in self._meses:
            return self._meses[ini]
        params_base = {"search": f"submissions.submission_status_date:[{ini:%Y%m%d} TO {fin:%Y%m%d}] AND submissions.submission_status:AP",
                       "limit": LIMITE}
        clave = f"{URL}?{ini:%Y%m}"

        def pedir():
            paginas, skip = [], 0
            for _ in range(MAX_PAGINAS):
                cuerpo = self.cliente().get_json(URL, {**params_base, "skip": skip})
                paginas.append(cuerpo)
                _, total = leer_pagina(cuerpo, ini, fin)
                skip += LIMITE
                if skip >= total:
                    return paginas
            raise ErrorFuente("paginacion", "fda")

        edad = EDAD_MES_EN_CURSO_S if fin >= self._hoy else EDAD_S
        try:
            paginas = self.cache.obtener("fda", clave, pedir, edad)
            out: list[Aprobacion] = []
            for pg in paginas:
                out.extend(leer_pagina(pg, ini, fin)[0])
            self._meses[ini] = out
        except ErrorFuente as ex:
            if ex.codigo == "http_404":
                # openFDA responde 404 a una búsqueda sin resultados: es un mes sin aprobaciones.
                self._meses[ini] = []
            else:
                log.warning("fda: %s (%s)", ex.codigo, ini.isoformat()[:7])
                self._meses[ini] = None
        return self._meses[ini]

    def _del_dia(self, nombres: list[str], d: date) -> tuple[Aprobacion | None, int] | None:
        """(aprobación ORIG del laboratorio ese día o None, suplementos ese
        día); None si el mes no se pudo."""
        aps = self.aprobaciones_del_mes(d)
        if aps is None:
            return None
        propias = [a for a in aps if a.fecha == d and a.sponsor in nombres]
        orig = next((a for a in propias if not a.suplemento), None)
        return orig, sum(1 for a in propias if a.suplemento)

    def columnas(self, ticker: str, momento: datetime) -> dict:
        nombres = self.mapa.get(ticker.upper())
        if not nombres:
            return todas_faltantes(self._NOMBRES)
        dia = fecha_ny(momento)
        vispera = dia - timedelta(days=1)
        while vispera.weekday() >= 5:
            vispera -= timedelta(days=1)
        hoy, ayer = self._del_dia(nombres, dia), self._del_dia(nombres, vispera)
        if hoy is None or ayer is None:
            return todas_faltantes(self._NOMBRES)
        elegida = hoy[0] or ayer[0]
        return {"fda_aprobacion_dia": hoy[0] is not None, "fda_aprobacion_vispera": ayer[0] is not None,
                "fda_aplicacion": elegida.aplicacion if elegida else None, "fda_tipo": elegida.tipo if elegida else None,
                "fda_clase": elegida.clase if elegida else None, "fda_laboratorio": elegida.sponsor if elegida else None,
                "fda_suplementos_dia": hoy[1]}


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar fda AAAA-MM`: guarda la primera página
    real de un mes y lista los sponsors para completar el mapa."""
    from fuentes.grabar import guardar

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar fda")
    ap.add_argument("mes", help="AAAA-MM")
    ap.add_argument("--skip", type=int, default=0, help="página siguiente (100, 200, ...)")
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    ini, fin = _mes(date.fromisoformat(args.mes + "-01"))
    params = {"search": f"submissions.submission_status_date:[{ini:%Y%m%d} TO {fin:%Y%m%d}] AND submissions.submission_status:AP",
              "limit": LIMITE, "skip": args.skip}
    r = cliente_fda().get(URL, params)
    sufijo = f"_p{args.skip // LIMITE + 1}" if args.skip else ""
    guardar("fda", f"aprobaciones_{ini:%Y%m}{sufijo}", URL, params, r.status, r.headers, r.texto, ficticio=False,
            directorio=args.dir)
    aps, total = leer_pagina(json.loads(r.texto), ini, fin)
    orig = [a for a in aps if not a.suplemento]
    print(f"{ini:%Y-%m}: {total} solicitudes con aprobación; {len(aps)} en la primera página "
          f"({len(orig)} ORIG, {len(aps) - len(orig)} SUPPL)")
    if args.skip + LIMITE < total:
        print(f"  faltan páginas: repetir con --skip {args.skip + LIMITE} (total {total})")
    for a in orig:
        print(f"  ORIG {a.fecha} {a.tipo} {a.aplicacion} {a.clase}: {a.sponsor}")
    return 0


cli.registrar("fda", _grabar)
