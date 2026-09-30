"""Planes B exploratorios (solo lectura, no tocan producción ni la v2).

Se corren con `--variante pead` y `--variante seguimiento`. Miden con las
mismas métricas del backtest v2 (R, acierto, MFE/MAE) sobre los mismos
datos y el mismo embudo de gap (pasos 1-4 de `motor.candidatos_gap`).

PEAD (`pead`) — deriva post-resultados en small caps $2–20:
  evento     8-K con ítem 2.02 (EDGAR) aceptado entre el cierre de la
             sesión anterior (16:00 ET) y la apertura del día (09:30 ET).
  filtros    gap oficial ≥ 4 % (subastas) y RVOL diario ≥ 3 (volumen del
             día / promedio de las 20 sesiones previas), apertura en $2–20.
  entrada    al cierre del día del evento (día 0), a la cotización de
             cierre de la barra diaria + slippage.
  salida     al cierre del día 3 (tres sesiones después) o por stop del
             6 % (si el mínimo de un día toca el stop, se sale al stop; si
             el día abre por debajo, a la apertura). R = 6 % de la entrada.
  ADVERTENCIA: mantiene posiciones de un día para otro. Rompe la regla
  de cierre diario de la v1 y necesitaría gestión de riesgo nueva (gap
  nocturno, tamaño, halts fuera de sesión). Es SOLO para medir.

Seguimiento (`seguimiento`) — nivel 1 al día siguiente, compatible con el
cierre diario:
  día 1      símbolo-día del embudo (gap ≥ 4 %, sin acción corporativa)
             con una noticia de las 24 h previas a las 11:00 clasificada
             nivel 1 alcista por la IA (misma caché de clasificaciones).
  día 2      la sesión siguiente: comprar cuando una vela de 1 min cierra
             por encima del máximo del día 1 (entre 09:31 y 15:30), a la
             apertura de la vela siguiente + slippage; stop 4 % bajo la
             entrada; salida a las 15:50 o por stop. R = 4 %.

En los dos, el tamaño es 0,5 % del equity inicial entre el riesgo por
acción (sin capitalizar, sin límites de cartera): aquí interesa la
expectativa por trade, no la curva.

`peadnoticia` es el mismo PEAD con otro evento: en vez del 8-K 2.02, un
titular de Benzinga publicado entre el cierre previo y la apertura que
nombre resultados (`PALABRAS_RESULTADOS`, sin IA). Existe porque los
runners de GitHub Actions reciben 403 de sec.gov (verificado el
2026-09-29: 1.943 de 1.943 pedidos); no reemplaza al 8-K, es un proxy
más ruidoso (un titular con "results" puede ser un ensayo clínico).

Los datos EDGAR se piden aquí con un cliente mínimo (`company_tickers` +
`submissions`, User-Agent con contacto en `FUENTES_SEC_USER_AGENT`, 8
pedidos/s, caché en el almacén) porque el paquete `fuentes/` (PRs #210–
#214) todavía no está fusionado; cuando lo esté, esto se reemplaza por
`fuentes.edgar`. La Z de `acceptanceDateTime` es UTC.
"""

from __future__ import annotations

import logging
import math
import os
import time as time_mod
from datetime import UTC, date, datetime, time, timedelta

import requests

from estrategia_v2 import catalizador as cat
from estrategia_v2 import reglas

from shadow_alpaca.backtest_v2 import datos
from shadow_alpaca.backtest_v2.motor import (APERTURA, CIERRE, MINUTOS_ANTES_DEL_CIERRE, NY, Parametros, Resultado,
                                             Senal, Trade, candidatos_gap)
from shadow_alpaca.cliente import ErrorDatos

log = logging.getLogger("shadow_alpaca.backtest_v2.planb")

PEAD_PRECIO_MAX = 20.0
PEAD_RVOL_MIN = 3.0
PEAD_STOP = 0.06
PEAD_DIAS = 3
SEG_STOP = 0.04
SEG_DESDE, SEG_HASTA = time(9, 31), time(15, 30)   # hora de cierre de la vela que rompe
RIESGO_EQUITY = 0.005
PALABRAS_RESULTADOS = ("financial results", "quarterly results", "reports first quarter", "reports second quarter",
                       "reports third quarter", "reports fourth quarter", "reports q1", "reports q2", "reports q3",
                       "reports q4", "fiscal year results", "full year results", "full-year results", "reports fiscal",
                       "earnings results", "reports earnings", "announces first quarter", "announces second quarter",
                       "announces third quarter", "announces fourth quarter", "quarter 2025 results",
                       "quarter 2026 results", "year-end results", "year end results", "reports record revenue",
                       "reports revenue", "reports net income", "reports results")


