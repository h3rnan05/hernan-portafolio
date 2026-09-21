"""Descubrimiento "movers primero", EN SOMBRA (pedido 2026-09-21).

POR QUÉ. El embudo actual muere en el catalizador: el 21/9, slot 3, 332
small caps pasaron precio/liquidez, 324 tenían alguna noticia y 0 tenían
un catalizador dentro de la ventana de 3 días. Los titulares que Yahoo
devuelve para small caps son casi siempre viejos (resúmenes de earnings
de julio). Este módulo invierte el orden: primero encuentra lo que YA se
está moviendo hoy (screener de Yahoo), confirma que entra dinero (RVOL
ajustado a la hora, volumen en dólares, precio sobre VWAP) y solo
entonces busca la noticia, como ETIQUETA (clase A con catalizador, clase
B sin catalizador pero con RVOL muy alto), no como compuerta.

SOMBRA. No escribe watchlist.json, no manda Telegram, no toca el
escaneo ni el rechequeo. Solo deja telemetría propia
(`telemetria/{fecha}/{fuente}/movers.jsonl`) para medir, durante unas
semanas, si esta forma de mirar el mercado produce candidatas mejores.
Apagado por omisión (`MomentumConfig.movers_sombra`); en el VPS se
enciende con MOMENTUM_MOVERS_SOMBRA=1 en paper.env.

UMBRALES. Todos en `config.py`, con prefijo `movers_`. Son puntos de
partida razonados, NO evidencia: la sombra existe para producirla.

DATOS. Regla 6 del CLAUDE.md: un campo que Yahoo no devuelve es None y la
candidata se rechaza con motivo explícito; jamás `float(v or 0)`.
Fail-closed: si el screener falla o viene vacío, la corrida registra el
error y termina sin candidatas.

Este módulo pertenece a la fase research + signal: no importa nada de
IA, brókers ni ejecución (regla 2)."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum_hunter import telemetria
from momentum_hunter.catalysts.ancla import ancla_ok
from momentum_hunter.catalysts.detector import NewsProvider, YahooNewsProvider, detectar_catalizador
from momentum_hunter.config import CONFIG, MomentumConfig
from momentum_hunter.data.provider import DataProvider, YahooProvider
from momentum_hunter.factors import intradia as fi
from momentum_hunter.models import BarraIntradia, Barras

log = logging.getLogger("momentum_hunter.movers")

NY = ZoneInfo("America/New_York")
ARCHIVO_TELEMETRIA = "movers.jsonl"
MINUTOS_SESION = 390.0   # 9:30 a 16:00 ET
# Campos de cada cotización del screener que se exigen. Sin uno de ellos
# la candidata se rechaza con motivo `campo_faltante:<campo>`.
CAMPOS_COTIZACION = {
    "symbol": "ticker",
    "regularMarketPrice": "precio",
    "regularMarketChangePercent": "cambio_pct",
    "regularMarketVolume": "volumen_dia",
    "marketCap": "market_cap",
}


@dataclass(frozen=True)
class Mover:
    """Una fila del screener, ya validada campo a campo."""
    ticker: str
    precio: float
    cambio_pct: float
    volumen_dia: float
    market_cap: float
    nombre: str | None = None
    hora_dato: str | None = None   # regularMarketTime en ISO UTC: para medir frescura


@dataclass
class Candidata:
    """Todos los números de una candidata, pasen o no el filtro. Lo que
    no se pudo calcular queda en None y el motivo lo dice."""
    ticker: str
    precio_screener: float
    cambio_pct: float
    volumen_dia_screener: float
    market_cap: float
    hora_dato: str | None = None
    nombre: str | None = None
    velas_hoy: int | None = None
    volumen_dia_intradia: float | None = None
    promedio_20d: float | None = None
    fraccion_sesion: float | None = None
    rvol_ajustado: float | None = None
    volumen_dolares: float | None = None
    vwap: float | None = None
    precio_intradia: float | None = None
    sobre_vwap: bool | None = None
    pasa_filtro: bool = False
    motivo_rechazo: str | None = None
    clase: str | None = None          # "A" | "B" | None
    catalizador_tipo: str | None = None
    catalizador_titular: str | None = None
    catalizador_fecha: str | None = None
    ancla_motivo: str | None = None
    titulares: int | None = None


@dataclass
class Corrida:
    inicio_ts: str
    timestamp: str = ""
    fuente: str = ""
    screener_ok: bool = False
    error: str | None = None
    embudo: dict = field(default_factory=dict)
    rechazos: Counter = field(default_factory=Counter)
    candidatas: list = field(default_factory=list)

    def como_dict(self) -> dict:
        d = asdict(self)
        d["rechazos"] = dict(self.rechazos)
        d["candidatas"] = [asdict(c) if isinstance(c, Candidata) else c for c in self.candidatas]
        return d


# ───────────────────────── screener ─────────────────────────

def construir_query(cfg: MomentumConfig = CONFIG):
    """EquityQuery de yfinance con los cinco filtros del pedido. Se importa
    acá y no arriba para que el módulo cargue aunque yfinance falte (la
    corrida entonces falla cerrada con motivo, ver `consultar_screener`)."""
    from yfinance import EquityQuery as Q
    return Q("and", [
        Q("eq", ["region", "us"]),
        Q("gte", ["intradayprice", cfg.movers_precio_min]),
        Q("lte", ["intradayprice", cfg.movers_precio_max]),
        Q("lt", ["intradaymarketcap", cfg.movers_market_cap_max]),
        Q("gt", ["dayvolume", cfg.movers_volumen_dia_min]),
        Q("gte", ["percentchange", cfg.movers_cambio_pct_min]),
    ])


def _numero(valor) -> float | None:
    """Solo un número real cuenta. Un None, un texto o un bool no se
    convierten a nada: el dato falta (regla 6)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    return None


