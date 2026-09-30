"""Reglas v2 de entrada y de riesgo, como funciones puras.

Una sola implementación para el backtest, la sombra y (más adelante) el
hunter y el ejecutor. Todo número sale de `ConfigV2`; aquí solo hay
lógica. Un dato que falta devuelve "no" con su motivo, nunca un cero.

Velas: 1 minuto, `t` = INICIO del minuto en UTC; una vela cierra en
t + 1 min. La hora de cada regla se lee en `senal.zona_horaria`.
"""

from __future__ import annotations

import math
from statistics import fmean
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from estrategia_v2.config import ConfigV2, Riesgo, Senal

UN_MINUTO = timedelta(minutes=1)


@dataclass(frozen=True)
class Vela:
    t: datetime
    o: float
    h: float
    l: float   # noqa: E741 -- mismo nombre que el feed
    c: float
    v: float


def hora_local(t: datetime, s: Senal) -> time:
    return t.astimezone(ZoneInfo(s.zona_horaria)).time()


def es_regular(v: Vela, s: Senal) -> bool:
    """Sesión regular desde el inicio del rango de apertura. El fin de la
    ventana de entrada es anterior al cierre, así que no hace falta más."""
    return hora_local(v.t, s) >= s.rango_apertura_inicio


def rango_apertura(velas_hoy: list[Vela], s: Senal) -> tuple[float, float] | None:
    """(máximo, mínimo) de las velas que empiezan en [inicio, fin)."""
    dentro = [v for v in velas_hoy
              if s.rango_apertura_inicio <= hora_local(v.t, s) < s.rango_apertura_fin]
    if not dentro:
        return None
    return max(v.h for v in dentro), min(v.l for v in dentro)


def vwap(velas: list[Vela]) -> float | None:
    """Precio típico ponderado por volumen (misma fórmula que el hunter v1)."""
    vol = sum(v.v for v in velas)
    if vol <= 0:
        return None
    return sum(fmean((v.h, v.l, v.c)) * v.v for v in velas) / vol


def minuto_de_sesion(t: datetime, s: Senal) -> int:
    """Minutos desde el inicio del rango de apertura (0 = la vela de las 9:30)."""
    local = t.astimezone(ZoneInfo(s.zona_horaria))
    inicio = local.replace(hour=s.rango_apertura_inicio.hour, minute=s.rango_apertura_inicio.minute,
                           second=0, microsecond=0)
    return (local - inicio) // UN_MINUTO


def volumen_acumulado(velas_regulares: list[Vela], hasta_minuto: int, s: Senal) -> float:
    return sum(v.v for v in velas_regulares if minuto_de_sesion(v.t, s) <= hasta_minuto)


def rvol(hoy: float, previos: list[float | None], s: Senal) -> float | None:
    """Acumulado de hoy / promedio de los `rvol_dias` previos a la misma
    hora. Menos sesiones de las pedidas, o alguna sin dato: None."""
    if len(previos) < s.rvol_dias or any(p is None for p in previos[-s.rvol_dias:]):
        return None
    prom = sum(previos[-s.rvol_dias:]) / s.rvol_dias
    return hoy / prom if prom > 0 else None


@dataclass
class Evaluacion:
    ok: bool
    fallos: list[str] = field(default_factory=list)
    orb_alto: float | None = None
    orb_bajo: float | None = None
    precio: float | None = None
    rvol: float | None = None


def evaluar_ruptura(
    velas_hoy: list[Vela], i: int, *, gap_oficial: float | None, rvol_valor: float | None,
    spread_pct: float | None, indice_sobre_vwap: bool | None, cfg: ConfigV2,
) -> Evaluacion:
    """¿La vela `i` (ya cerrada) dispara la v2? Todas las condiciones.

    Ruptura = la vela cierra por ENCIMA del máximo del rango de apertura
    y la anterior no (un cruce). Cada cruce se evalúa; el caller toma el
    primero que pasa todo y hace una sola entrada por símbolo y día."""
    s = cfg.senal
    ev = Evaluacion(ok=False)
    vela = velas_hoy[i]
    cierre_local = hora_local(vela.t + UN_MINUTO, s)
    regulares = [v for v in velas_hoy[:i + 1] if es_regular(v, s)]
    orb = rango_apertura(regulares, s)
    if orb is not None:
        ev.orb_alto, ev.orb_bajo = orb
    ev.precio, ev.rvol = vela.c, rvol_valor

    if not (s.ventana_inicio <= cierre_local <= s.ventana_fin):
        ev.fallos.append("fuera_de_ventana")
    if gap_oficial is None:
        ev.fallos.append("gap_sin_dato")
    elif gap_oficial < s.gap_min_pct:
        ev.fallos.append("gap")
    if orb is None:
        ev.fallos.append("rango_sin_dato")
    else:
        previa = velas_hoy[i - 1] if i > 0 else None
        cruza = vela.c > orb[0] and (previa is None or previa.c <= orb[0])
        if not cruza:
            ev.fallos.append("sin_ruptura")
        anteriores = [v.v for v in regulares[:-1]]
        if not anteriores:
            ev.fallos.append("vol_ruptura_sin_dato")
        elif vela.v < s.vela_ruptura_vol_min_x * (sum(anteriores) / len(anteriores)):
            ev.fallos.append("vol_ruptura")
    precio_vwap = vwap(regulares)
    if s.precio_sobre_vwap and (precio_vwap is None or vela.c <= precio_vwap):
        ev.fallos.append("vwap" if precio_vwap is not None else "vwap_sin_dato")
    if rvol_valor is None:
        ev.fallos.append("rvol_sin_dato")
    elif rvol_valor < s.rvol_min:
        ev.fallos.append("rvol")
    if spread_pct is None:
        ev.fallos.append("spread_sin_dato")
    elif spread_pct > s.spread_max_pct:
        ev.fallos.append("spread")
    if s.indice_sobre_vwap:
        if indice_sobre_vwap is None:
            ev.fallos.append("indice_sin_dato")
        elif not indice_sobre_vwap:
            ev.fallos.append("indice")
    ev.ok = not ev.fallos
    return ev


# -------------------------------------------------------------------- riesgo


def stop_inicial(entrada: float, orb_bajo: float, r: Riesgo) -> float | None:
    """Mínimo del rango de apertura, acotado: más cerca que `stop_min_pct`
    se aleja hasta ese mínimo; más lejos que `stop_max_pct` no se entra."""
    if entrada <= 0:
        return None
    distancia = (entrada - orb_bajo) / entrada
    if distancia > r.stop_max_pct:
        return None
    if distancia < r.stop_min_pct:
        return entrada * (1 - r.stop_min_pct)
    return orb_bajo


def objetivo(entrada: float, stop: float, r: Riesgo) -> float:
    return entrada + r.objetivo_r * (entrada - stop)


def tamano(equity: float, efectivo: float, entrada: float, stop: float, nivel: int, r: Riesgo) -> int:
    """Acciones enteras: (equity × riesgo% × fracción del nivel) / (entrada − stop),
    topado por `tope_posicion_pct_equity` y por el efectivo (sin margen)."""
    riesgo_accion = entrada - stop
    if riesgo_accion <= 0 or entrada <= 0 or nivel not in r.tamano_por_nivel:
        return 0
    por_riesgo = math.floor(equity * r.riesgo_pct_equity * r.tamano_por_nivel[nivel] / riesgo_accion)
    por_tope = math.floor(min(equity * r.tope_posicion_pct_equity, efectivo) / entrada)
    return max(0, min(por_riesgo, por_tope))