def titular_de_resultados(titular: str) -> bool:
    t = (titular or "").lower()
    return any(p in t for p in PALABRAS_RESULTADOS)

URL_TICKERS = "https://www.sec.gov/files/company_tickers.json"
URL_SUBMISSIONS = "https://data.sec.gov/submissions/"
ENV_UA = "FUENTES_SEC_USER_AGENT"


LIMITACIONES_PEAD = [
    "PLAN B EXPLORATORIO, solo lectura: no es la v2 ni toca producción.",
    "MANTIENE POSICIONES DE UN DÍA PARA OTRO: rompe la regla de cierre diario de la v1 y necesitaría gestión de "
    "riesgo nueva (gap nocturno, tamaño, halts fuera de sesión). Solo para medir.",
    "Entrada al cierre diario (barra diaria) + slippage; stop 6 % mirado sobre mínimos diarios: si el día abre por "
    "debajo del stop, se sale a la apertura (motivo `stop_gap`, pérdida > 1 R). R = 6 %.",
    "Evento = 8-K 2.02 aceptado entre el cierre previo y la apertura (EDGAR, Z = UTC). Sin dato EDGAR el símbolo-día "
    "se excluye, nunca cuenta como «sin 8-K».",
    "RVOL diario = volumen del día / promedio de 20 sesiones previas (barras diarias con ajuste split).",
    "Tamaño 0,5 % del equity inicial entre el riesgo, sin capitalizar ni límites de cartera: mide expectativa por trade.",
    "Float, ETF y SPAC no se filtran aquí (el embudo llega hasta acción corporativa); universo: listados de hoy.",
]
LIMITACIONES_SEGUIMIENTO = [
    "PLAN B EXPLORATORIO, solo lectura: no es la v2 ni toca producción.",
    "Día 1 = gap ≥ 4 % sin acción corporativa con una noticia nivel 1 alcista (IA, misma caché); día 2 = ruptura del "
    "máximo del día 1 en velas de 1 min (09:31–15:30), entrada a la apertura de la vela siguiente + slippage.",
    "Stop 4 % bajo la entrada (R = 4 %), salida a las 15:50 o por stop; sin objetivo ni breakeven.",
    "Tamaño 0,5 % del equity inicial entre el riesgo, sin capitalizar ni límites de cartera.",
    "Float, ETF, SPAC, RVOL, VWAP, SPY y spread NO se aplican: es la versión más simple de la idea.",
]


# ------------------------------------------------------------ EDGAR mínimo