def _epoch_a_iso(valor) -> str | None:
    n = _numero(valor)
    if n is None or n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n, tz=UTC).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def parsear_cotizaciones(respuesta, rechazos: Counter | None = None) -> list[Mover]:
    """Filas válidas de la respuesta de `yf.screen`. Cada fila a la que le
    falte un campo obligatorio se cuenta en `rechazos` como
    `campo_faltante:<campo>` y no entra."""
    rechazos = rechazos if rechazos is not None else Counter()
    filas = respuesta.get("quotes") if isinstance(respuesta, dict) else None
    if not isinstance(filas, list):
        return []
    out = []
    for fila in filas:
        if not isinstance(fila, dict):
            rechazos["fila_ilegible"] += 1
            continue
        ticker = fila.get("symbol")
        if not isinstance(ticker, str) or not ticker.strip():
            rechazos["campo_faltante:symbol"] += 1
            continue
        valores = {}
        faltante = None
        for campo, nombre in CAMPOS_COTIZACION.items():
            if campo == "symbol":
                continue
            v = _numero(fila.get(campo))
            if v is None:
                faltante = campo
                break
            valores[nombre] = v
        if faltante is not None:
            rechazos[f"campo_faltante:{faltante}"] += 1
            continue
        nombre = fila.get("shortName") or fila.get("longName")
        out.append(Mover(
            ticker=ticker.strip().upper(), precio=valores["precio"], cambio_pct=valores["cambio_pct"],
            volumen_dia=valores["volumen_dia"], market_cap=valores["market_cap"],
            nombre=nombre if isinstance(nombre, str) else None,
            hora_dato=_epoch_a_iso(fila.get("regularMarketTime")),
        ))
    return out


def consultar_screener(cfg: MomentumConfig = CONFIG, screen=None, rechazos: Counter | None = None) -> list[Mover]:
    """Una llamada al screener, top `movers_top` por % de cambio. Cualquier
    fallo se propaga: el caller decide (la corrida en sombra lo registra
    y termina sin candidatas)."""
    if screen is None:
        import yfinance as yf
        screen = yf.screen
    respuesta = screen(construir_query(cfg), size=cfg.movers_top, sortField="percentchange", sortAsc=False)
    movers = parsear_cotizaciones(respuesta, rechazos)
    movers.sort(key=lambda m: m.cambio_pct, reverse=True)
    return movers[:cfg.movers_top]


# ───────────────────────── dinero entrando ─────────────────────────

def fraccion_sesion(ahora: datetime) -> float | None:
    """Parte de la sesión regular ya transcurrida (0-1). None fuera de
    sesión: un RVOL "ajustado a la hora" no tiene sentido si no hay hora
    de sesión que ajustar. Se usa Nueva York de verdad (con su horario de
    verano), no un offset fijo."""
    local = ahora.astimezone(NY)
    if local.weekday() >= 5:
        return None
    minutos = (local.hour * 60 + local.minute) - (9 * 60 + 30)
    if minutos < 1 or minutos > MINUTOS_SESION:
        return None
    return minutos / MINUTOS_SESION


