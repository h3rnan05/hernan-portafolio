"""Genera el memo de /options TICKER bajo demanda -- motor 100%
determinístico (Black-Scholes, Greeks, probabilidad, valor esperado,
liquidez) que construye y rankea las estrategias reales de
screener/options_strategies.py sobre la cadena de opciones real
(screener/options_ideas.obtener_cadena). El LLM SOLO traduce el ranking
YA calculado a lenguaje llano -- nunca elige, nunca puntúa, nunca decide
qué operar (Principio #3 del AIOS, ver ROADMAP.md). Mismos guardrails que
news_analyst/explicador.py: prompt + filtro de palabras prohibidas como
defensa adicional al propio prompt.

Coherencia con la tesis (modo simple, por defecto): antes de mostrar el
Top N se filtran las estrategias cuya dirección
(`options_strategies.direccion_estrategia`) contradice la tesis técnica
-- pedido explícito tras ver un caso real donde el Top 1 era "Bear Put
Spread" (bajista) con la tesis técnica en "Alcista" arriba del mismo
mensaje, lo cual rompía la confianza ("¿por qué me recomiendas una
estrategia bajista si la tesis es alcista?"). El ranking matemático
NUNCA cambia por esto -- ver `_coincide_con_tesis` -- solo cambia qué se
muestra por defecto. `--full` sigue mostrando las 9 estrategias sin
filtrar, en el orden real del ranking.

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
    direccion_estrategia,
    iv_referencia,
    puntuar,
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


def _coincide_con_tesis(tendencia_label: str, direccion: str) -> bool:
    """¿La dirección de esta estrategia contradice la tesis técnica? Sin
    tesis direccional clara (neutral/no_determinable) no hay con qué
    contradecir, así que cualquier dirección cuenta como coherente.
    Tampoco se oculta una estrategia de dirección "neutral" (ej. Iron
    Condor: apuesta a que el precio se quede quieto, no a una dirección)
    porque no contradice ninguna tesis direccional -- solo se ocultan las
    estrategias de dirección estrictamente OPUESTA a la tesis."""
    if tendencia_label in ("neutral", "no_determinable") or direccion == "neutral":
        return True
    return tendencia_label == direccion


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


def _formatear_simple(indice: int, score_100: int, e: EstrategiaOpciones) -> list[str]:
    ganancia = f"${e.ganancia_maxima:,.0f}" if e.ganancia_maxima is not None else "Ilimitada"
    return [
        f"{indice}. {e.nombre} -- Score: {score_100}/100",
        f"   Riesgo máx: ${e.riesgo_maximo:,.0f}  |  Ganancia máx: {ganancia}",
    ]


def _formatear_full(indice: int, score_100: int, e: EstrategiaOpciones) -> list[str]:
    ganancia = f"${e.ganancia_maxima:,.0f}" if e.ganancia_maxima is not None else "Ilimitada"
    prob = f"{e.probabilidad_exito:.0%}" if e.probabilidad_exito is not None else "No disponible"
    ev = f"${e.valor_esperado:,.2f}" if e.valor_esperado is not None else "No disponible"
    liq = f"{e.liquidez_score:.0f}/100" if e.liquidez_score is not None else "No disponible"
    delta = f"{e.delta_neto:+.2f}" if e.delta_neto is not None else "No disponible"
    theta = f"${e.theta_neto:+.2f}/día" if e.theta_neto is not None else "No disponible"
    breakevens = ", ".join(f"${b:.2f}" for b in e.breakevens) or "No disponible"
    lineas = [f"{indice}. {e.nombre} -- Score: {score_100}/100"]
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
    # (estrategia, score) emparejados ANTES de filtrar/recortar nada, para
    # que el score mostrado siempre corresponda a la estrategia correcta
    # aunque el modo simple solo muestre un subconjunto.
    pares = list(zip(estrategias, (round(s * 100) for s in puntuar(estrategias))))

    tesis = _tesis(tendencia_label)
    lineas = [f"📐 Opciones: {nombre} ({ticker})", "", f"Tesis técnica: {tesis}",
              f"Vencimiento usado: {cadena.vencimiento} ({cadena.dias_a_vencimiento} días)",
              f"IV: {iv_ref:.0%}" if iv_ref is not None else "IV: No disponible",
              "IV Rank: No disponible -- requiere histórico de IV que el screener no recolecta hoy.",
              ""]

    if modo == "full":
        lineas.append(f"Ranking completo ({len(pares)} estrategias, orden puramente cuantitativo):")
        lineas.append("")
        for i, (e, score) in enumerate(pares, 1):
            lineas += _formatear_full(i, score, e)
    else:
        # Coherencia con la tesis: se ocultan las estrategias de dirección
        # opuesta (ver _coincide_con_tesis) -- el ranking matemático de
        # `pares` NUNCA se reordena, solo se filtra qué se muestra por
        # defecto. Si ninguna coincide (tesis muy direccional y la cadena
        # solo da lo contrario), se cae al Top N normal en vez de mostrar
        # una lista vacía -- con una nota explícita de por qué.
        alineadas = [(e, s) for e, s in pares if _coincide_con_tesis(tendencia_label, direccion_estrategia(e.nombre))]
        if alineadas:
            top = alineadas[:TOP_N_SIMPLE]
            lineas.append(f"Estrategias que sí tienen sentido para una tesis {tesis.lower()} "
                          f"({len(top)} de {len(alineadas)}):")
        else:
            top = pares[:TOP_N_SIMPLE]
            lineas.append(f"Ninguna estrategia coincide con la tesis {tesis.lower()} -- "
                          f"mostrando las mejores por score de todas formas:")
        lineas.append("")
        for i, (e, score) in enumerate(top, 1):
            lineas += _formatear_simple(i, score, e)
            lineas.append("")
        if len(pares) > len(top):
            lineas.append(f"Ver las {len(pares)} estrategias completas (incluye las que no "
                          f"coinciden con la tesis): /options {ticker} --full")
            lineas.append("")

    explicacion = _filtrar_explicacion(
        llamar_claude(SYSTEM_PROMPT, _mensaje_usuario(ticker, tesis, [e for e, _ in (pares if modo == "full" else top)])))
    if explicacion:
        lineas.append("💬 Explicación:")
        lineas.append(explicacion)
    else:
        lineas.append("💬 Explicación: no disponible (requiere ANTHROPIC_API_KEY configurada, "
                       "o la respuesta no pasó el filtro de seguridad).")
    lineas.append("")
    lineas.append(DISCLAIMER)
    return "\n".join(lineas)
