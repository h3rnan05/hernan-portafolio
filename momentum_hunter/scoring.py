"""Score compuesto 0-100 -- Prompt 6: 40% momentum, 25% catalizador, 20%
liquidez, 15% gestión del riesgo. CERO valoración fundamental, cero
dividendos, cero P/E, cero ROE -- ese es el criterio del Investment
Analyst (`screener/`), no el de este bot.

A diferencia de `screener.scoring.puntuar` (que normaliza por percentil
CROSS-SECTIONAL contra el resto del universo del día), aquí cada
sub-score es ABSOLUTO -- un scanner de momentum necesita poder puntuar un
ticker aislado en cualquier momento del día, no solo al final de una
corrida completa del universo. Los umbrales de cada tramo son fijos y
documentados, igual de "editoriales" que los umbrales de percentil del
screener, solo que expresados en unidades absolutas (RVOL, %, ATR/precio)
en vez de percentiles.

`riesgo` mide qué tan MANEJABLE es el riesgo del setup (volatilidad en
una banda operable, float no extremadamente ilíquido, precio no en
territorio de manipulación fácil) -- nunca "qué tan buena es la
oportunidad" (eso ya lo capturan momentum/catalizador). Un float
ultra-bajo puntúa BAJO en riesgo aunque sea la razón por la que un short
squeeze es explosivo -- son dos preguntas distintas a propósito."""

from __future__ import annotations

from dataclasses import dataclass, field

from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.models import FactoresMomentum, Metadata

# --- Fuerza editorial de cada tipo de catalizador (0-100) ---
FUERZA_CATALIZADOR: dict[str, float] = {
    "fda": 100.0, "adquisicion": 100.0, "regulatorio": 85.0, "earnings": 80.0,
    "contrato": 80.0, "guidance": 75.0, "insider_buying": 70.0,
    "nuevo_cliente": 70.0, "upgrade_analista": 65.0, "patente": 65.0,
    "buyback": 60.0, "rumor": 50.0,
}
BONUS_POR_FUENTE_ADICIONAL = 5.0

# --- Bandas de momentum ---
RVOL_PARA_SCORE_MAXIMO = 10.0
GAP_PARA_SCORE_MAXIMO = 0.20

# --- Bandas de riesgo ---
ATR_PCT_BANDA_SANA = (0.03, 0.15)   # ATR/precio dentro de esto = riesgo bien definido
ATR_PCT_TECHO_PENALIZACION = 0.40
FLOAT_RIESGO_BAJO = 5_000_000
FLOAT_RIESGO_ALTO = 50_000_000


@dataclass(frozen=True)
class Puntuacion:
    ticker: str
    score_total: float
    sub: dict[str, float] = field(default_factory=dict)


def _rsi_a_score(rsi: float) -> float:
    """RSI < 50: poco momentum (escala 0-40). 50-80: la zona típica de
    momentum saludable (escala 40-100). > 80: sobrecomprado/agotado,
    decae de vuelta -- un RSI de 95 no es "más momentum" que uno de 75,
    es una señal de reversión inminente."""
    if rsi < 50.0:
        return max(0.0, rsi / 50.0 * 40.0)
    if rsi <= 80.0:
        return 40.0 + (rsi - 50.0) / 30.0 * 60.0
    return max(50.0, 100.0 - (rsi - 80.0) * 2.5)


def momentum_score(factores: FactoresMomentum) -> float | None:
    componentes: dict[str, tuple[float, float]] = {}  # nombre -> (valor 0-100, peso interno)
    if factores.rvol is not None:
        componentes["rvol"] = (min(100.0, factores.rvol / RVOL_PARA_SCORE_MAXIMO * 100.0), 0.35)
    if factores.gap_pct is not None:
        componentes["gap"] = (min(100.0, abs(factores.gap_pct) / GAP_PARA_SCORE_MAXIMO * 100.0), 0.15)
    if factores.distancia_max_52s is not None:
        componentes["distancia_52s"] = (max(0.0, min(100.0, factores.distancia_max_52s * 100.0)), 0.15)
    if factores.rsi is not None:
        componentes["rsi"] = (_rsi_a_score(factores.rsi), 0.10)
    if factores.macd is not None and factores.macd_signal is not None:
        componentes["macd"] = (100.0 if factores.macd > factores.macd_signal else 0.0, 0.10)
    # `breakout_20d` es un bool sin estado "no calculado" (ver
    # `factors/momentum.breakout_nd`: devuelve False tanto si no rompió
    # como si no había historia suficiente) -- por eso solo se suma al
    # score cuando ya hay al menos otro factor real disponible; si no,
    # un ticker sin ningún dato real terminaría con momentum_score=0.0
    # en vez de None, y "0 de 100" no es lo mismo que "no lo sé".
    if componentes:
        componentes["breakout"] = (100.0 if factores.breakout_20d else 0.0, 0.15)

    peso_total = sum(p for _, p in componentes.values())
    if peso_total == 0:
        return None
    return round(sum(v * p for v, p in componentes.values()) / peso_total, 1)


