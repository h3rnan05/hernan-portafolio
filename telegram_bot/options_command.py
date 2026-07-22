"""Genera el memo de /options TICKER bajo demanda -- motor 100%
determinístico (Black-Scholes, Greeks, probabilidad, valor esperado,
liquidez) que construye y rankea las estrategias reales de
screener/options_strategies.py sobre la cadena de opciones real
(screener/options_ideas.obtener_cadena). El LLM SOLO traduce el ranking
YA calculado a lenguaje llano -- nunca elige, nunca puntúa, nunca decide
qué operar (Principio #3 del AIOS, ver ROADMAP.md). Mismos guardrails que
news_analyst/explicador.py: prompt + filtro de palabras prohibidas como
defensa adicional al propio prompt.

IV Rank: sigue sin estar disponible (no hay histórico de IV recolectado
todavía) -- se muestra "No disponible" siempre, nunca un número
inventado. Ver el docstring de screener/options_ideas.py para el detalle
de por qué.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from news_analyst.explicador import llamar_claude  # noqa: E402
from screener.data.provider import Fundamentales, YahooProvider  # noqa: E402
from screener.factors import technical as tech  # noqa: E402
from screener.options_ideas import DISCLAIMER, clasificar_tendencia, obtener_cadena  # noqa: E402
from screener.options_strategies import (  # noqa: E402
    EstrategiaOpciones,
    construir_estrategias,
    iv_referencia,
    rankear,
)

log = logging.getLogger("telegram_bot.options_command")

TOP_N_SIMPLE = 4

# Defensa adicional al prompt: si el LLM se desvía y sugiere una acción de
# todas formas, la explicación se descarta entera antes de mostrarse
# (mismo patrón de belt-and-suspenders que news_analyst/explicador.py).
_PALABRAS_PROHIBIDAS = (
    "compra", "compren", "vende", "vendan", "recomiendo",
    "recomendación de comprar", "recomendación de vender",
    "hay que comprar", "hay que vender", "deberías abrir", "te recomiendo",
    "ejecuta esta", "abre esta posición", "esta es la mejor opción",
)

SYSTEM_PROMPT = """\
Eres el analista de opciones del AIOS. Tu ÚNICO trabajo es EXPLICAR en \
lenguaje llano un ranking de estrategias de opciones que YA fue calculado \
por un motor determinístico (Black-Scholes, Greeks, probabilidad, valor \
esperado, liquidez) -- NUNCA eliges cuál operar ni cambias el orden.

Reglas duras:
1. NUNCA sugieras abrir, comprar, vender o ejecutar una estrategia \
específica. No uses frases como "te recomiendo", "deberías abrir", \
"esta es la mejor opción". Tu trabajo es explicar el ranking que ya \
existe, no decidir -- el diseño de este sistema lo prohíbe.
2. Usa SOLO los números que se te dan (riesgo, ganancia, breakeven, \
probabilidad, valor esperado, liquidez). No inventes cifras ni datos \
externos que no estén ahí.
3. Para cada estrategia explica, en 2-3 frases: ventajas, desventajas y \
cuándo tendría sentido considerarla o qué la invalidaría (ej. un \
movimiento fuerte en contra, resultados antes del vencimiento, un \
cambio brusco en la volatilidad implícita).
4. Sé conciso.

