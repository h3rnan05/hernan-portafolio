"""Calendario de resultados (earnings) como columnas del backtest v2.

Dos modos, mismas columnas:

  finnhub  `GET https://finnhub.io/api/v1/calendar/earnings?from&to&symbol`
           con `FINNHUB_API_KEY` (secreto; va en el parámetro `token` y
           nunca al log ni a la clave de caché). Trae fecha y `hour`
           (`bmo` antes de abrir, `amc` tras el cierre, `dmh` en horario,
           `""` desconocida) y la próxima fecha, que es lo que el vivo
           necesita para el "día siguiente". El plan gratis no garantiza
           un año de historia: `python -m fuentes grabar resultados
           TICKER` lo comprueba e imprime HISTORIA OK / SIN HISTORIA.
  8k       Sin historia en Finnhub, el evento del backtest es el 8-K
           ítem 2.02 de EDGAR (F1a), con su hora de aceptación. No sabe
           el futuro: `resultados_proximo_dias` queda FALTANTE.

COLUMNAS:
    resultados_reciente     True si hubo reporte en las 24 h previas al
                            instante | False | FALTANTE
    resultados_fecha        fecha del reporte reciente (ISO) | None | FALTANTE
    resultados_hora         "bmo" | "amc" | "dmh" | None | FALTANTE
    resultados_proximo_dias días hasta el próximo reporte (0 = hoy, más
                            tarde que el instante) | None (ninguno en 30 d) | FALTANTE
    resultados_fuente       "finnhub" | "8k_2.02"
Con Finnhub, un `dmh` (durante el horario) de HOY no dice si ya salió a
la hora de la señal: `resultados_reciente` es FALTANTE en ese caso; un
`hour` vacío, igual. Con 8-K la hora es la de aceptación y no hay duda.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fuentes import __main__ as cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import FALTANTE, ErrorFuente
from fuentes.edgar import LectorEdgar, ochok_en_ventana
from fuentes.http import Cliente, Limitador
from fuentes.tiempo import NY, fecha_ny, leer_fecha, ny

log = logging.getLogger("fuentes.resultados")

ENV_TOKEN = "FINNHUB_API_KEY"
URL_CALENDARIO = "https://finnhub.io/api/v1/calendar/earnings"
DIAS_ATRAS = 400        # ventana que se pide por símbolo (cubre un backtest de 12 meses)
DIAS_ADELANTE = 30
EDAD_S = 24 * 3600.0
HORAS_VENTANA = 24
HORAS = {"bmo", "amc", "dmh"}
APERTURA = time(9, 30)
CIERRE = time(16, 0)


@dataclass(frozen=True)
class Evento:
    fecha: date
    hora: str | None          # bmo | amc | dmh | None


def token_configurado() -> str:
    tok = os.environ.get(ENV_TOKEN, "").strip()
    if not tok:
        raise ErrorFuente("sin_credenciales", "resultados")
    return tok


def cliente_finnhub(transport=None, dormir=None) -> Cliente:
    kw = {"dormir": dormir} if dormir is not None else {}
    # 60/min en el plan gratis; se usa la mitad.
    return Cliente("finnhub", "hernan-portafolio fuentes", limitador=Limitador(30, 60.0), transport=transport, **kw)


def leer_calendario(cuerpo: object, simbolo: str) -> list[Evento]:
    if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("earningsCalendar"), list):
        raise ErrorFuente("cuerpo", "resultados")
    out = []
    for e in cuerpo["earningsCalendar"]:
        if not isinstance(e, dict) or str(e.get("symbol", "")).upper() != simbolo.upper():
            continue
        f = leer_fecha(e.get("date"))
        if f is None:
            continue
        h = e.get("hour")
        out.append(Evento(f, h if isinstance(h, str) and h in HORAS else None))
    return sorted(out, key=lambda e: e.fecha)


def momento_del_evento(e: Evento) -> tuple[datetime, datetime] | None:
    """(desde cuándo pudo salir, cuándo seguro ya salió), en UTC. None
    si la hora es desconocida (dmh o vacía). Un `bmo` sale en algún
    momento antes de la apertura; un `amc`, al cierre o poco después."""
    if e.hora == "bmo":
        return ny(e.fecha, time(0, 0)), ny(e.fecha, APERTURA)
    if e.hora == "amc":
        return ny(e.fecha, CIERRE), ny(e.fecha, CIERRE)
    return None


def reciente(eventos: list[Evento], momento: datetime) -> tuple[object, Evento | None]:
    """(True/False/FALTANTE, evento). FALTANTE si un evento del día o del
    día previo tiene hora desconocida y podría haber ocurrido antes."""
    desde = momento - timedelta(hours=HORAS_VENTANA)
    dudoso = False
    for e in reversed(eventos):
        if e.fecha < desde.astimezone(NY).date() or e.fecha > fecha_ny(momento):
            continue
        rango = momento_del_evento(e)
        if rango is None:
            dudoso = True
            continue
        ini, salida = rango
        if desde < salida <= momento:
            return True, e
        if ini <= momento < salida:
            dudoso = True   # bmo del día a las 08:00 NY: no se sabe si ya salió
    if dudoso:
        return FALTANTE, None
    return False, None


def proximo(eventos: list[Evento], momento: datetime) -> int | None:
    hoy = fecha_ny(momento)
    for e in eventos:
        if e.fecha < hoy:
            continue
        if e.fecha == hoy:
            rango = momento_del_evento(e)
            if rango is None or rango[0] > momento:
                return 0
            continue
        if (e.fecha - hoy).days <= DIAS_ADELANTE:
            return (e.fecha - hoy).days
        break
    return None


class ResultadosFinnhub:
    nombre = "resultados"
    _NOMBRES = ["resultados_reciente", "resultados_fecha", "resultados_hora", "resultados_proximo_dias",
                "resultados_fuente"]

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None) -> None:
        self.cache = cache or Cache()
        self._cliente = cliente
        self._eventos: dict[tuple[str, date, date], list[Evento] | None] = {}

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def cliente(self) -> Cliente:
        if self._cliente is None:
            self._cliente = cliente_finnhub()
        return self._cliente

    def eventos(self, ticker: str, desde: date, hasta: date) -> list[Evento] | None:
        k = (ticker.upper(), desde, hasta)
        if k in self._eventos:
            return self._eventos[k]
        params_publicos = {"from": desde.isoformat(), "to": hasta.isoformat(), "symbol": ticker.upper()}
        clave = URL_CALENDARIO + "?" + "&".join(f"{a}={b}" for a, b in sorted(params_publicos.items()))

        def pedir():
            tok = token_configurado()
            return self.cliente().get_json(URL_CALENDARIO, {**params_publicos, "token": tok})

        try:
            cuerpo = self.cache.obtener("finnhub_resultados", clave, pedir, EDAD_S)
            self._eventos[k] = leer_calendario(cuerpo, ticker)
        except ErrorFuente as ex:
            log.warning("resultados: %s (%s)", ex.codigo, ticker)
            self._eventos[k] = None
        return self._eventos[k]

    def _ventana(self, momento: datetime) -> tuple[date, date]:
        # Ventana fija por año calendario del instante, para que un
        # backtest de 12 meses pida una o dos veces por símbolo.
        d = fecha_ny(momento)
        return date(d.year, 1, 1) - timedelta(days=DIAS_ATRAS - 365), date(d.year, 12, 31) + timedelta(days=DIAS_ADELANTE)

    def columnas(self, ticker: str, momento: datetime) -> dict:
        desde, hasta = self._ventana(momento)
        ev = self.eventos(ticker, desde, hasta)
        if ev is None:
            return todas_faltantes(self._NOMBRES)
        rec, e = reciente(ev, momento)
        return {"resultados_reciente": rec, "resultados_fecha": e.fecha.isoformat() if e else None,
                "resultados_hora": e.hora if e else None, "resultados_proximo_dias": proximo(ev, momento),
                "resultados_fuente": "finnhub"}


class ResultadosEdgar:
    """Mismas columnas, con el 8-K 2.02 como evento (sin historia en Finnhub)."""

    nombre = "resultados"
    _NOMBRES = ResultadosFinnhub._NOMBRES

    def __init__(self, lector: LectorEdgar | None = None) -> None:
        self.lector = lector or LectorEdgar()

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def columnas(self, ticker: str, momento: datetime) -> dict:
        pres = self.lector.presentaciones(ticker)
        if pres is None:
            return todas_faltantes(self._NOMBRES)
        try:
            en_ventana = [p for p in ochok_en_ventana(pres, momento, HORAS_VENTANA) if "2.02" in p.items]
        except ErrorFuente:
            return todas_faltantes(self._NOMBRES)
        if not en_ventana:
            return {"resultados_reciente": False, "resultados_fecha": None, "resultados_hora": None,
                    "resultados_proximo_dias": FALTANTE, "resultados_fuente": "8k_2.02"}
        p = en_ventana[0]
        local = p.aceptada.astimezone(NY)
        hora = "bmo" if local.time() < APERTURA else ("amc" if local.time() >= CIERRE else "dmh")
        return {"resultados_reciente": True, "resultados_fecha": local.date().isoformat(), "resultados_hora": hora,
                "resultados_proximo_dias": FALTANTE, "resultados_fuente": "8k_2.02"}


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar resultados TICKER`: guarda el calendario
    real de los últimos 400 días y dice si el plan trae historia."""
    from fuentes.grabar import guardar

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar resultados")
    ap.add_argument("ticker")
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    hoy = date.today()
    cliente = cliente_finnhub()
    params = {"from": (hoy - timedelta(days=DIAS_ATRAS)).isoformat(), "to": (hoy + timedelta(days=DIAS_ADELANTE)).isoformat(),
              "symbol": args.ticker.upper()}
    r = cliente.get(URL_CALENDARIO, {**params, "token": token_configurado()})
    # Se guarda SIN el token: la respuesta grabada entra al repo.
    guardar("resultados", f"calendario_{args.ticker.upper()}", URL_CALENDARIO, params, r.status, r.headers, r.texto,
            ficticio=False, directorio=args.dir)
    import json
    ev = leer_calendario(json.loads(r.texto), args.ticker)
    viejos = [e for e in ev if e.fecha < hoy - timedelta(days=300)]
    if viejos:
        print(f"HISTORIA OK: {len(ev)} eventos, el más antiguo {ev[0].fecha}")
        return 0
    print(f"SIN HISTORIA: {len(ev)} eventos, ninguno anterior a 300 días. Usar ResultadosEdgar (8-K 2.02) en el backtest.")
    return 3


cli.registrar("resultados", _grabar)
