"""Genera /trade TICKER -- la versión de bolsillo de /report + /options:
responde en menos de 30 segundos de lectura la conclusión, la mejor
estrategia de opciones según el motor determinístico, un ejemplo
educativo concreto, escenarios sobre el payoff real, y los riesgos
principales. /report y /options siguen existiendo tal cual para cuando
SÍ quieras profundizar -- /trade no los reemplaza.

Principio #3 (igual que /report y /options): NINGÚN número de este
comando es una predicción de resultado ni una recomendación de compra/
venta. "Convicción del modelo" es el score real del screener (0-100,
`screener.scoring.puntuar()`) o el score compuesto real del ranking de
estrategias (`options_strategies.puntuar()`) -- ninguno de los dos es una
probabilidad de éxito, son medidas de qué tan bien califica el activo o
la estrategia según las reglas fijas del motor, igual que ya se muestran
en el screener diario y en /options.

100% determinístico, sin LLM: a diferencia de la versión anterior de
este comando, el bloque "¿Por qué?" ya no depende de una llamada a
Claude (que podía fallar en silencio -- ver auditoría en vivo) sino que
arma frases cortas directamente de números ya reales (RSI, precio vs.
objetivo de analistas, score compuesto de la estrategia). Mismo criterio
que el resto del motor: reglas fijas, reproducibles, nunca una síntesis
abierta.

Honestidad de los datos: los escenarios evalúan el payoff REAL de la
estrategia (`options_strategies.evaluar_payoff`) en 3 precios concretos
(el objetivo promedio de analistas si existe, el precio actual sin
cambios, y un movimiento del 15% en la dirección que perjudica a la
estrategia) -- son escenarios sobre números reales, no una predicción de
que vayan a ocurrir. Si no hay estrategias de opciones disponibles hoy,
o el ticker no está en la shortlist de hoy, o no hay precio objetivo de
analistas, el mensaje lo dice explícitamente en vez de inventar un dato.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_TELEGRAM_BOT_DIR = Path(__file__).resolve().parent
if str(_TELEGRAM_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_TELEGRAM_BOT_DIR))

from report_command import _clasificar_valoracion, _riesgos, _shortlist_entry  # noqa: E402
from screener.data.provider import Fundamentales, YahooProvider  # noqa: E402
from screener.factors import technical as tech  # noqa: E402
from screener.options_ideas import clasificar_tendencia, obtener_cadena  # noqa: E402
from screener.options_strategies import (  # noqa: E402
    EstrategiaOpciones,
    construir_estrategias,
    direccion_estrategia,
    evaluar_payoff,
    puntuar,
    rankear,
)

log = logging.getLogger("telegram_bot.trade_command")

SEP = "━━━━━━━━━━━━━━━━━━"


def _emoji_convic(score: float) -> str:
    if score >= 70:
        return "🟢"
    if score >= 40:
        return "🟡"
    return "🔴"


def _fmt_strike(k: float) -> str:
    return f"{k:.0f}" if float(k).is_integer() else f"{k:.2f}"


def _fmt_price(x: float) -> str:
    return f"${x:,.2f}"


def _fmt_price_round(x: float) -> str:
    return f"${x:,.0f}"


def _fmt_signed_round(x: float) -> str:
    return f"+${x:,.0f}" if x >= 0 else f"-${abs(x):,.0f}"


def _emoji_payoff(payoff: float) -> str:
    """🟢/🔴 según el SIGNO real del payoff calculado -- nunca según una
    dirección asumida de antemano (ver docstring de _precio_favorable: el
    precio objetivo real de analistas puede no coincidir con la dirección
    que técnicamente favorece a la estrategia, y en ese caso el emoji debe
    reflejar el número real, no la suposición)."""
    return "🟢" if payoff >= 0 else "🔴"


def _punto_equilibrio(breakevens: list[float]) -> str:
    if not breakevens:
        return "No disponible"
    if len(breakevens) == 1:
        return _fmt_price(breakevens[0])
    lo, hi = min(breakevens), max(breakevens)
    return f"{_fmt_price(lo)} - {_fmt_price(hi)}"


def _precio_adverso(spot: float, delta_neto: float | None) -> float:
    """Movimiento de ±15% en la dirección que perjudica a la estrategia
    (según el signo de su delta neto) -- una convención fija y
    documentada para el escenario de estrés, no una predicción."""
    if delta_neto is not None and delta_neto < 0:
        return spot * 1.15
    return spot * 0.85


def _precio_favorable(spot: float, delta_neto: float | None) -> float:
    """Espejo de _precio_adverso: ±15% en la dirección que beneficia a
    la estrategia -- se usa solo si no hay precio objetivo real de
    analistas para el escenario favorable."""
    if delta_neto is not None and delta_neto < 0:
        return spot * 0.85
    return spot * 1.15


_TESIS_DISPLAY = {
    "alcista": "Alcista", "bajista": "Bajista", "esperar": "Esperar",
    "neutral": "Neutral", "no_determinable": "No determinable",
}


def _tesis_categoria(tendencia_label: str, valoracion_label: str, rsi: float | None) -> str:
    """Tesis del screener en una de 4 categorías ("alcista"/"bajista"/
    "esperar"/"neutral") -- "esperar" es un caso especial de tendencia
    alcista con una señal de sobrecompra o valuación exigente encima:
    todavía no es una tesis bajista, pero tampoco compraría hoy. Única
    fuente de verdad tanto para el texto de "Mi conclusión" como para la
    capa de coherencia contra la estrategia de opciones (ver
    _tesis_coincide_con_estrategia)."""
    sobrecomprado = rsi is not None and rsi >= 70
    if tendencia_label == "alcista" and (sobrecomprado or valoracion_label == "Exigente"):
        return "esperar"
    if tendencia_label in ("alcista", "bajista", "neutral"):
        return tendencia_label
    return "no_determinable"


def _tesis_coincide_con_estrategia(tesis_categoria: str, direccion: str) -> bool:
    """Compara la tesis del screener con la dirección de la estrategia top
    -- NUNCA cambia el ranking, solo decide cómo se PRESENTA. Sin tesis
    direccional clara (neutral/no_determinable) no hay con qué contradecir,
    así que cualquier dirección cuenta como coherente. "Esperar" es
    incompatible con una estrategia alcista (es justo el caso que motivó
    esta capa: "no compraría hoy" seguido de "Long Call" leía como
    contradictorio)."""
    if tesis_categoria in ("neutral", "no_determinable"):
        return True
    if tesis_categoria == "esperar":
        return direccion != "alcista"
    return tesis_categoria == direccion


def _conclusion_acciones(tesis_categoria: str) -> tuple[str | None, str]:
    """(preámbulo opcional, veredicto) sobre comprar la acción directamente
    -- puramente determinístico sobre la tesis ya categorizada."""
    if tesis_categoria == "esperar":
        return "Esperaría una pequeña corrección antes de entrar.", "No compraría acciones hoy."
    if tesis_categoria == "alcista":
        return None, "Compraría acciones hoy si busco exposición directa al alza."
    if tesis_categoria == "bajista":
        return None, "No compraría acciones hoy -- la tendencia técnica es bajista."
    if tesis_categoria == "neutral":
        return None, "No hay una señal técnica clara para comprar acciones hoy."
    return None, "No tengo suficiente información técnica para opinar sobre comprar acciones hoy."


def _conclusion_opciones(estrategias: list[EstrategiaOpciones], score_100: float | None) -> str:
    if not estrategias:
        return "No hay estrategias de opciones disponibles hoy (cadena insuficiente)."
    if score_100 is not None and score_100 < 40:
        return "Investigaría con cautela una estrategia con opciones (relación riesgo/beneficio modesta hoy)."
    return "Sí investigaría una estrategia con opciones."


def _por_que_bullets(rsi: float | None, fund: Fundamentales, spot: float, score_100: float | None) -> list[str]:
    """Frases cortas armadas de números ya reales -- ningún LLM interviene
    aquí (ver docstring del módulo)."""
    bullets = []
    if rsi is not None and rsi >= 70:
        bullets.append(f"RSI en sobrecompra ({rsi:.0f}).")
    elif rsi is not None and rsi <= 30:
        bullets.append(f"RSI en sobreventa ({rsi:.0f}).")
    if fund.analista_precio_objetivo:
        objetivo = fund.analista_precio_objetivo
        if spot > objetivo:
            bullets.append(f"El precio ya está por encima del objetivo promedio de analistas ({_fmt_price(objetivo)}).")
        elif spot < objetivo:
            bullets.append(f"El precio está por debajo del objetivo promedio de analistas ({_fmt_price(objetivo)}), "
                          f"con espacio para subir según el consenso.")
        else:
            bullets.append(f"El precio está prácticamente en el objetivo promedio de analistas ({_fmt_price(objetivo)}).")
    if score_100 is not None:
        if score_100 >= 70:
            bullets.append("Buena relación riesgo/beneficio comparada con las demás estrategias evaluadas hoy.")
        elif score_100 >= 40:
            bullets.append("Relación riesgo/beneficio razonable comparada con las demás estrategias evaluadas hoy.")
        else:
            bullets.append("Fue la estrategia mejor calificada hoy, aunque con una relación riesgo/beneficio modesta.")
    return bullets


def generar_trade(ticker: str) -> str:
    """Punto de entrada de /trade TICKER. Nunca lanza: cualquier fallo se
    convierte en un mensaje de error legible."""
    ticker = ticker.upper().strip()
    provider = YahooProvider()
    barras_por_ticker = provider.barras([ticker], dias=400)
    if ticker not in barras_por_ticker:
        return (f"No pude obtener datos de precio para {ticker} -- revisa que "
                f"el ticker esté bien escrito, o Yahoo bloqueó la consulta.")
    barras = barras_por_ticker[ticker]
    spot = barras.close[-1]
    fund = provider.fundamentales([ticker]).get(ticker, Fundamentales(ticker))
    nombre = fund.nombre or ticker
    entrada_shortlist = _shortlist_entry(ticker)
    score_screener = entrada_shortlist["score"] if entrada_shortlist else None

    rsi = tech.rsi(barras)
    vol = tech.volatilidad_anual(barras)
    tendencia_label = clasificar_tendencia(tech.score_tendencia(barras))
    valoracion_label = _clasificar_valoracion(
        fund.pe, (entrada_shortlist or {}).get("sub_scores", {}).get("valor"))
    riesgos = _riesgos(rsi, fund.pe, vol, tendencia_label)
    riesgos_reales = [r for r in riesgos if "Sin banderas" not in r]

    cadena = obtener_cadena(ticker)
    estrategias = rankear(construir_estrategias(cadena, spot)) if cadena else []
    top = estrategias[0] if estrategias else None
    score_100 = round(puntuar(estrategias)[0] * 100) if estrategias else None

    tesis_categoria = _tesis_categoria(tendencia_label, valoracion_label, rsi)

    lineas = [f"📊 {ticker} — {nombre}", ""]
    if score_screener is not None:
        lineas.append(f"{_emoji_convic(score_screener)} Convicción del modelo: {score_screener:.0f}/100")
    else:
        lineas.append("⚪ Convicción del modelo: No disponible (no está en la shortlist de hoy)")
    lineas += ["", SEP, "", "📌 Mi conclusión", ""]

    preambulo, veredicto_acciones = _conclusion_acciones(tesis_categoria)
    if preambulo:
        lineas.append(preambulo)
        lineas.append("")
    lineas.append(veredicto_acciones)
    lineas.append(_conclusion_opciones(estrategias, score_100))
    lineas += ["", SEP, ""]

    if top is None:
        lineas += ["🎯 Mejor estrategia", ""]
        lineas.append("No pude calcular estrategias de opciones para hoy (cadena insuficiente o no disponible).")
    else:
        direccion_top = direccion_estrategia(top.nombre)
        coincide = _tesis_coincide_con_estrategia(tesis_categoria, direccion_top)
        if coincide:
            lineas += ["🎯 Mejor estrategia", "", top.nombre, ""]
        else:
            lineas += [
                "🎯 Si decidieras operar hoy, la estrategia con mejor relación matemática es...", "",
                top.nombre, "",
                f"⚠️ Esta estrategia no coincide con la tesis principal del modelo "
                f"({_TESIS_DISPLAY[tesis_categoria]}). El ranking de opciones es puramente matemático "
                f"(valor esperado, probabilidad, liquidez) y no considera la dirección de la tesis técnica.",
                "",
            ]
        lineas += [
            f"Convicción: {score_100}/100", "",
            "¿Por qué?", "",
        ]
        bullets = _por_que_bullets(rsi, fund, spot, score_100)
        for b in bullets or [top.razon]:
            lineas.append(f"• {b}")
        lineas += ["", SEP, "", "💰 Ejemplo", ""]

        for p in top.patas:
            lineas.append(f"{p.accion.capitalize()} {p.tipo.capitalize()} {_fmt_strike(p.strike)}")
        if top.capital_adicional_requerido:
            lineas.append("(Requiere poseer 100 acciones o efectivo reservado adicional.)")
        ganancia_txt = _fmt_price_round(top.ganancia_maxima) if top.ganancia_maxima is not None else "Ilimitada"
        lineas += [
            "",
            "Costo máximo:",
            _fmt_price_round(top.riesgo_maximo) if top.riesgo_maximo is not None else "No disponible",
            "",
            "Ganancia máxima:",
            ganancia_txt,
            "",
            "Punto de equilibrio:",
            _punto_equilibrio(top.breakevens),
            "", SEP, "", "📈 Escenarios", "",
        ]

        delta_neto = top.delta_neto
        precio_fav = fund.analista_precio_objetivo or _precio_favorable(spot, delta_neto)
        payoff_fav = evaluar_payoff(top, precio_fav, spot=spot)
        direccion_fav = "baja" if precio_fav < spot else "sube"
        lineas.append(f"{_emoji_payoff(payoff_fav)} Si {direccion_fav} hacia {_fmt_price_round(precio_fav)}")
        lineas.append("Ganancia estimada:" if payoff_fav >= 0 else "Pérdida estimada:")
        lineas.append(_fmt_signed_round(payoff_fav))
        lineas.append("")

        payoff_flat = evaluar_payoff(top, spot, spot=spot)
        lineas.append("🟡 Si se queda igual")
        lineas.append("Ganancia estimada:" if payoff_flat >= 0 else "Pérdida estimada:")
        lineas.append(_fmt_signed_round(payoff_flat))
        lineas.append("")

        precio_adv = _precio_adverso(spot, delta_neto)
        payoff_adv = evaluar_payoff(top, precio_adv, spot=spot)
        emoji_adv = _emoji_payoff(payoff_adv)
        if len(top.breakevens) == 1:
            palabra = "sube arriba de" if precio_adv > spot else "cae por debajo de"
            etiqueta = f"{emoji_adv} Si {palabra} {_fmt_price_round(top.breakevens[0])}"
        elif len(top.breakevens) == 2:
            lo, hi = min(top.breakevens), max(top.breakevens)
            etiqueta = f"{emoji_adv} Si sale del rango {_fmt_price_round(lo)} - {_fmt_price_round(hi)}"
        else:
            etiqueta = f"{emoji_adv} Si se mueve fuerte en tu contra"
        lineas.append(etiqueta)
        lineas.append("Pérdida máxima:" if payoff_adv < 0 else "Ganancia estimada:")
        lineas.append(_fmt_signed_round(payoff_adv))
        lineas.append("")

    lineas += [SEP, "", "⚠️ Riesgos", ""]
    if riesgos_reales:
        for r in riesgos_reales:
            lineas.append(f"• {r}")
    else:
        lineas.append("Sin banderas de riesgo técnico relevantes detectadas hoy.")
    lineas += ["", SEP, "", "Próximo paso", "", f"/report {ticker}", f"/options {ticker} --full", ""]
    lineas.append("Esto no es una recomendación de compra/venta.")

    return "\n".join(lineas)
