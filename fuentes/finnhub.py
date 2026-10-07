"""Finnhub (F7): noticias por empresa y earnings de hoy/ayer, como columnas del backtest v2.

Dos endpoints, una clave (`FINNHUB_API_KEY`, secreto de GitHub Actions):

  /company-news   `GET https://finnhub.io/api/v1/company-news?symbol&from&to`,
                  SIEMPRE con from = to = un solo día (ver TOPE abajo).
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
que "reporta hoy" en principio se sabía a la hora de la señal, pero ver
SESGO DE LOOKAHEAD más abajo.

TOPE (medido en el VPS el 2026-09-30, plan gratis): /company-news
devuelve como máximo ~250 artículos y se queda con los MÁS RECIENTES; lo
viejo del rango se pierde en silencio (AAPL 21..25/9: 245 artículos, el
primero del 21 a las 18:35 UTC; el 21 solo: 57 desde las 05:55). Por eso
se pide un día por pedido y se unen los días, deduplicando por `id`. Un
día que devuelve `TOPE_DIA` (240) o más artículos puede venir recortado:
toda columna cuya ventana toque ese día es FALTANTE, no un conteo.

Días pedidos. `from`/`to` son fechas y Finnhub no documenta en qué zona
las corta. Para una ventana [a, b) se piden los días desde la fecha NY de
`a` hasta la fecha UTC de `b` (la fecha NY es la UTC o la del día
anterior: el rango cubre las dos lecturas) y después se filtra por
`datetime`. Cada día se cachea aparte: un día pasado, para siempre; uno
que todavía puede recibir artículos, 5 min. Una señal toca 2-3 días,
pero las señales vecinas del mismo símbolo los comparten, así que el
costo real contra el limitador es ~1 pedido nuevo por símbolo y día. Si
un día falla o toca el tope no se piden los siguientes de esa ventana
(serían llamadas gastadas en una columna que igual queda FALTANTE).

Historia. El plan gratis da ~1 año de noticias. Una lista vacía para un
día fuera de ese año no es "no hubo noticias": es que la fuente no llega.
Por eso un instante con más de `HISTORIA_DIAS` de antigüedad es FALTANTE
sin pedir nada. Dentro del año, una lista vacía sí es cero (la fuente
respondió completa y un small cap sin noticias en 24 h es normal; SOUN
21..25/9: 12 artículos).

Límite: 60 llamadas/min en el plan gratis. `resultados.cliente_finnhub`
usa por defecto UN limitador del proceso (`resultados.limitador_finnhub`,
50/min) compartido por F2a y F7 y por los dos endpoints de F7: juntas
nunca pasan de 50/min, aunque cada una arme su propio cliente. No cubre
otros procesos con la misma clave (p. ej. el metadata del hunter en el
VPS): si corren a la vez, el margen de 10/min es lo que los separa.

SESGO DE LOOKAHEAD en `finnhub_earnings_*`: el histórico del calendario
de Finnhub guarda la fecha FINAL del reporte, no la que se conocía en su
momento. Si una empresa movió su fecha (adelantó, atrasó o la confirmó a
última hora), el backtest la ve como si se hubiera sabido de antemano.
Estas dos columnas NO son point-in-time estricto; las de noticias sí.
Cualquier resultado que dependa de ellas hay que leerlo con eso en mente.

COLUMNAS:
    finnhub_noticias_24h    artículos distintos en [instante − 24 h, instante)
                            | FALTANTE (también si un día de su ventana tocó el tope)
    finnhub_noticia_60min   True si alguno en [instante − 60 min, instante)
                            | False | FALTANTE (ídem, solo con los días de SU ventana)
    finnhub_earnings_hoy    True si el calendario pone un reporte en la
                            fecha NY del instante | False | FALTANTE
                            (con sesgo de lookahead, ver arriba)
    finnhub_earnings_ayer   True si lo pone el día hábil anterior (lunes →
                            viernes; NO descuenta feriados) | False | FALTANTE
                            (con sesgo de lookahead, ver arriba)
FALTANTE: sin clave, error HTTP, cuerpo que no es una lista, un artículo
sin `datetime` legible (invalida el día: no se sabe qué se omite), un día
en el tope, instante fuera de la historia del plan, o un calendario sin
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
TOPE_DIA = 240            # >= esto en un día: posiblemente recortado (el tope real ronda 250)
EDAD_RECIENTE_S = 300.0   # un día que todavía puede recibir artículos se recachea cada 5 min

Articulos = list[tuple[object, datetime]]


def token_configurado() -> str:
    tok = os.environ.get(ENV_TOKEN, "").strip()
    if not tok:
        raise ErrorFuente("sin_credenciales", "finnhub")
    return tok


def simbolo_finnhub(ticker: str) -> str:
    # Finnhub usa el punto de la bolsa (BRK.B); Yahoo, el guion.
    return ticker.strip().upper().replace("-", ".")


def leer_noticias(cuerpo: object) -> Articulos:
    """[(identidad, hora UTC)] sin duplicados. Levanta ErrorFuente si el
    cuerpo no es una lista (Finnhub responde `{"error": ...}` con 200 en
    algunos fallos) o si un artículo no trae `datetime` legible."""
    if not isinstance(cuerpo, list):
        raise ErrorFuente("cuerpo", "finnhub")
    out = []
    for a in cuerpo:
        if not isinstance(a, dict):
            raise ErrorFuente("item_ilegible", "finnhub")
        ts = entero(a.get("datetime"))
        if ts is None or ts <= 0:
            raise ErrorFuente("item_ilegible", "finnhub")
        # El mismo artículo llega repetido a veces: se cuenta una vez (ver `unir`).
        ident = a.get("id") if entero(a.get("id")) is not None else (ts, a.get("headline"))
        out.append((ident, datetime.fromtimestamp(ts, UTC)))
    return unir([out])


def recortado(cuerpo: object) -> bool:
    """El día llegó con TOPE_DIA artículos o más, contados crudos (antes
    de deduplicar: el tope lo aplica Finnhub sobre lo que manda)."""
    return isinstance(cuerpo, list) and len(cuerpo) >= TOPE_DIA


def dias_de(desde: datetime, hasta: datetime) -> list[date]:
    """Días a pedir para la ventana [desde, hasta): de la fecha NY de
    `desde` a la fecha UTC de `hasta`, que cubre las dos lecturas posibles
    de `from`/`to` (UTC o Nueva York)."""
    d, fin = fecha_ny(desde), a_utc(hasta).date()
    out = []
    while d <= fin:
        out.append(d)
        d += timedelta(days=1)
    return out


def unir(dias: list[Articulos]) -> Articulos:
    """Une listas quitando repetidos por identidad: un artículo cerca de
    medianoche puede llegar bajo dos días."""
    vistos: set = set()
    out = []
    for lista in dias:
        for ident, hora in lista:
            if ident not in vistos:
                vistos.add(ident)
                out.append((ident, hora))
    return out


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
        # (símbolo, día) -> (artículos, recortado), o None si el día falló.
        self._dias: dict[tuple[str, date], tuple[Articulos, bool] | None] = {}

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def hoy(self) -> date:
        return self._hoy or datetime.now(UTC).date()

    def noticias_del_dia(self, ticker: str, dia: date) -> tuple[Articulos, bool] | None:
        """(artículos, recortado) de UN día (from = to = dia), o None si falló."""
        sim = simbolo_finnhub(ticker)
        k = (sim, dia)
        if k in self._dias:
            return self._dias[k]
        params = {"symbol": sim, "from": dia.isoformat(), "to": dia.isoformat()}
        clave = URL_NOTICIAS + "?" + "&".join(f"{a}={b}" for a, b in sorted(params.items()))
        edad = EDAD_RECIENTE_S if dia >= self.hoy() - timedelta(days=1) else None

        def pedir():
            tok = token_configurado()
            return self._cliente.get_json(URL_NOTICIAS, params, {CABECERA_TOKEN: tok})

        try:
            cuerpo = self.cache.obtener("finnhub_noticias", clave, pedir, edad)
            self._dias[k] = (leer_noticias(cuerpo), recortado(cuerpo))
            if self._dias[k][1]:
                log.warning("finnhub: %s %s llegó al tope (%d): posiblemente recortado", sim, dia, len(cuerpo))
        except ErrorFuente as ex:
            log.warning("finnhub: noticias %s (%s %s)", ex.codigo, sim, dia)
            self._dias[k] = None
        return self._dias[k]

    def noticias(self, ticker: str, desde: datetime, hasta: datetime) -> Articulos | None:
        """Artículos de los días que cubren [desde, hasta), unidos y sin
        repetidos. None si algún día falló o tocó el tope."""
        dias = []
        for d in dias_de(desde, hasta):
            r = self.noticias_del_dia(ticker, d)
            if r is None or r[1]:
                return None
            dias.append(r[0])
        return unir(dias)

    def _columnas_noticias(self, ticker: str, momento: datetime) -> dict:
        if (self.hoy() - momento.date()).days > HISTORIA_DIAS:
            return todas_faltantes(["finnhub_noticias_24h", "finnhub_noticia_60min"])
        # Cada columna mira solo los días de SU ventana: un día recortado
        # ayer invalida el conteo de 24 h, no la de 60 min de hoy. Los días
        # comunes salen de `_dias` sin volver a pedir.
        desde24, desde60 = momento - VENTANA_24H, momento - VENTANA_60MIN
        l24 = self.noticias(ticker, desde24, momento)
        l60 = self.noticias(ticker, desde60, momento)
        return {
            "finnhub_noticias_24h": FALTANTE if l24 is None else sum(1 for _, h in l24 if desde24 <= h < momento),
            "finnhub_noticia_60min": FALTANTE if l60 is None else any(desde60 <= h < momento for _, h in l60),
        }

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
    """`python -m fuentes grabar finnhub TICKER DESDE HASTA`: pide las
    noticias reales DÍA POR DÍA (como la fuente), guarda cada día sin la
    clave e imprime cuántas llegaron y de qué horas. Avisa los días que
    tocan el tope. Sale con 5 si alguno lo tocó y 3 si todo vino vacío."""
    import json

    from fuentes.grabar import guardar

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar finnhub")
    ap.add_argument("ticker")
    ap.add_argument("desde", type=date.fromisoformat)
    ap.add_argument("hasta", type=date.fromisoformat)
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    sim = simbolo_finnhub(args.ticker)
    cliente = cliente_finnhub()
    tok = token_configurado()
    total, en_tope = 0, []
    d = args.desde
    while d <= args.hasta:
        params = {"symbol": sim, "from": d.isoformat(), "to": d.isoformat()}
        r = cliente.get(URL_NOTICIAS, params, {CABECERA_TOKEN: tok})
        # La clave va en la cabecera, que `guardar` no conserva: el archivo entra al repo limpio.
        guardar("finnhub", f"noticias_{sim}_{d:%Y%m%d}", URL_NOTICIAS, params, r.status, r.headers, r.texto,
                ficticio=False, directorio=args.dir)
        cuerpo = json.loads(r.texto)
        lista = leer_noticias(cuerpo)
        total += len(lista)
        horas = sorted(h for _, h in lista)
        rango = f", {horas[0]:%H:%M}..{horas[-1]:%H:%M} UTC" if horas else ""
        aviso = ""
        if recortado(cuerpo):
            en_tope.append(d)
            aviso = f"  <-- TOPE ({len(cuerpo)} >= {TOPE_DIA}): posiblemente recortado"
        print(f"{sim} {d}: {len(lista)} artículos{rango}{aviso}")
        d += timedelta(days=1)
    if en_tope:
        print(f"{len(en_tope)} día(s) en el tope: {', '.join(map(str, en_tope))}. Sus columnas quedan FALTANTE.")
        return 5
    if total == 0:
        print(f"{sim} {args.desde}..{args.hasta}: 0 artículos (¿fuera de la historia del plan?)")
        return 3
    return 0


cli.registrar("finnhub", _grabar)
