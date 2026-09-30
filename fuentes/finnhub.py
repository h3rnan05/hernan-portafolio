"""Finnhub (F7): noticias por empresa y earnings de hoy/ayer, como columnas del backtest v2.

Dos endpoints, una clave (`FINNHUB_API_KEY`, secreto de GitHub Actions):

  /company-news   `GET https://finnhub.io/api/v1/company-news?symbol&from&to`
                  Lista de artículos con `datetime` (segundos UNIX, UTC),
                  `id`, `headline`, `source`. La clave va en la cabecera
                  `X-Finnhub-Token`, no en la URL: así no puede terminar
                  en la clave de caché, en una respuesta grabada ni en el
                  texto de una excepción de `requests`.
  /calendar/earnings  El mismo pedido que F2a (`resultados.ResultadosFinnhub`),
                  reutilizado tal cual: misma caché, mismo parser. Aquí
                  solo se deriva "reporta hoy / reportó ayer".

Point-in-time. Una noticia cuenta solo si su `datetime` es ESTRICTAMENTE
anterior al instante de la señal (el cierre de la vela, ver
`columnas.py`): una noticia con la misma marca que el instante no se
sabía todavía. El calendario de earnings se publica por adelantado, así
que "reporta hoy" se sabía a la hora de la señal; la limitación es que
el histórico de Finnhub guarda la fecha FINAL, y una fecha movida a
última hora entra como si se hubiera conocido antes.

Rango pedido. `from`/`to` son fechas; Finnhub no documenta en qué zona
las corta. La fecha NY de un instante es la UTC o la del día anterior,
así que se pide desde (fecha UTC del inicio de la ventana − 1 día) hasta
la fecha UTC del instante, y después se filtra por `datetime`. Todas las
señales de un símbolo en un mismo día comparten el pedido y la caché.

Historia. El plan gratis da ~1 año de noticias. Una lista vacía para un
día fuera de ese año no es "no hubo noticias": es que la fuente no llega.
Por eso un instante con más de `HISTORIA_DIAS` de antigüedad es FALTANTE
sin pedir nada. Dentro del año, una lista vacía sí es cero (la fuente
respondió completa y un small cap sin noticias en 24 h es normal).
Limitación: no se sabe si el plan gratis recorta la lista en días con
muchísimos artículos; `python -m fuentes grabar finnhub` imprime cuántos
llegaron para comprobarlo en un día cargado.

Límite: 60 llamadas/min en el plan gratis. `resultados.cliente_finnhub`
usa 30/min; esta fuente comparte UN cliente (un limitador) entre sus dos
endpoints. Si F2a y F7 corren en el mismo proceso, cada una con su
cliente, el peor caso es 30 + 30 = 60/min: justo el tope, nunca encima.

COLUMNAS:
    finnhub_noticias_24h    artículos distintos en [instante − 24 h, instante)
                            | FALTANTE
    finnhub_noticia_60min   True si alguno en [instante − 60 min, instante)
                            | False | FALTANTE
    finnhub_earnings_hoy    True si el calendario pone un reporte en la
                            fecha NY del instante | False | FALTANTE
    finnhub_earnings_ayer   True si lo pone el día hábil anterior (lunes →
                            viernes; NO descuenta feriados) | False | FALTANTE
FALTANTE: sin clave, error HTTP, cuerpo que no es una lista, un artículo
sin `datetime` legible (invalida toda la respuesta: no se sabe qué se
omite), instante fuera de la historia del plan, o un calendario sin
eventos o que empieza después del día anterior al instante (no hay
cobertura que respalde un False). Nunca 0 ni False por ausencia.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fuentes import cli
from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import FALTANTE, ErrorFuente, entero
from fuentes.http import Cliente
from fuentes.resultados import ResultadosFinnhub, cliente_finnhub
from fuentes.tiempo import a_utc, fecha_ny

log = logging.getLogger("fuentes.finnhub")

ENV_TOKEN = "FINNHUB_API_KEY"
CABECERA_TOKEN = "X-Finnhub-Token"
URL_NOTICIAS = "https://finnhub.io/api/v1/company-news"
HISTORIA_DIAS = 365
VENTANA_24H = timedelta(hours=24)
VENTANA_60MIN = timedelta(minutes=60)
EDAD_RECIENTE_S = 300.0   # un día que todavía puede recibir artículos se recachea cada 5 min


def token_configurado() -> str:
    tok = os.environ.get(ENV_TOKEN, "").strip()
    if not tok:
        raise ErrorFuente("sin_credenciales", "finnhub")
    return tok


def simbolo_finnhub(ticker: str) -> str:
    # Finnhub usa el punto de la bolsa (BRK.B); Yahoo, el guion.
    return ticker.strip().upper().replace("-", ".")


def leer_noticias(cuerpo: object) -> list[tuple[object, datetime]]:
    """[(identidad, hora UTC)] sin duplicados. Levanta ErrorFuente si el
    cuerpo no es una lista (Finnhub responde `{"error": ...}` con 200 en
    algunos fallos) o si un artículo no trae `datetime` legible."""
    if not isinstance(cuerpo, list):
        raise ErrorFuente("cuerpo", "finnhub")
    vistos: set = set()
    out = []
    for a in cuerpo:
        if not isinstance(a, dict):
            raise ErrorFuente("item_ilegible", "finnhub")
        ts = entero(a.get("datetime"))
        if ts is None or ts <= 0:
            raise ErrorFuente("item_ilegible", "finnhub")
        hora = datetime.fromtimestamp(ts, UTC)
        # El mismo artículo llega repetido a veces: se cuenta una vez.
        ident = a.get("id") if entero(a.get("id")) is not None else (ts, a.get("headline"))
        if ident in vistos:
            continue
        vistos.add(ident)
        out.append((ident, hora))
    return out


def rango_pedido(momento: datetime) -> tuple[date, date]:
    desde = momento - VENTANA_24H
    return desde.date() - timedelta(days=1), momento.date()


def dia_habil_anterior(d: date) -> date:
    cursor = d - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def earnings_hoy_ayer(fechas: list[date], momento: datetime) -> tuple[object, object]:
    """(hoy, ayer) como True/False, o FALTANTE en ambos si el calendario
    no respalda un False: vacío, o su primer evento es posterior al día
    anterior al instante (la cobertura no llega)."""
    hoy = fecha_ny(momento)
    ayer = dia_habil_anterior(hoy)
    if not fechas or min(fechas) > ayer:
        return FALTANTE, FALTANTE
    return hoy in fechas, ayer in fechas


class Finnhub:
    nombre = "finnhub"
    _NOMBRES = ["finnhub_noticias_24h", "finnhub_noticia_60min", "finnhub_earnings_hoy", "finnhub_earnings_ayer"]

    def __init__(self, cache: Cache | None = None, cliente: Cliente | None = None, hoy: date | None = None) -> None:
        self.cache = cache or Cache()
        self._cliente = cliente or cliente_finnhub()
        self._hoy = hoy
        # El calendario es el de F2a, con la misma caché y el mismo cliente.
        self._calendario = ResultadosFinnhub(self.cache, self._cliente)
        self._noticias: dict[tuple[str, date, date], list[tuple[object, datetime]] | None] = {}

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def hoy(self) -> date:
        return self._hoy or datetime.now(UTC).date()

    def noticias(self, ticker: str, desde: date, hasta: date) -> list[tuple[object, datetime]] | None:
        sim = simbolo_finnhub(ticker)
        k = (sim, desde, hasta)
        if k in self._noticias:
            return self._noticias[k]
        params = {"symbol": sim, "from": desde.isoformat(), "to": hasta.isoformat()}
        clave = URL_NOTICIAS + "?" + "&".join(f"{a}={b}" for a, b in sorted(params.items()))
        edad = EDAD_RECIENTE_S if hasta >= self.hoy() - timedelta(days=1) else None

        def pedir():
            tok = token_configurado()
            return self._cliente.get_json(URL_NOTICIAS, params, {CABECERA_TOKEN: tok})

        try:
            cuerpo = self.cache.obtener("finnhub_noticias", clave, pedir, edad)
            self._noticias[k] = leer_noticias(cuerpo)
        except ErrorFuente as ex:
            log.warning("finnhub: noticias %s (%s)", ex.codigo, sim)
            self._noticias[k] = None
        return self._noticias[k]

    def _columnas_noticias(self, ticker: str, momento: datetime) -> dict:
        nombres = ["finnhub_noticias_24h", "finnhub_noticia_60min"]
        if (self.hoy() - momento.date()).days > HISTORIA_DIAS:
            return todas_faltantes(nombres)
        lista = self.noticias(ticker, *rango_pedido(momento))
        if lista is None:
            return todas_faltantes(nombres)
        previas = [h for _, h in lista if h < momento]
        return {"finnhub_noticias_24h": sum(1 for h in previas if h >= momento - VENTANA_24H),
                "finnhub_noticia_60min": any(h >= momento - VENTANA_60MIN for h in previas)}

    def _columnas_earnings(self, ticker: str, momento: datetime) -> dict:
        desde, hasta = self._calendario._ventana(momento)
        ev = self._calendario.eventos(simbolo_finnhub(ticker), desde, hasta)
        if ev is None:
            return todas_faltantes(["finnhub_earnings_hoy", "finnhub_earnings_ayer"])
        hoy, ayer = earnings_hoy_ayer([e.fecha for e in ev], momento)
        return {"finnhub_earnings_hoy": hoy, "finnhub_earnings_ayer": ayer}

    def columnas(self, ticker: str, momento: datetime) -> dict:
        momento = a_utc(momento)
        return {**self._columnas_noticias(ticker, momento), **self._columnas_earnings(ticker, momento)}


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar finnhub TICKER DESDE HASTA`: guarda las
    noticias reales del rango (sin la clave) e imprime cuántas llegaron y
    de qué horas, para ver si el plan recorta la lista o la historia."""
    from fuentes.grabar import guardar

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar finnhub")
    ap.add_argument("ticker")
    ap.add_argument("desde", type=date.fromisoformat)
    ap.add_argument("hasta", type=date.fromisoformat)
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    sim = simbolo_finnhub(args.ticker)
    params = {"symbol": sim, "from": args.desde.isoformat(), "to": args.hasta.isoformat()}
    r = cliente_finnhub().get(URL_NOTICIAS, params, {CABECERA_TOKEN: token_configurado()})
    # La clave va en la cabecera, que `guardar` no conserva: el archivo entra al repo limpio.
    guardar("finnhub", f"noticias_{sim}_{args.desde:%Y%m%d}_{args.hasta:%Y%m%d}", URL_NOTICIAS, params, r.status,
            r.headers, r.texto, ficticio=False, directorio=args.dir)
    import json
    lista = leer_noticias(json.loads(r.texto))
    if not lista:
        print(f"{sim} {args.desde}..{args.hasta}: 0 artículos (¿fuera de la historia del plan?)")
        return 3
    horas = sorted(h for _, h in lista)
    print(f"{sim} {args.desde}..{args.hasta}: {len(lista)} artículos, del {horas[0]:%Y-%m-%d %H:%M} "
          f"al {horas[-1]:%Y-%m-%d %H:%M} UTC")
    return 0


cli.registrar("finnhub", _grabar)