Responde en español, texto plano (sin markdown, sin JSON) -- un párrafo \
corto por estrategia, en el mismo orden del ranking que se te dio."""


def _tesis(tendencia_label: str) -> str:
    return {"alcista": "Alcista", "bajista": "Bajista", "neutral": "Neutral"}.get(
        tendencia_label, "No determinable")


def _mensaje_usuario(ticker: str, tesis: str, estrategias: list[EstrategiaOpciones]) -> str:
    lineas = [f"Ticker: {ticker}. Tesis técnica: {tesis}.", ""]
    for i, e in enumerate(estrategias, 1):
        ganancia = f"${e.ganancia_maxima:,.0f}" if e.ganancia_maxima is not None else "ilimitada"
        prob = f"{e.probabilidad_exito:.0%}" if e.probabilidad_exito is not None else "no disponible"
        ev = f"${e.valor_esperado:,.2f}" if e.valor_esperado is not None else "no disponible"
        liq = f"{e.liquidez_score:.0f}/100" if e.liquidez_score is not None else "no disponible"
        breakevens = ", ".join(f"${b:.2f}" for b in e.breakevens) or "no disponible"
        lineas.append(
            f"{i}. {e.nombre} -- riesgo máx ${e.riesgo_maximo:,.0f}, ganancia máx {ganancia}, "
            f"breakeven(s) {breakevens}, probabilidad de éxito {prob}, valor esperado {ev}, "
            f"liquidez {liq}.")
    return "\n".join(lineas)


def _filtrar_explicacion(texto: str | None) -> str | None:
    if not texto:
        return None
    if any(p in texto.lower() for p in _PALABRAS_PROHIBIDAS):
        log.warning("explicación de opciones descartada: contenía lenguaje de recomendación")
        return None
    return texto.strip()


def _estrellas_por_posicion(indice0: int, total: int) -> str:
    """indice0: posición 0-based en el ranking. 1ra -> 5 estrellas, última
    -> 1 estrella, escala lineal entre medio. Es relativo a ESTE ranking
    (las ~9 estrategias de este ticker hoy), no una escala universal."""
    n = 5 if total <= 1 else round(5 - (indice0 / (total - 1)) * 4)
    n = max(1, min(5, n))
    return "⭐" * n + "☆" * (5 - n)


def _formatear_simple(indice: int, total: int, e: EstrategiaOpciones) -> list[str]:
    ganancia = f"${e.ganancia_maxima:,.0f}" if e.ganancia_maxima is not None else "Ilimitada"
    return [
        f"{indice}. {e.nombre} {_estrellas_por_posicion(indice - 1, total)}",
        f"   Riesgo máx: ${e.riesgo_maximo:,.0f}  |  Ganancia máx: {ganancia}",
    ]


def _formatear_full(indice: int, e: EstrategiaOpciones) -> list[str]:
    ganancia = f"${e.ganancia_maxima:,.0f}" if e.ganancia_maxima is not None else "Ilimitada"
    prob = f"{e.probabilidad_exito:.0%}" if e.probabilidad_exito is not None else "No disponible"
    ev = f"${e.valor_esperado:,.2f}" if e.valor_esperado is not None else "No disponible"
    liq = f"{e.liquidez_score:.0f}/100" if e.liquidez_score is not None else "No disponible"
    delta = f"{e.delta_neto:+.2f}" if e.delta_neto is not None else "No disponible"
    theta = f"${e.theta_neto:+.2f}/día" if e.theta_neto is not None else "No disponible"
    breakevens = ", ".join(f"${b:.2f}" for b in e.breakevens) or "No disponible"
    lineas = [f"{indice}. {e.nombre}"]
    for p in e.patas:
        lineas.append(f"     {p.accion} {p.tipo} ${p.strike:.2f} (prima ${p.prima:.2f})")
    if e.capital_adicional_requerido:
        lineas.append("     Requiere poseer 100 acciones o efectivo reservado adicional.")
    lineas += [
        f"   Riesgo máximo: ${e.riesgo_maximo:,.0f}",
        f"   Ganancia máxima: {ganancia}",
        f"   Breakeven(s): {breakevens}",
        f"   Probabilidad de éxito (bajo la IV implícita): {prob}",
        f"   Valor esperado: {ev}",
        f"   Delta neto: {delta}  |  Theta neto: {theta}",
        f"   Liquidez (aprox.): {liq}",
        f"   {e.razon}",
        "",
    ]
    return lineas


def generar_options(ticker: str, modo: str = "simple") -> str:
    """Punto de entrada de /options TICKER [--full]. Nunca lanza:
    cualquier fallo se convierte en un mensaje de error legible."""
    ticker = ticker.upper().strip()
    provider = YahooProvider()
    barras_por_ticker = provider.barras([ticker], dias=400)
    if ticker not in barras_por_ticker:
        return (f"No pude obtener datos de precio para {ticker} -- revisa que "
                f"el ticker esté bien escrito, o Yahoo bloqueó la consulta. "
                f"Intenta de nuevo en un momento.")
    barras = barras_por_ticker[ticker]
    spot = barras.close[-1]
    fund = provider.fundamentales([ticker]).get(ticker, Fundamentales(ticker))
    nombre = fund.nombre or ticker
    tendencia_label = clasificar_tendencia(tech.score_tendencia(barras))

    cadena = obtener_cadena(ticker)
    if cadena is None:
        return (f"No pude obtener la cadena de opciones de {ticker} -- puede que no "
                f"tenga opciones listadas, o Yahoo bloqueó la consulta. Intenta de "
                f"nuevo en un momento.")

    iv_ref = iv_referencia(cadena, spot)
    estrategias = rankear(construir_estrategias(cadena, spot))
    if not estrategias:
        return (f"No pude construir estrategias de opciones para {ticker} con los "
                f"datos disponibles hoy -- puede que la cadena tenga muy pocos "
                f"strikes cotizados.")

    tesis = _tesis(tendencia_label)
    lineas = [f"📐 Opciones: {nombre} ({ticker})", "", f"Tesis técnica: {tesis}",
              f"Vencimiento usado: {cadena.vencimiento} ({cadena.dias_a_vencimiento} días)",
              f"IV: {iv_ref:.0%}" if iv_ref is not None else "IV: No disponible",
              "IV Rank: No disponible -- requiere histórico de IV que el screener no recolecta hoy.",
              ""]

    if modo == "full":
        estrategias_para_llm = estrategias
        lineas.append(f"Ranking completo ({len(estrategias)} estrategias, orden puramente cuantitativo):")
        lineas.append("")
        for i, e in enumerate(estrategias, 1):
            lineas += _formatear_full(i, e)
    else:
        top = estrategias[:TOP_N_SIMPLE]
        estrategias_para_llm = top
        lineas.append(f"Top {len(top)} estrategias (de {len(estrategias)} calculadas):")
        lineas.append("")
        for i, e in enumerate(top, 1):
            lineas += _formatear_simple(i, len(top), e)
            lineas.append("")
        restantes = len(estrategias) - len(top)
        if restantes > 0:
            lineas.append(f"... y {restantes} más. Escribe /options {ticker} --full")
            lineas.append("")

    explicacion = _filtrar_explicacion(
        llamar_claude(SYSTEM_PROMPT, _mensaje_usuario(ticker, tesis, estrategias_para_llm)))
    if explicacion:
        lineas.append("💬 Explicación:")
        lineas.append(explicacion)
    else:
        lineas.append("💬 Explicación: no disponible (requiere ANTHROPIC_API_KEY configurada, "
                       "o la respuesta no pasó el filtro de seguridad).")
    lineas.append("")
    lineas.append(DISCLAIMER)
    return "\n".join(lineas)