class EdgarMinimo:
    """Lo justo para saber los 8-K 2.02 de un ticker, con caché en el almacén."""

    def __init__(self, cache: datos.Cache, transport=None, dormir=time_mod.sleep) -> None:
        self.cache = cache
        self._transport = transport or requests.get
        self._dormir = dormir
        self._mapa: dict[str, int] | None = None
        self._ultimo = 0.0
        self.fallos = 0

    def _ua(self) -> str:
        ua = os.environ.get(ENV_UA, "").strip()
        if not ua or "@" not in ua:
            raise ErrorDatos("sin_user_agent")
        return ua

    def _get(self, url: str) -> dict:
        espera = 0.13 - (time_mod.monotonic() - self._ultimo)   # 8/s
        if espera > 0:
            self._dormir(espera)
        self._ultimo = time_mod.monotonic()
        try:
            r = self._transport(url, headers={"User-Agent": self._ua(), "Accept-Encoding": "gzip, deflate"}, timeout=20)
        except requests.RequestException as ex:
            raise ErrorDatos("red") from ex
        if getattr(r, "status_code", None) != 200:
            raise ErrorDatos(f"http_{getattr(r, 'status_code', None)}")
        try:
            cuerpo = r.json()
        except ValueError:
            raise ErrorDatos("cuerpo") from None
        if not isinstance(cuerpo, dict):
            raise ErrorDatos("cuerpo")
        return cuerpo

    def cik(self, ticker: str) -> int | None:
        if self._mapa is None:
            cuerpo = self.cache.obtener("edgar", URL_TICKERS, lambda: self._get(URL_TICKERS))
            self._mapa = {str(v.get("ticker", "")).upper(): int(v["cik_str"]) for v in cuerpo.values()
                          if isinstance(v, dict) and str(v.get("cik_str", "")).isdigit()}
        return self._mapa.get(ticker.upper().replace("-", "").replace(".", ""))

    def ochok_202(self, ticker: str) -> list[datetime] | None:
        """Aceptaciones (UTC) de los 8-K con ítem 2.02 del emisor, o None sin dato."""
        try:
            cik = self.cik(ticker)
            if cik is None:
                return None
            url = f"{URL_SUBMISSIONS}CIK{cik:010d}.json"
            cuerpo = self.cache.obtener("edgar", url, lambda: self._get(url))
        except ErrorDatos as ex:
            self.fallos += 1
            log.warning("edgar %s: %s", ticker, ex.codigo)
            return None
        rec = (cuerpo.get("filings") or {}).get("recent") or {}
        out = []
        for form, items, acc in zip(rec.get("form", []), rec.get("items", []), rec.get("acceptanceDateTime", [])):
            if str(form).upper() not in ("8-K", "8-K/A") or "2.02" not in str(items).split(","):
                continue
            if not isinstance(acc, str) or not acc.endswith("Z"):
                continue
            try:
                out.append(datetime.fromisoformat(acc[:-1] + "+00:00").astimezone(UTC))
            except ValueError:
                continue
        return out


# ------------------------------------------------------------------ util


def _fecha(f: list) -> date:
    return datetime.fromisoformat(f[0].replace("Z", "+00:00")).astimezone(NY).date()


def _en_ny(d: date, h: time) -> datetime:
    return datetime.combine(d, h, tzinfo=NY).astimezone(UTC)


def _cantidad(equity: float, entrada: float, stop: float) -> int:
    riesgo = entrada - stop
    return math.floor(equity * RIESGO_EQUITY / riesgo) if riesgo > 0 else 0


# ------------------------------------------------------------------ PEAD


def rvol_diario(filas: list[list], i: int, n: int = 20) -> float | None:
    previas = [f[5] for f in filas[max(0, i - n):i]]
    if len(previas) < n:
        return None
    prom = sum(previas) / n
    return filas[i][5] / prom if prom > 0 else None


