"""Motor matemático determinístico de opciones -- Black-Scholes, Greeks,
probabilidad de éxito y valor esperado. Ningún LLM interviene en este
módulo: son fórmulas fijas sobre números ya conocidos (spot, strike, días,
IV). Es la base sobre la que se construirá /options: un LLM podrá más
adelante traducir estos resultados a lenguaje llano, pero nunca calcularlos
ni elegir entre ellos (Principio #3 del proyecto).

Convenciones:
  - Todas las tasas e IV son anuales, en fracción (0.30 = 30%), no en %.
  - `dias` es días calendario hasta el vencimiento (no días hábiles) --
    coincide con lo que ya devuelve options_ideas.DatosOpciones.dias_a_vencimiento.
  - theta se devuelve por DÍA (no por año) y vega/rho por 1% de cambio en
    IV/tasa (no por 100%) -- son las unidades que un trader espera ver.
  - La tasa libre de riesgo no se obtiene en vivo (no hay una fuente
    gratuita confiable conectada); se usa una aproximación fija
    documentada como tal, nunca se presenta como un dato de mercado real.
  - El valor esperado se calcula integrando el payoff sobre la
    distribución lognormal implícita en la IV actual (medida
    "risk-neutral"), SIN descontar por valor del dinero en el tiempo --
    razonable para vencimientos cortos y consistente con cómo se explica
    en la documentación educativa de opciones. No es una predicción ni
    una garantía de ventaja estadística: es cuánto "espera" el propio
    mercado de opciones (vía su IV) que valga la posición, en promedio.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

TipoOpcion = Literal["call", "put"]

# Aproximación fija de la tasa libre de riesgo (T-bill ~3 meses). No se
# obtiene en vivo -- documentado explícitamente, nunca se presenta como
# dato de mercado real.
TASA_LIBRE_RIESGO_DEFAULT = 0.045

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # por día calendario
    vega: float   # por 1 punto porcentual de IV (ej. 30% -> 31%)
    rho: float    # por 1 punto porcentual de tasa libre de riesgo


def _d1_d2(spot: float, strike: float, anios: float, iv: float, tasa: float) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (tasa + 0.5 * iv * iv) * anios) / (iv * math.sqrt(anios))
    d2 = d1 - iv * math.sqrt(anios)
    return d1, d2


def _validar_entradas(spot: float, strike: float, dias: int, iv: float) -> float | None:
    """Devuelve None si las entradas son válidas, o un mensaje de error si
    no -- evita NaN/ZeroDivisionError silenciosos en vez de lanzar."""
    if spot <= 0 or strike <= 0:
        return "spot y strike deben ser positivos"
    if dias <= 0:
        return "no quedan días al vencimiento"
    if iv <= 0:
        return "IV debe ser positiva"
    return None


def precio_black_scholes(
    spot: float, strike: float, dias: int, iv: float,
    tipo: TipoOpcion, tasa: float = TASA_LIBRE_RIESGO_DEFAULT,
) -> float | None:
    """Precio teórico Black-Scholes de una call/put europea. None si las
    entradas no son válidas (nunca lanza ni devuelve NaN)."""
    if _validar_entradas(spot, strike, dias, iv):
        return None
    anios = dias / 365.0
    d1, d2 = _d1_d2(spot, strike, anios, iv, tasa)
    descuento = math.exp(-tasa * anios)
    if tipo == "call":
        return spot * _norm_cdf(d1) - strike * descuento * _norm_cdf(d2)
    return strike * descuento * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def greeks(
    spot: float, strike: float, dias: int, iv: float,
    tipo: TipoOpcion, tasa: float = TASA_LIBRE_RIESGO_DEFAULT,
) -> Greeks | None:
    """Greeks de una call/put europea, en las unidades que un trader
    espera ver (theta/día, vega y rho por 1 punto porcentual). None si las
    entradas no son válidas."""
    if _validar_entradas(spot, strike, dias, iv):
        return None
    anios = dias / 365.0
    d1, d2 = _d1_d2(spot, strike, anios, iv, tasa)
    descuento = math.exp(-tasa * anios)
    pdf_d1 = _norm_pdf(d1)

    gamma = pdf_d1 / (spot * iv * math.sqrt(anios))
    vega = spot * pdf_d1 * math.sqrt(anios) / 100.0  # por 1 punto porcentual de IV

    if tipo == "call":
        delta = _norm_cdf(d1)
        theta_anual = (-(spot * pdf_d1 * iv) / (2 * math.sqrt(anios))
                       - tasa * strike * descuento * _norm_cdf(d2))
        rho = strike * anios * descuento * _norm_cdf(d2) / 100.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_anual = (-(spot * pdf_d1 * iv) / (2 * math.sqrt(anios))
                       + tasa * strike * descuento * _norm_cdf(-d2))
        rho = -strike * anios * descuento * _norm_cdf(-d2) / 100.0

    return Greeks(delta=delta, gamma=gamma, theta=theta_anual / 365.0, vega=vega, rho=rho)


def probabilidad_sobre(
    spot: float, umbral: float, dias: int, iv: float,
    tasa: float = TASA_LIBRE_RIESGO_DEFAULT,
) -> float | None:
    """Probabilidad (bajo la medida risk-neutral implícita en la IV) de
    que el precio termine POR ENCIMA de `umbral` al vencimiento. Es la
    misma matemática que produce N(d2) en Black-Scholes -- se puede usar
    tanto con un strike (probabilidad ITM) como con un breakeven
    (probabilidad de que la posición sea rentable). None si las entradas
    no son válidas."""
    if _validar_entradas(spot, umbral, dias, iv):
        return None
    anios = dias / 365.0
    _, d2 = _d1_d2(spot, umbral, anios, iv, tasa)
    return _norm_cdf(d2)


def valor_esperado_payoff(
    spot: float, dias: int, iv: float, payoff: Callable[[float], float],
    tasa: float = TASA_LIBRE_RIESGO_DEFAULT, n_puntos: int = 4001,
) -> float | None:
    """Valor esperado de una posición cuyo resultado al vencimiento es
    `payoff(precio_final)`, integrando sobre la distribución lognormal
    risk-neutral implícita en la IV actual (regla del trapecio sobre una
    grilla de +/-6 desviaciones estándar en log-precio). Sirve para
    CUALQUIER estrategia -- un solo leg o varias patas -- sin necesitar una
    fórmula cerrada distinta para cada una: basta con describir su payoff.

    No descuenta por valor del dinero en el tiempo (razonable para
    vencimientos cortos) y usa la distribución IMPLÍCITA del mercado, no
    una predicción propia -- no es una garantía de ventaja estadística,
    es "cuánto espera el propio mercado de opciones, en promedio, según
    su IV actual". None si las entradas no son válidas."""
    if _validar_entradas(spot, spot, dias, iv):
        return None
    anios = dias / 365.0
    mu = math.log(spot) + (tasa - 0.5 * iv * iv) * anios
    sigma = iv * math.sqrt(anios)

    log_min, log_max = mu - 6 * sigma, mu + 6 * sigma
    paso = (log_max - log_min) / (n_puntos - 1)

    # Cambio de variable: en vez de integrar payoff(S)*f_S(S) dS (donde
    # f_S es la densidad lognormal en el espacio de precios), se integra
    # payoff(e^x)*f_X(x) dx en el espacio de log-precio, con f_X la
    # densidad NORMAL de x=ln(S) -- evita el ds=S*dx que se perdía al
    # mezclar una densidad en espacio de precio con un paso en log-precio.
    total = 0.0
    anterior = None
    for i in range(n_puntos):
        log_precio = log_min + i * paso
        precio = math.exp(log_precio)
        densidad_normal = _norm_pdf((log_precio - mu) / sigma) / sigma
        valor = payoff(precio) * densidad_normal
        if anterior is not None:
            total += (valor + anterior) / 2.0 * paso
        anterior = valor
    return total