def rvol_ajustado(volumen_dia: float | None, promedio_20d: float | None, fraccion: float | None) -> float | None:
    """volumen del día / (promedio de 20 días × fracción de sesión). A las
    10:00 ET llevar el 25 % del volumen normal de un día entero ya es un
    RVOL de ~3,9; sin el ajuste, el mismo dato diría 0,25 y nadie
    calificaría antes de las 15:00."""
    if volumen_dia is None or promedio_20d is None or fraccion is None:
        return None
    if promedio_20d <= 0 or fraccion <= 0:
        return None
    return volumen_dia / (promedio_20d * fraccion)


def promedio_volumen_20d(barras: Barras | None) -> float | None:
    """Promedio de volumen de los últimos 20 días COMPLETOS. Yahoo ya
    recorta la vela de hoy en formación (ver data/provider.py); con menos
    de 20 días no se promedia lo que hay: se devuelve None."""
    if barras is None or len(barras.volume) < 20:
        return None
    ultimos = barras.volume[-20:]
    if any(v is None for v in ultimos):
        return None
    return sum(ultimos) / 20.0


def volumen_y_dolares_hoy(bi_hoy: BarraIntradia) -> tuple[float | None, float | None]:
    """Volumen acumulado de la sesión regular de hoy y su valor en dólares
    (Σ cierre×volumen por vela). None sin velas regulares."""
    regulares = [(c, v) for t, c, v in zip(bi_hoy.timestamps, bi_hoy.close, bi_hoy.volume, strict=True)
                 if fi.es_sesion_regular(t)]
    if not regulares:
        return None, None
    return sum(v for _, v in regulares), sum(c * v for c, v in regulares)


def evaluar(mover: Mover, bi: BarraIntradia | None, barras: Barras | None, ahora: datetime,
            cfg: MomentumConfig = CONFIG) -> Candidata:
    """Números de "dinero entrando" para un mover. Nunca inventa: cada
    dato que falte deja None y un motivo; el primer motivo gana."""
    c = Candidata(ticker=mover.ticker, precio_screener=mover.precio, cambio_pct=mover.cambio_pct,
                  volumen_dia_screener=mover.volumen_dia, market_cap=mover.market_cap,
                  hora_dato=mover.hora_dato, nombre=mover.nombre)
    if bi is None or not bi.timestamps:
        c.motivo_rechazo = "sin_intradia"
        return c
    hoy = fi.barras_de_hoy(bi)
    c.velas_hoy = len(hoy.timestamps)
    c.volumen_dia_intradia, c.volumen_dolares = volumen_y_dolares_hoy(hoy)
    c.vwap = fi.vwap_real(hoy)
    c.precio_intradia = hoy.close[-1] if hoy.close else None
    c.promedio_20d = promedio_volumen_20d(barras)
    c.fraccion_sesion = fraccion_sesion(ahora)
    c.rvol_ajustado = rvol_ajustado(c.volumen_dia_intradia, c.promedio_20d, c.fraccion_sesion)
    if c.vwap is not None and c.precio_intradia is not None:
        c.sobre_vwap = c.precio_intradia > c.vwap

    if c.volumen_dia_intradia is None:
        c.motivo_rechazo = "sin_velas_regulares_hoy"
    elif c.promedio_20d is None:
        c.motivo_rechazo = "sin_promedio_20d"
    elif c.fraccion_sesion is None:
        c.motivo_rechazo = "fuera_de_sesion"
    elif c.rvol_ajustado is None:
        c.motivo_rechazo = "rvol_no_calculable"
    elif c.rvol_ajustado < cfg.movers_rvol_min:
        c.motivo_rechazo = "rvol_bajo"
    elif c.volumen_dolares is None or c.volumen_dolares < cfg.movers_volumen_dolares_min:
        c.motivo_rechazo = "dolares_bajos"
    elif c.sobre_vwap is None:
        c.motivo_rechazo = "vwap_no_calculable"
    elif not c.sobre_vwap:
        c.motivo_rechazo = "bajo_vwap"
    else:
        c.pasa_filtro = True
    return c


# ───────────────────────── catalizador como etiqueta ─────────────────────────