def catalyst_score(catalizador: Catalizador | None) -> float:
    """0 si no hay catalizador CONFIRMADO -- nunca inventa fuerza donde
    no hay nada verificable (coherente con Prompt 4)."""
    if catalizador is None or not catalizador.confirmado:
        return 0.0
    base = FUERZA_CATALIZADOR.get(catalizador.tipo, 50.0)
    bonus = BONUS_POR_FUENTE_ADICIONAL * len(catalizador.fuentes_adicionales)
    return round(min(100.0, base + bonus), 1)


def liquidez_score(precio: float, volumen_promedio: float | None, cfg: MomentumConfig) -> float:
    """Basado en el dollar-volume promedio (precio × volumen promedio) --
    liquidez ESTRUCTURAL del papel, no el volumen inusual de hoy (eso ya
    lo captura `momentum_score` vía RVOL; contarlo dos veces sería
    duplicar la misma señal bajo dos pesos distintos)."""
    if volumen_promedio is None or volumen_promedio <= 0 or precio <= 0:
        return 0.0
    dollar_volume = precio * volumen_promedio
    piso = precio * cfg.volumen_promedio_min
    if dollar_volume >= 20_000_000:
        return 100.0
    if dollar_volume >= 10_000_000:
        return 90.0
    if dollar_volume >= 5_000_000:
        return 75.0
    if dollar_volume >= 2_000_000:
        return 60.0
    if dollar_volume >= piso:
        return 40.0
    return 20.0


def _riesgo_volatilidad(factores: FactoresMomentum, precio: float) -> float | None:
    if factores.atr is None or precio <= 0:
        return None
    atr_pct = factores.atr / precio
    piso, techo = ATR_PCT_BANDA_SANA
    if piso <= atr_pct <= techo:
        return 100.0
    if atr_pct < piso:
        return max(0.0, atr_pct / piso * 100.0)
    return max(0.0, 100.0 - (atr_pct - techo) / (ATR_PCT_TECHO_PENALIZACION - techo) * 100.0)


def _riesgo_float(meta: Metadata) -> float | None:
    if meta.shares_float is None:
        return None
    if meta.shares_float >= FLOAT_RIESGO_ALTO:
        return 100.0
    if meta.shares_float <= FLOAT_RIESGO_BAJO:
        return 30.0  # explosivo, pero difícil de administrar (spreads, halts) -- ver docstring
    rango = FLOAT_RIESGO_ALTO - FLOAT_RIESGO_BAJO
    return 30.0 + (meta.shares_float - FLOAT_RIESGO_BAJO) / rango * 70.0


def _riesgo_precio(precio: float) -> float:
    if precio < 1.0:
        return 40.0
    if precio < 2.0:
        return 60.0
    if precio < 5.0:
        return 80.0
    return 100.0


def riesgo_score(meta: Metadata, factores: FactoresMomentum, precio: float) -> float | None:
    componentes: dict[str, tuple[float, float]] = {}
    vol = _riesgo_volatilidad(factores, precio)
    if vol is not None:
        componentes["volatilidad"] = (vol, 0.5)
    flt = _riesgo_float(meta)
    if flt is not None:
        componentes["float"] = (flt, 0.3)
    componentes["precio"] = (_riesgo_precio(precio), 0.2)

    peso_total = sum(p for _, p in componentes.values())
    if peso_total == 0:
        return None
    return round(sum(v * p for v, p in componentes.values()) / peso_total, 1)


def puntuar(
    ticker: str, precio: float, volumen_promedio: float | None,
    factores: FactoresMomentum, catalizador: Catalizador | None,
    meta: Metadata, cfg: MomentumConfig,
) -> Puntuacion:
    """Score final -- re-normaliza sobre los pesos de los sub-scores
    disponibles (mismo principio que `screener.scoring.puntuar`: un dato
    faltante no castiga con 0, se excluye del promedio ponderado)."""
    cfg.validar()
    sub: dict[str, float] = {}

    m = momentum_score(factores)
    if m is not None:
        sub["momentum"] = m
    sub["catalizador"] = catalyst_score(catalizador)
    sub["liquidez"] = liquidez_score(precio, volumen_promedio, cfg)
    r = riesgo_score(meta, factores, precio)
    if r is not None:
        sub["riesgo"] = r

    peso_disp, acum = 0.0, 0.0
    for factor, peso in cfg.pesos.items():
        s = sub.get(factor)
        if s is not None:
            acum += s * peso
            peso_disp += peso
    total = round(acum / peso_disp, 1) if peso_disp > 0 else 0.0
    return Puntuacion(ticker=ticker, score_total=total, sub=sub)