def correr_pead(cfg, cliente, cache: datos.Cache, universo: list, desde: date, hasta: date, p: Parametros,
                edgar: EdgarMinimo | None = None, evento: str = "edgar") -> Resultado:
    res = Resultado()
    cand = candidatos_gap(cfg, cliente, cache, universo, desde, hasta, res)
    edgar = edgar or (EdgarMinimo(cache) if evento == "edgar" else None)
    sesiones_idx = {d: i for i, d in enumerate(cand.todas)}
    eventos: dict[str, list[datetime] | None] = {}
    noticias_dia: dict[date, list | None] = {}

    def _titulares(d: date, previa: date | None, simbolos: list[str]) -> list | None:
        """Noticias de resultados entre el cierre previo y la apertura de `d` (una vez por día)."""
        if d not in noticias_dia:
            desde_n = _en_ny(previa, CIERRE) if previa else _en_ny(d - timedelta(days=1), CIERRE)
            try:
                nots = datos.noticias(cliente, cache, simbolos, desde_n, _en_ny(d, APERTURA))
                noticias_dia[d] = [n for n in nots if titular_de_resultados(n.titular)]
            except ErrorDatos:
                noticias_dia[d] = None
        return noticias_dia[d]
    for d in sorted(cand.por_dia):
        simbolos_dia = list(cand.por_dia[d])
        for t, gap in cand.por_dia[d].items():
            filas = cand.diarias.get(t, [])
            idx = next((i for i, f in enumerate(filas) if _fecha(f) == d), None)
            if idx is None:
                res.sin_dato["diaria"] += 1
                continue
            o, c = filas[idx][1], filas[idx][4]
            if not (cfg.universo.precio_min <= o <= PEAD_PRECIO_MAX):
                res.descartes_senal["precio"] += 1
                continue
            rv = rvol_diario(filas, idx)
            if rv is None:
                res.sin_dato["rvol_diario"] += 1
                continue
            if rv < PEAD_RVOL_MIN:
                res.descartes_senal["rvol"] += 1
                continue
            res.embudo["gap_rvol_precio"] += 1
            i_ses = sesiones_idx.get(d)
            previa = cand.todas[i_ses - 1] if i_ses else None
            if evento == "edgar":
                if t not in eventos:
                    eventos[t] = edgar.ochok_202(t)
                acc = eventos[t]
                if acc is None:
                    res.sin_dato["edgar"] += 1
                    continue
                desde_ev = _en_ny(previa, CIERRE) if previa else _en_ny(d - timedelta(days=1), CIERRE)
                hasta_ev = _en_ny(d, APERTURA)
                if not any(desde_ev <= a <= hasta_ev for a in acc):
                    res.descartes_senal["sin_8k_202"] += 1
                    continue
                res.embudo["con_8k_202"] += 1
                catalizador = "8-K 2.02"
            else:
                nots = _titulares(d, previa, simbolos_dia)
                if nots is None:
                    res.sin_dato["noticias"] += 1
                    continue
                propias = [n for n in nots if t in n.simbolos]
                if not propias:
                    res.descartes_senal["sin_titular_resultados"] += 1
                    continue
                res.embudo["con_titular_resultados"] += 1
                catalizador = propias[0].titular
            entrada = c * (1 + p.slippage)
            stop = entrada * (1 - PEAD_STOP)
            r_unidad = entrada - stop
            cantidad = _cantidad(p.equity_inicial, entrada, stop)
            if cantidad < 1:
                res.no_entradas["tamano_cero"] += 1
                continue
            siguientes = filas[idx + 1: idx + 1 + PEAD_DIAS]
            if len(siguientes) < PEAD_DIAS:
                res.no_entradas["sin_dias_posteriores"] += 1
                continue
            salida_p, motivo, salida_t = None, None, None
            maximo, minimo = entrada, entrada
            for f in siguientes:
                _, fo, fh, fl, fc, _ = f
                if fo <= stop:
                    # El gap nocturno pasó por encima del stop: se sale a la
                    # apertura y la pérdida es mayor que 1 R. Motivo aparte
                    # para medir cuánto cuesta dormir con la posición.
                    salida_p, motivo, salida_t = fo, "stop_gap", _fecha(f)
                    minimo = min(minimo, fo)
                    break
                if fl <= stop:
                    salida_p, motivo, salida_t = stop, "stop", _fecha(f)
                    minimo = min(minimo, fl)
                    maximo = max(maximo, fh)
                    break
                maximo, minimo = max(maximo, fh), min(minimo, fl)
            if salida_p is None:
                salida_p, motivo, salida_t = siguientes[-1][4], "cierre", _fecha(siguientes[-1])
            ejec = salida_p * (1 - p.slippage)
            momento = _en_ny(d, CIERRE)
            senal = Senal(t, d, momento, entrada, stop, 1, gap, rv, catalizador)
            res.senales.append(senal)
            res.trades.append(Trade(senal, cantidad, entrada, stop, entrada + 2 * r_unidad, momento, entrada,
                                    _en_ny(salida_t, CIERRE), ejec, motivo, (ejec - entrada) / r_unidad,
                                    (ejec - entrada) * cantidad, (maximo - entrada) / r_unidad, (entrada - minimo) / r_unidad))
    res.embudo["senal"] = len(res.senales)
    res.embudo["trades"] = len(res.trades)
    res.sesiones = len(cand.sesiones)
    if edgar is not None:
        res.clasificaciones["edgar_fallos"] = edgar.fallos
    return res


# ---------------------------------------------------------- seguimiento