def etiquetar(c: Candidata, noticias: NewsProvider | None, cfg: MomentumConfig = CONFIG,
              hoy=None) -> Candidata:
    """Clase A = catalizador confirmado que además pasa el ancla (es de
    ESTE ticker). Clase B = sin catalizador pero RVOL >= movers_rvol_clase_b.
    El resto se descarta con motivo. Solo se llama para las que pasaron
    el filtro: las noticias son la parte cara y frágil."""
    titulares = noticias.titulares(c.ticker) if noticias is not None else []
    c.titulares = len(titulares)
    catalizador = detectar_catalizador(titulares, cfg, hoy) if titulares else None
    if catalizador is not None:
        ok, motivo = ancla_ok(c.ticker, c.nombre, catalizador.titular)
        c.ancla_motivo = motivo
        if ok:
            c.clase = "A"
            c.catalizador_tipo = catalizador.tipo
            c.catalizador_titular = catalizador.titular
            c.catalizador_fecha = getattr(catalizador, "fecha", None)
            return c
    if c.rvol_ajustado is not None and c.rvol_ajustado >= cfg.movers_rvol_clase_b:
        c.clase = "B"
        return c
    c.motivo_rechazo = "sin_catalizador_y_rvol_insuficiente_para_clase_b"
    return c


# ───────────────────────── la corrida en sombra ─────────────────────────

def _registrar(corrida: Corrida, dir_telemetria: Path) -> Path | None:
    try:
        fecha = corrida.timestamp[:10]
        ruta = dir_telemetria / fecha / corrida.fuente / ARCHIVO_TELEMETRIA
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(corrida.como_dict(), ensure_ascii=False) + "\n")
        return ruta
    except Exception as ex:  # la telemetría jamás tumba la corrida
        log.warning("no se pudo guardar la telemetría de movers: %s", type(ex).__name__)
        return None


def correr_sombra(cfg: MomentumConfig = CONFIG, provider: DataProvider | None = None,
                  noticias: NewsProvider | None = None, screen=None, ahora: datetime | None = None,
                  dir_telemetria: Path = telemetria.DIR_TELEMETRIA) -> Corrida:
    """Una corrida completa: screener → dinero entrando → etiqueta →
    telemetría. Devuelve la corrida registrada. Nunca escribe la
    watchlist ni manda avisos."""
    inicio = ahora or datetime.now(UTC)
    corrida = Corrida(inicio_ts=inicio.isoformat(timespec="seconds"), fuente=telemetria.resolver_fuente())
    embudo = {"screener": 0, "validas": 0, "con_intradia": 0, "pasan_filtro": 0, "clase_a": 0, "clase_b": 0, "descartadas": 0}
    try:
        movers = consultar_screener(cfg, screen, corrida.rechazos)
        corrida.screener_ok = True
    except Exception as ex:
        # Fail-closed: sin screener no hay candidatas, y queda dicho por qué.
        corrida.error = f"screener:{type(ex).__name__}"
        log.warning("movers: el screener falló (%s); corrida sin candidatas", type(ex).__name__)
        movers = []
    embudo["screener"] = len(movers) + sum(v for k, v in corrida.rechazos.items() if k.startswith("campo_faltante") or k == "fila_ilegible")
    embudo["validas"] = len(movers)
    if corrida.screener_ok and not movers:
        corrida.error = corrida.error or "screener_vacio"

    if movers:
        provider = provider or YahooProvider()
        tickers = [m.ticker for m in movers]
        try:
            intradia = provider.barras_intradia(tickers, cfg.intervalo_intradia, cfg.periodo_intradia)
        except Exception as ex:
            intradia = {}
            corrida.rechazos[f"intradia:{type(ex).__name__}"] += 1
        try:
            diarias = provider.barras(tickers, dias=40)
        except Exception as ex:
            diarias = {}
            corrida.rechazos[f"diarias:{type(ex).__name__}"] += 1
        noticias = noticias if noticias is not None else YahooNewsProvider()
        for m in movers:
            c = evaluar(m, intradia.get(m.ticker), diarias.get(m.ticker), inicio, cfg)
            if c.velas_hoy:
                embudo["con_intradia"] += 1
            if c.pasa_filtro:
                embudo["pasan_filtro"] += 1
                c = etiquetar(c, noticias, cfg, inicio.date())
                if c.clase == "A":
                    embudo["clase_a"] += 1
                elif c.clase == "B":
                    embudo["clase_b"] += 1
            if c.motivo_rechazo:
                embudo["descartadas"] += 1
                corrida.rechazos[c.motivo_rechazo] += 1
            corrida.candidatas.append(c)

    corrida.embudo = embudo
    corrida.timestamp = datetime.now(UTC).isoformat(timespec="seconds") if ahora is None else (inicio + timedelta(seconds=1)).isoformat(timespec="seconds")
    ruta = _registrar(corrida, dir_telemetria)
    log.info("movers (sombra): screener=%d validas=%d pasan=%d A=%d B=%d error=%s telemetria=%s",
             embudo["screener"], embudo["validas"], embudo["pasan_filtro"], embudo["clase_a"], embudo["clase_b"],
             corrida.error, ruta)
    return corrida
