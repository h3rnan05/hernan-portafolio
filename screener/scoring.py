"""Motor de scoring: normalización cross-sectional a 0-100 + score compuesto.

'Cross-sectional' = cada acción se puntúa RELATIVA al universo del día, no
contra un umbral absoluto. Es como piensan los fondos cuantitativos: no
importa el valor absoluto de un factor sino su percentil frente al resto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from screener.config import ScreenerConfig
from screener.data.provider import Barras, Fundamentales
from screener.factors import technical as tech

log = logging.getLogger("screener.scoring")


def percentiles(valores: dict[str, float], invertir: bool = False) -> dict[str, float]:
    """Convierte {ticker: valor_crudo} en {ticker: score 0-100} por rango
    percentil. `invertir=True` para factores donde menor es mejor (vol, P/E)."""
    items = [(t, v) for t, v in valores.items() if v is not None]
    if not items:
        return {}
    # 'higher is better' por defecto -> orden descendente (mayor valor, i=0,
    # recibe 100). invertir=True (menor es mejor) usa orden ascendente.
    ordenados = sorted(items, key=lambda kv: kv[1], reverse=not invertir)
    n = len(ordenados)
    if n == 1:
        return {ordenados[0][0]: 100.0}
    return {t: round(100.0 * (n - 1 - i) / (n - 1), 1)
            for i, (t, _) in enumerate(ordenados)}


@dataclass
class Puntuacion:
    ticker: str
    score_total: float
    sub: dict[str, float] = field(default_factory=dict)   # factor -> 0-100
    sector: str | None = None
    nombre: str | None = None      # metadata para el reporte, no entra al cálculo
    industria: str | None = None


def _factor_valor(f: Fundamentales) -> float | None:
    """Barato = bueno. Combina el inverso de P/E y P/B disponibles."""
    partes = []
    if f.pe and f.pe > 0:
        partes.append(1.0 / f.pe)
    if f.pb and f.pb > 0:
        partes.append(1.0 / f.pb)
    return sum(partes) / len(partes) if partes else None


def _factor_calidad(f: Fundamentales) -> float | None:
    """ROE + margen operativo (rentabilidad del negocio)."""
    partes = [x for x in (f.roe, f.margen_operativo) if x is not None]
    return sum(partes) / len(partes) if partes else None


def puntuar(
    barras: dict[str, Barras],
    fund: dict[str, Fundamentales],
    cfg: ScreenerConfig,
) -> list[Puntuacion]:
    """Núcleo: de barras+fundamentales al ranking puntuado."""
    cfg.validar()

    # 1) Factores crudos por acción
    crudo: dict[str, dict[str, float | None]] = {}
    for t, b in barras.items():
        crudo[t] = {
            "momentum":  tech.momentum_compuesto(b),
            "tendencia": tech.score_tendencia(b),
            "baja_vol":  tech.volatilidad_anual(b),
            "liquidez":  tech.dollar_volume(b),
            "prox_52s":  tech.proximidad_maximo_52s(b),
            "rsi":       tech.rsi(b),
            "valor":     _factor_valor(fund.get(t, Fundamentales(t))),
            "calidad":   _factor_calidad(fund.get(t, Fundamentales(t))),
        }

    # 2) Normalización cross-sectional a 0-100 (invertir donde menor=mejor)
    invertidos = {"baja_vol"}  # menor volatilidad puntúa más alto
    factores = list(cfg.pesos.keys())
    scores_factor: dict[str, dict[str, float]] = {}
    for fac in factores:
        col = {t: crudo[t].get(fac) for t in crudo}
        scores_factor[fac] = percentiles(col, invertir=(fac in invertidos))

    # 3) Score compuesto = suma ponderada de los sub-scores disponibles.
    #    Si a una acción le falta un factor, se re-normaliza sobre los pesos
    #    presentes (no se le castiga con 0 por falta de dato gratis).
    resultado: list[Puntuacion] = []
    for t in crudo:
        sub, peso_disp, acum = {}, 0.0, 0.0
        for fac, peso in cfg.pesos.items():
            s = scores_factor.get(fac, {}).get(t)
            if s is not None:
                sub[fac] = s
                acum += s * peso
                peso_disp += peso
        if peso_disp == 0:
            continue
        total = round(acum / peso_disp, 1)
        f = fund.get(t, Fundamentales(t))
        resultado.append(Puntuacion(
            ticker=t, score_total=total, sub=sub,
            sector=f.sector, nombre=f.nombre, industria=f.industria,
        ))

    resultado.sort(key=lambda p: p.score_total, reverse=True)
    log.info("puntuadas %d acciones", len(resultado))
    return resultado
