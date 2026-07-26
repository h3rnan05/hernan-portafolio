"""Prompt 9: el bot no se limita a decir "comprar" -- decide el vehículo
de entrada y justifica por qué ese y no otro. Determinístico, sin LLM
(mismo principio que el resto del pipeline).

Deliberadamente NO reutiliza `screener/options_math.py` /
`screener/options_strategies.py` (el motor de Black-Scholes/Greeks del
Investment Analyst): la mayoría de penny stocks / low float / small caps
de este universo NI SIQUIERA tienen una cadena de opciones listada, y
cuando la tienen, suele ser demasiado ilíquida para que un ranking por
Greeks tenga sentido -- ese motor está calibrado para el S&P 500. Aquí
la decisión es más simple y apropiada al universo: ¿hay opciones
razonablemente líquidas, sí o no?, y si las hay, ¿qué tipo de tesis es
(explosiva/direccional, de tendencia gradual, o de reversión paciente)?

Reglas fijas (Prompt 9):
- Sin opciones disponibles -> Comprar acciones (única opción práctica).
- Short squeeze / breakout / news momentum / earnings play (tesis
  explosiva, urgencia real, horizonte de días): Long Call -- el
  apalancamiento con riesgo limitado a la prima es lo que mejor
  aprovecha un movimiento rápido.
- Trend continuation (tendencia ya establecida, movimiento más gradual):
  Bull Call Spread -- reduce el costo cuando no se espera una explosión,
  a cambio de topar la ganancia máxima.
- Reversal (tesis paciente: "quiero comprar en la zona de reversión, y
  si no sube no me importa terminar comprando la acción"): Cash Secured
  Put -- cobra prima mientras se espera confirmación.
- Score o catalizador insuficiente: No Operar."""

from __future__ import annotations

from momentum_hunter.config import MomentumConfig


def tiene_opciones(ticker: str) -> bool:
    """Best-effort vía yfinance -- lista de vencimientos no vacía. Si
    yfinance falla o no está instalado, se asume que NO hay opciones
    (degradar hacia "Comprar acciones" es la opción segura: nunca
    recomienda una estrategia de opciones sin haber podido confirmar que
    existe una cadena real)."""
    try:
        import yfinance as yf
        return bool(yf.Ticker(ticker).options)
    except Exception:
        return False


def _justificar_long_call(ticker: str) -> list[str]:
    return [
        f"Necesita mucho menos capital que comprar {ticker} directamente, con la pérdida "
        "máxima limitada a la prima pagada.",
        "El apalancamiento de una call aprovecha mejor un catalizador de corto plazo "
        "(1-10 días): si el movimiento llega rápido, el rendimiento porcentual supera "
        "ampliamente el de comprar acciones.",
        "Frente a un Bull Call Spread: aquí no se limita la ganancia máxima, coherente "
        "con una tesis de movimiento explosivo, no uno moderado.",
    ]


def _justificar_bull_call_spread(ticker: str) -> list[str]:
    return [
        "Reduce el costo frente a una Long Call pura: vender un call más arriba financia "
        "parte de la prima.",
        "Coherente con una tendencia ya establecida (no un catalizador explosivo puntual): "
        "no se necesita una subida enorme para capturar la mayor parte de la ganancia del spread.",
        f"Frente a comprar {ticker} directamente: el riesgo máximo es lo que se paga por "
        "el spread, mucho menor que el capital de comprar acciones.",
    ]


def _justificar_csp(ticker: str) -> list[str]:
    return [
        "Cobra una prima mientras se espera la confirmación de la reversión, en vez de "
        "pagarla como en una call.",
        f"Si {ticker} sigue cayendo y se ejerce, el precio de compra efectivo "
        "(strike − prima) es mejor que comprar la acción hoy mismo.",
        "Coherente con una tesis paciente (reversión), no con un breakout donde cada día "
        "de espera reduce el margen del movimiento.",
    ]


def _justificar_acciones(ticker: str) -> list[str]:
    return [
        f"{ticker} no tiene una cadena de opciones lo suficientemente líquida -- común en "
        "penny stocks y low float.",
        "Comprar acciones directamente es la forma práctica de tomar la posición sin pagar "
        "un spread bid/ask excesivo en las opciones.",
    ]


def _justificar_no_operar() -> list[str]:
    return ["El score o el catalizador no son suficientes para justificar abrir una posición hoy."]


def decidir_estrategia(
    ticker: str, tipo_oportunidad: str, score_total: float, catalizador_confirmado: bool,
    opciones_disponibles: bool, cfg: MomentumConfig,
) -> tuple[str, list[str]]:
    """Único punto de entrada -- devuelve (nombre_estrategia, justificación).
    `tipo_oportunidad` es la clave interna de `classification.tipo_oportunidad`
    (no la etiqueta con emoji)."""
    if score_total < cfg.score_minimo_alerta or not catalizador_confirmado:
        return "No Operar", _justificar_no_operar()
    if not opciones_disponibles:
        return "Comprar acciones", _justificar_acciones(ticker)
    if tipo_oportunidad == "reversal":
        return "Cash Secured Put", _justificar_csp(ticker)
    if tipo_oportunidad == "trend_continuation":
        return "Bull Call Spread", _justificar_bull_call_spread(ticker)
    return "Long Call", _justificar_long_call(ticker)
