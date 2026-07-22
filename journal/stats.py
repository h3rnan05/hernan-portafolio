"""Estadísticas determinísticas sobre el journal -- win rate, expectancy,
drawdown y rendimiento por estrategia. Solo sobre operaciones CERRADAS
(las abiertas todavía no tienen resultado). Ningún LLM interviene: son
fórmulas fijas sobre números ya registrados."""

from __future__ import annotations

from dataclasses import dataclass

from journal.models import EntradaJournal


@dataclass(frozen=True)
class EstadisticasJournal:
    n_operaciones: int
    n_ganadoras: int
    n_perdedoras: int
    win_rate: float | None
    ganancia_promedio: float | None   # promedio de las operaciones ganadoras
    perdida_promedio: float | None    # promedio de las operaciones perdedoras (negativo)
    expectancy: float | None          # ganancia esperada por operación
    pnl_total: float
    drawdown_maximo: float            # peor caída del equity acumulado, en dólares


def _cerradas(entradas: list[EntradaJournal]) -> list[EntradaJournal]:
    return [e for e in entradas if e.estado == "cerrada" and e.resultado is not None]


def calcular_estadisticas(entradas: list[EntradaJournal]) -> EstadisticasJournal:
    """Todas las métricas se derivan de resultado (P&L real reportado al
    cerrar) -- nunca se estima o se infiere un resultado que no se
    reportó."""
    cerradas = _cerradas(entradas)
    n = len(cerradas)
    if n == 0:
        return EstadisticasJournal(
            n_operaciones=0, n_ganadoras=0, n_perdedoras=0, win_rate=None,
            ganancia_promedio=None, perdida_promedio=None, expectancy=None,
            pnl_total=0.0, drawdown_maximo=0.0,
        )

    ganadoras = [e.resultado for e in cerradas if e.resultado > 0]
    perdedoras = [e.resultado for e in cerradas if e.resultado <= 0]
    win_rate = len(ganadoras) / n
    ganancia_promedio = sum(ganadoras) / len(ganadoras) if ganadoras else None
    perdida_promedio = sum(perdedoras) / len(perdedoras) if perdedoras else None
    # Expectancy = ganancia esperada por operación, ponderada por el win
    # rate REAL observado (no por la probabilidad teórica que el motor
    # de opciones estimó al abrir -- eso es lo que el paper trading sirve
    # para validar).
    expectancy = win_rate * (ganancia_promedio or 0.0) + (1 - win_rate) * (perdida_promedio or 0.0)
    pnl_total = sum(e.resultado for e in cerradas)

    # Drawdown: equity acumulado en el orden real en que se CERRARON las
    # operaciones (fecha_cierre) -- la peor caída desde el pico anterior.
    ordenadas = sorted(cerradas, key=lambda e: e.fecha_cierre or "")
    equity = pico = drawdown_maximo = 0.0
    for e in ordenadas:
        equity += e.resultado
        pico = max(pico, equity)
        drawdown_maximo = max(drawdown_maximo, pico - equity)

    return EstadisticasJournal(
        n_operaciones=n, n_ganadoras=len(ganadoras), n_perdedoras=len(perdedoras),
        win_rate=win_rate, ganancia_promedio=ganancia_promedio, perdida_promedio=perdida_promedio,
        expectancy=expectancy, pnl_total=pnl_total, drawdown_maximo=drawdown_maximo,
    )


def calcular_estadisticas_por_estrategia(entradas: list[EntradaJournal]) -> dict[str, EstadisticasJournal]:
    """Una tabla de EstadisticasJournal, una por cada nombre de
    estrategia que tenga al menos una operación cerrada -- para responder
    'esta estrategia realmente funciona, o es esta otra'."""
    nombres = sorted({e.estrategia for e in _cerradas(entradas)})
    return {nombre: calcular_estadisticas([e for e in entradas if e.estrategia == nombre])
            for nombre in nombres}
