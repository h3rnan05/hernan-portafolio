"""Estadísticas determinísticas sobre las alertas ya resueltas -- Prompt
10: "Calcular automáticamente Win Rate, Average Return, Maximum
Drawdown, Expectancy, Sharpe. El sistema debe aprender cuáles patrones
funcionan mejor con el tiempo." Mismo principio que `journal/stats.py`:
fórmulas fijas sobre números ya medidos, cero LLM, cero juicio.

"El sistema debe aprender" se traduce aquí en las funciones
`calcular_por_*` -- las mismas métricas, agrupadas por patrón, hora del
día, tipo de catalizador, float, gap o RVOL, para poder responder "¿qué
patrón gana más? ¿qué horario funciona mejor?" en vez de un solo número
agregado.

Pivote 2026-07-26 (pedido explícito: "quiero que el sistema tenga
MEMORIA... no quiero optimizar eso todavía, solo quiero que la
arquitectura quede preparada"): esto es deliberadamente solo la mitad de
"medir", no la de "decidir" -- ninguna función de aquí ajusta pesos de
`scoring.py` ni umbrales de `config.py`. Cuando haya suficientes alertas
resueltas para que estas tablas signifiquen algo, un ajuste real de
`config.MomentumConfig` seguiría siendo una decisión humana explícita
(mismo principio del Validation Pipeline del `ROADMAP.md` raíz: ningún
número se cambia solo).

Nota honesta sobre Sharpe: aquí es simplemente
media(retornos) / desviación_estándar(retornos) sobre los retornos por
ALERTA (no por día, no anualizado, sin restar una tasa libre de riesgo)
-- una medida de consistencia relativa entre grupos, no el Sharpe Ratio
anualizado de un portafolio real. Sirve para comparar un grupo contra
otro, no para reportarlo como si fuera el Sharpe de un fondo."""

from __future__ import annotations

import statistics
from collections.abc import Callable
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


def _agrupar(
    alertas: list[AlertaRegistrada], horizonte_dias: int, clave_de: Callable[[AlertaRegistrada], object | None],
) -> dict[object, EstadisticasHorizonte]:
    """Núcleo común de todas las funciones `calcular_por_*` -- agrupa
    por lo que devuelva `clave_de` (None se excluye: no se puede
    aprender de un dato que no se pudo medir), y calcula las mismas
    `EstadisticasHorizonte` de siempre sobre cada grupo."""
    con_resultado = _con_resultado(alertas, f"{horizonte_dias}d")
    claves = sorted({clave_de(a) for a in con_resultado if clave_de(a) is not None}, key=str)
    return {
        clave: calcular_estadisticas([a for a in alertas if clave_de(a) == clave], horizonte_dias)
        for clave in claves
    }


def calcular_por_clasificacion(
    alertas: list[AlertaRegistrada], horizonte_dias: int,
) -> dict[str, EstadisticasHorizonte]:
    """Una tabla de estadísticas por patrón -- para responder "¿el Gap
    and Go realmente gana más que el Bull Flag?" en vez de una sola
    cifra que mezcla patrones distintos."""
    return _agrupar(alertas, horizonte_dias, lambda a: a.clasificacion)


def calcular_por_hora(alertas: list[AlertaRegistrada], horizonte_dias: int) -> dict[int, EstadisticasHorizonte]:
    """Por hora UTC en que se mandó la alerta -- "¿qué horario funciona
    mejor?". Agrupación por hora exacta (no por bandas) a propósito:
    con pocos datos reales, inventar bandas ("apertura"/"mediodía"/...)
    sería una decisión editorial prematura -- se puede re-agrupar
    después con más historia."""
    return _agrupar(alertas, horizonte_dias, lambda a: a.hora_utc)


def calcular_por_catalizador(
    alertas: list[AlertaRegistrada], horizonte_dias: int,
) -> dict[str, EstadisticasHorizonte]:
    """Por tipo de catalizador -- "¿qué tipo de noticia funciona mejor?"."""
    return _agrupar(alertas, horizonte_dias, lambda a: a.catalizador_tipo)


# Bandas de float/gap/RVOL -- a diferencia de la hora del día, aquí SÍ
# hace falta agrupar en rangos (float/gap/RVOL son continuos, agrupar
# por valor exacto no uniría casi nada). Los cortes son un punto de
# partida editorial, documentado y fácil de ajustar cuando haya
# suficientes alertas resueltas para que valga la pena revisarlos --
# nunca se ajustan solos.
_BANDAS_FLOAT: tuple[tuple[str, Callable[[float], bool]], ...] = (
    ("<5M", lambda f: f < 5_000_000),
    ("5-20M", lambda f: 5_000_000 <= f < 20_000_000),
    ("20-50M", lambda f: 20_000_000 <= f < 50_000_000),
    (">=50M", lambda f: f >= 50_000_000),
)
_BANDAS_GAP: tuple[tuple[str, Callable[[float], bool]], ...] = (
    ("<5%", lambda g: g < 0.05),
    ("5-10%", lambda g: 0.05 <= g < 0.10),
    ("10-20%", lambda g: 0.10 <= g < 0.20),
    (">=20%", lambda g: g >= 0.20),
)
_BANDAS_RVOL: tuple[tuple[str, Callable[[float], bool]], ...] = (
    ("<5x", lambda r: r < 5.0),
    ("5-10x", lambda r: 5.0 <= r < 10.0),
    (">=10x", lambda r: r >= 10.0),
)


def _banda(valor: float | None, bandas: tuple[tuple[str, Callable[[float], bool]], ...]) -> str | None:
    if valor is None:
        return None
    for etiqueta, predicado in bandas:
        if predicado(valor):
            return etiqueta
    return None


def calcular_por_float(alertas: list[AlertaRegistrada], horizonte_dias: int) -> dict[str, EstadisticasHorizonte]:
    """Por banda de float -- "¿qué float termina siendo el más rentable?"."""
    return _agrupar(alertas, horizonte_dias, lambda a: _banda(a.float_acciones, _BANDAS_FLOAT))


def calcular_por_gap(alertas: list[AlertaRegistrada], horizonte_dias: int) -> dict[str, EstadisticasHorizonte]:
    """Por banda de gap (valor absoluto) -- "¿qué gap funciona mejor?"."""
    return _agrupar(
        alertas, horizonte_dias,
        lambda a: _banda(abs(a.gap_pct) if a.gap_pct is not None else None, _BANDAS_GAP),
    )


def calcular_por_rvol(alertas: list[AlertaRegistrada], horizonte_dias: int) -> dict[str, EstadisticasHorizonte]:
    """Por banda de RVOL -- "¿qué nivel de RVOL termina siendo el más
    rentable?"."""
    return _agrupar(alertas, horizonte_dias, lambda a: _banda(a.rvol, _BANDAS_RVOL))