def correr_seguimiento(cfg, cliente, cache: datos.Cache, universo: list, desde: date, hasta: date, clasificador,
                       p: Parametros) -> Resultado:
    res = Resultado()
    cand = candidatos_gap(cfg, cliente, cache, universo, desde, hasta, res)
    horas = timedelta(hours=cfg.catalizador.horas_maximas)
    liquidacion_min = MINUTOS_ANTES_DEL_CIERRE
    for d in sorted(cand.por_dia):
        cands = cand.por_dia[d]
        i_ses = cand.todas.index(d)
        if i_ses + 1 >= len(cand.todas):
            continue
        d2 = cand.todas[i_ses + 1]
        fin_ventana = _en_ny(d, cfg.senal.ventana_fin)
        try:
            nots = datos.noticias(cliente, cache, list(cands), fin_ventana - horas, fin_ventana)
        except ErrorDatos:
            res.sin_dato["noticias"] += len(cands)
            continue
        nivel1 = []
        for t in cands:
            propias = [n for n in nots if t in n.simbolos]
            if not propias:
                continue
            res.embudo["con_noticias"] += 1
            if any(cat.veto(n.titular, n.resumen, cfg.catalizador) for n in propias):
                res.descartes_senal["veto"] += 1
                continue
            clasif = [clasificador.clasificar(n, cfg, res) for n in propias]
            if any(cat.operable(c, cfg.catalizador) and c.nivel == 1 for c in clasif):
                nivel1.append(t)
        if not nivel1:
            continue
        res.embudo["nivel_1"] += len(nivel1)
        velas_d2 = datos.minutos(cliente, cache, nivel1, d2, APERTURA, CIERRE)
        for t in nivel1:
            filas = cand.diarias.get(t, [])
            dia1 = next((f for f in filas if _fecha(f) == d), None)
            if dia1 is None:
                res.sin_dato["diaria"] += 1
                continue
            maximo_d1 = dia1[2]
            velas = [v for v in datos.a_velas(velas_d2.get(t, [])) if reglas.es_regular(v, cfg.senal)]
            if not velas:
                res.sin_dato["velas_dia2"] += 1
                continue
            senal = None
            previa = None
            for i, v in enumerate(velas):
                hora = reglas.hora_local(v.t + reglas.UN_MINUTO, cfg.senal)
                if SEG_DESDE <= hora <= SEG_HASTA and v.c > maximo_d1 and (previa is None or previa.c <= maximo_d1) \
                        and cfg.universo.precio_min <= v.c <= cfg.universo.precio_max:
                    senal = (i, v)
                    break
                previa = v
            if senal is None:
                res.descartes_senal["sin_ruptura_dia1"] += 1
                continue
            i, v = senal
            if i + 1 >= len(velas):
                res.no_entradas["sin_velas_tras_senal"] += 1
                continue
            fv = velas[i + 1]
            entrada = fv.o * (1 + p.slippage)
            stop = entrada * (1 - SEG_STOP)
            r_unidad = entrada - stop
            cantidad = _cantidad(p.equity_inicial, entrada, stop)
            if cantidad < 1:
                res.no_entradas["tamano_cero"] += 1
                continue
            liquidacion = _en_ny(d2, CIERRE) - timedelta(minutes=liquidacion_min)
            maximo, minimo = fv.h, fv.l
            salida_v, precio, motivo = None, None, None
            for k, w in enumerate(velas[i + 1:]):
                if w.t >= liquidacion:
                    salida_v, precio, motivo = w, w.o, "cierre"
                    break
                if w.l <= stop:
                    salida_v, precio, motivo = w, min(stop, w.o), "stop"
                    break
                maximo, minimo = max(maximo, w.h), min(minimo, w.l)
            if salida_v is None:
                salida_v, precio, motivo = velas[-1], velas[-1].c, "cierre"
            maximo, minimo = max(maximo, salida_v.h), min(minimo, salida_v.l)
            ejec = precio * (1 - p.slippage)
            s = Senal(t, d2, v.t + reglas.UN_MINUTO, v.c, stop, 1, cands[t], 0.0, f"nivel 1 del {d}")
            res.senales.append(s)
            res.trades.append(Trade(s, cantidad, entrada, stop, entrada + 2 * r_unidad, fv.t, entrada, salida_v.t, ejec,
                                    motivo, (ejec - entrada) / r_unidad, (ejec - entrada) * cantidad,
                                    (maximo - entrada) / r_unidad, (entrada - minimo) / r_unidad))
    res.embudo["senal"] = len(res.senales)
    res.embudo["trades"] = len(res.trades)
    res.sesiones = len(cand.sesiones)
    return res
