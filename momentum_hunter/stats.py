"""Estadísticas determinísticas sobre las alertas ya resueltas -- Prompt
10: "Calcular automáticamente Win Rate, Average Return, Maximum
Drawdown, Expectancy, Sharpe. El sistema debe aprender cuáles patrones
funcionan mejor con el tiempo." Mismo principio que `journal/stats.py`:
fórmulas fijas sobre números ya medidos, cero LLM, cero juicio.

"El sistema debe aprender" se traduce aquí en `calcular_por_clasificacion`
-- las mismas métricas, agrupadas por tipo de oportunidad
(🔥 BREAKOUT / 🚀 SHORT SQUEEZE / ...), para poder responder "¿qué patrón
realmente funciona?" en vez de solo un número agregado.

Nota honesta sobre Sharpe: aquí es simplemente
media(retornos) / desviación_estándar(retornos) sobre los retornos por
ALERTA (no por día, no anualizado, sin restar una tasa libre de riesgo)
-- una medida de consistencia relativa entre patrones, no el Sharpe
Ratio anualizado de un portafolio real. Sirve para comparar un patrón
contra otro, no para reportarlo como si fuera el Sharpe de un fondo."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from momentum_hunter.tracker import AlertaRegistrada


@dataclass(frozen=True)
class EstadisticasHorizonte:
    horizonte_dias: int
    n: int
    win_rate: float | None
    retorno_promedio: float | None
    drawdown_maximo: float | None
    expectancy: float | None
    sharpe: float | None


def _con_resultado(alertas: list[AlertaRegistrada], clave: str) -> list[AlertaRegistrada]:
    return [a for a in alertas if a.resultados_pct.get(clave) is not None]


def calcular_estadisticas(alertas: list[AlertaRegistrada], horizonte_dias: int) -> EstadisticasHorizonte:
    clave = f"{horizonte_dias}d"
    con_resultado = _con_resultado(alertas, clave)
    n = len(con_resultado)
    if n == 0:
        return EstadisticasHorizonte(horizonte_dias, 0, None, None, None, None, None)

    retornos = [a.resultados_pct[clave] for a in con_resultado]
    ganadoras = [r for r in retornos if r > 0]
    perdedoras = [r for r in retornos if r <= 0]
    win_rate = len(ganadoras) / n
    retorno_promedio = sum(retornos) / n
    ganancia_prom = sum(ganadoras) / len(ganadoras) if ganadoras else 0.0
    perdida_prom = sum(perdedoras) / len(perdedoras) if perdedoras else 0.0
    expectancy = win_rate * ganancia_prom + (1 - win_rate) * perdida_prom

    ordenadas = sorted(con_resultado, key=lambda a: a.fecha)
    equity = pico = drawdown_maximo = 0.0
    for a in ordenadas:
        equity += a.resultados_pct[clave]
        pico = max(pico, equity)
        drawdown_maximo = max(drawdown_maximo, pico - equity)

    sharpe = None
    if n > 1:
        sd = statistics.pstdev(retornos)
        sharpe = retorno_promedio / sd if sd > 0 else None

    return EstadisticasHorizonte(
        horizonte_dias=horizonte_dias, n=n, win_rate=win_rate,
        retorno_promedio=retorno_promedio, drawdown_maximo=drawdown_maximo,
        expectancy=expectancy, sharpe=sharpe,
    )


def calcular_todos_los_horizontes(
    alertas: list[AlertaRegistrada], horizontes: tuple[int, ...],
) -> dict[int, EstadisticasHorizonte]:
    return {h: calcular_estadisticas(alertas, h) for h in horizontes}


def calcular_por_clasificacion(
    alertas: list[AlertaRegistrada], horizonte_dias: int,
) -> dict[str, EstadisticasHorizonte]:
    """Una tabla de estadísticas por tipo de oportunidad -- para
    responder "¿el short squeeze realmente gana más que el breakout?"
    en vez de una sola cifra que mezcla patrones distintos."""
    clasificaciones = sorted({a.clasificacion for a in _con_resultado(alertas, f"{horizonte_dias}d")})
    return {
        c: calcular_estadisticas([a for a in alertas if a.clasificacion == c], horizonte_dias)
        for c in clasificaciones
    }
