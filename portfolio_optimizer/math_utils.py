"""Matematica pura del riesgo de portafolio -- sin red, sin estado, sin
aleatoriedad. Cualquier OptimizationStrategy (v1 greedy, o a futuro
Mean-Variance, Risk Parity, HRP...) comparte estas funciones en vez de
reimplementar el calculo de riesgo cada una.
"""

from __future__ import annotations


def correlacion(matriz: dict[tuple[str, str], float], a: str, b: str) -> float:
    """Busca la correlacion de (a,b) en la matriz dispersa, sin importar el
    orden del par. Si no esta informada, asume 0 (sin correlacion conocida)
    -- nunca inventa un valor optimista."""
    if a == b:
        return 1.0
    return matriz.get((a, b), matriz.get((b, a), 0.0))


def varianza_portafolio(
    pesos: dict[str, float],
    volatilidades: dict[str, float],
    correlaciones: dict[tuple[str, str], float],
) -> float:
    """Varianza del portafolio = suma sobre i,j de w_i * w_j * vol_i * vol_j
    * correlacion(i,j), con correlacion(i,i)=1. Usa la matriz de correlacion
    completa, no un promedio ponderado ingenuo de volatilidades."""
    tickers = [t for t, w in pesos.items() if w > 0]
    var = 0.0
    for i in tickers:
        for j in tickers:
            rho = correlacion(correlaciones, i, j)
            var += pesos[i] * pesos[j] * volatilidades[i] * volatilidades[j] * rho
    return max(var, 0.0)


def volatilidad_portafolio(
    pesos: dict[str, float],
    volatilidades: dict[str, float],
    correlaciones: dict[tuple[str, str], float],
) -> float:
    return varianza_portafolio(pesos, volatilidades, correlaciones) ** 0.5


def beta_portafolio(pesos: dict[str, float], betas: dict[str, float]) -> float:
    """Beta del portafolio = suma ponderada de los betas individuales --
    el estandar de la industria (aditivo bajo CAPM)."""
    return sum(w * betas.get(t, 0.0) for t, w in pesos.items() if w > 0)


def hhi(pesos: dict[str, float]) -> float:
    """Herfindahl-Hirschman Index normalizado sobre el capital invertido
    (excluye cash). 1.0 = todo en una posicion, cercano a 0 = repartido."""
    invertido = {t: w for t, w in pesos.items() if w > 0}
    total = sum(invertido.values())
    if total <= 0:
        return 0.0
    return sum((w / total) ** 2 for w in invertido.values())


def score_diversificacion(
    pesos: dict[str, float], correlaciones: dict[tuple[str, str], float],
) -> float:
    """(1 - concentracion) * (1 - correlacion promedio entre posiciones).
    0 = nada diversificado (una sola posicion, o todo correlacionado 1.0),
    cercano a 1 = muchas posiciones parejas y no correlacionadas entre si."""
    tickers = [t for t, w in pesos.items() if w > 0]
    if not tickers:
        return 0.0
    concentracion = hhi(pesos)
    if len(tickers) < 2:
        return 0.0  # con una sola posicion, (1 - concentracion) ya da 0
    pares = [
        correlacion(correlaciones, a, b)
        for i, a in enumerate(tickers)
        for b in tickers[i + 1 :]
    ]
    promedio_corr = sum(pares) / len(pares)
    return round(max(0.0, (1 - concentracion) * (1 - promedio_corr)), 4)
