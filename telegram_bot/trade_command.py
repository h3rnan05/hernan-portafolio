"""Genera /trade TICKER -- la versión de bolsillo de /report + /options:
responde en menos de 30 segundos de lectura la conclusión, la tesis del
modelo frente a la mejor estrategia de opciones, en qué condiciones esa
estrategia gana (no "escenarios" con números que a veces contradicen a
la conclusión), un ejemplo educativo concreto, y un plan de acción
cuando la conclusión es esperar. /report y /options siguen existiendo
tal cual para cuando SÍ quieras profundizar -- /trade no los reemplaza.

Principio #3 (igual que /report y /options): NINGÚN número de este
comando es una predicción de resultado ni una recomendación de compra/
venta. "Score cuantitativo" (antes "Convicción") es el score real del
screener (0-100, `screener.scoring.puntuar()`) o el score compuesto real
del ranking de estrategias (`options_strategies.puntuar()`) -- ninguno
de los dos es una probabilidad de éxito, son medidas de qué tan bien
califica el activo o la estrategia según las reglas fijas del motor,
igual que ya se muestran en el screener diario y en /options. Se
renombró de "Convicción" a "Score cuantitativo" porque ese nombre podía
leerse como "% de probabilidad de ganar", que no es lo que mide.

100% determinístico, sin LLM: ninguna sección depende de una llamada a
Claude (que podía fallar en silencio -- ver auditoría en vivo). El
bloque "¿Por qué?" combina una explicación FIJA por tipo de estrategia
(qué gana, qué arriesga, cuál es el problema estructural -- son hechos
sobre CÓMO funciona cada una de las 9 estrategias, no una síntesis
abierta) con hechos reales del día (RSI, precio vs. objetivo de
analistas).

Coherencia con la tesis: antes de presentar la estrategia top se compara
la tesis del screener (Alcista/Bajista/Esperar/Neutral) con la dirección
real de esa estrategia (`options_strategies.direccion_estrategia`). El
ranking matemático NUNCA cambia por esto -- ver `_tesis_coincide_con_
estrategia`. Solo cambia cómo se presenta, para no mostrar mensajes
contradictorios como "No compraría acciones hoy" seguido de un Long Call
sin más contexto.

"¿Qué tiene que pasar para ganar?" reemplaza a una sección anterior de
"escenarios" que a veces mostraba pérdida en los 3 precios evaluados (un
Long Call, por ejemplo, pierde tanto si el precio baja como si se queda
igual -- solo gana si sube lo suficiente) y eso leía como contradictorio
("¿entonces para qué me la muestras?"). La condición de ganancia usa el
breakeven y el vencimiento REALES; "lo que puede salir mal" es una lista
fija por tipo de estrategia (verdades estructurales, no una predicción
sobre este ticker).

Plan de acción: solo aparece cuando la conclusión es "esperar" -- usa la
media móvil de 50 días (nivel técnico real, no un porcentaje arbitrario)
como referencia de "precio ideal para volver a revisar", el breakeven de
la estrategia top como nivel de ruptura para reconsiderar, y la fecha
real de resultados si existe. Nunca inventa un nivel de soporte que no
se pueda calcular con los datos disponibles.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
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
from screener.options_ideas import clasificar_tendencia, obtener_cadena, proxima_fecha_resultados  # noqa: E402
from screener.options_strategies import (  # noqa: E402
    EstrategiaOpciones,
    construir_estrategias,
    direccion_estrategia,
    puntuar,
    rankear,
)

log = logging.getLogger("telegram_bot.trade_command")

SEP = "━━━━━━━━━━━━━━━━━━"

_MESES_ES = {1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
             7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"}


def _emoji_score(score: float) -> str:
    if score >= 70:
        return "🟢"
    if score >= 40:
        return "🟡"
    return "🔴"


def _fmt_strike(k: float) -> str:
    return f"{k:.0f}" if float(k).is_integer() else f"{k:.2f}"


def _fmt_price_round(x: float) -> str:
    return f"${x:,.0f}"


def _fmt_fecha_es(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return f"{d.day} de {_MESES_ES[d.month]}"
    except (ValueError, KeyError):
        return iso


def _obtener_fecha_resultados(ticker: str) -> str | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        return proxima_fecha_resultados(yf.Ticker(ticker))
    except Exception as e:
        log.debug("fecha de resultados de %s falló: %s", ticker, e)
        return None


_TESIS_DISPLAY = {
    "alcista": "Alcista", "bajista": "Bajista", "esperar": "Esperar",
    "neutral": "Neutral", "no_determinable": "No determinable",
}
_TESIS_EMOJI = {
    "alcista": "🟢", "bajista": "🔴", "esperar": "🟡",
    "neutral": "⚪", "no_determinable": "⚪",
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


# Explicación FIJA por tipo de estrategia -- qué gana, qué arriesga y cuál
# es el problema estructural de cada una de las 9 estrategias del motor.
# Son hechos sobre CÓMO funciona el tipo de estrategia (siempre iguales
# para esa estrategia, en cualquier ticker), no una predicción sobre este
# ticker en particular -- eso lo aportan los hechos reales de abajo
# (RSI, precio vs. objetivo de analistas).
_EXPLICACION_ESTRATEGIA = {
    "Long Call": (
        "Porque es la estrategia que más gana si {ticker} sigue subiendo con fuerza. Capital "
        "requerido bajo (solo la prima) y la pérdida máxima es conocida desde el inicio. El "
        "problema: necesitas un movimiento relativamente fuerte antes del vencimiento -- si el "
        "precio se queda igual o sube poco, pierdes la prima."
    ),
    "Long Put": (
        "Porque es la estrategia que más gana si {ticker} cae con fuerza. Capital requerido bajo "
        "(solo la prima) y la pérdida máxima es conocida desde el inicio. El problema: necesitas "
        "una caída relativamente fuerte antes del vencimiento -- si el precio se queda igual o "
        "baja poco, pierdes la prima."
    ),
    "Bull Call Spread": (
        "Apuesta a que {ticker} suba, vendiendo una call más arriba para reducir el costo frente "
        "a un Long Call puro. El problema: la ganancia también tiene un techo -- si el precio "
        "sube mucho más allá de ese techo, no ganas más."
    ),
    "Bear Put Spread": (
        "Apuesta a que {ticker} baje, vendiendo una put más abajo para reducir el costo frente a "
        "un Long Put puro. El problema: la ganancia también tiene un techo -- si el precio cae "
        "mucho más allá de ese nivel, no ganas más."
    ),
    "Bull Put Spread": (
        "Cobra una prima apostando a que {ticker} se mantenga arriba de un nivel -- gana incluso "
        "si el precio no se mueve o sube. El problema: si el precio cae con fuerza, la pérdida "
        "puede superar varias veces la prima cobrada."
    ),
    "Bear Call Spread": (
        "Cobra una prima apostando a que {ticker} se mantenga abajo de un nivel -- gana incluso "
        "si el precio no se mueve o baja. El problema: si el precio sube con fuerza, la pérdida "
        "puede superar varias veces la prima cobrada."
    ),
    "Covered Call": (
        "Ya posees (o comprarías) 100 acciones de {ticker} y cobras una prima por comprometerte a "
        "venderlas si suben mucho. Genera ingreso extra. El problema: limita cuánto puedes ganar "
        "si el precio sube con fuerza, y sigues expuesto a que caiga."
    ),
    "Cash Secured Put": (
        "Cobras una prima por comprometerte a comprar 100 acciones de {ticker} si el precio cae a "
        "un nivel -- ganas si se mantiene o sube. El problema: si sigue cayendo, terminas "
        "comprando por encima del precio de mercado."
    ),
    "Iron Condor": (
        "Cobra una prima apostando a que {ticker} se quede dentro de un rango hasta el "
        "vencimiento -- gana con tranquilidad si no hay movimiento fuerte. El problema: si el "
        "precio se sale del rango en cualquier dirección, la estrategia pierde."
    ),
}

# Lista fija de "qué puede salir mal" por tipo de estrategia -- mismo
# criterio que _EXPLICACION_ESTRATEGIA (verdades estructurales del tipo
# de estrategia, no una predicción sobre este ticker).
_RIESGOS_ESTRATEGIA = {
    "Long Call": ["Que {ticker} no suba.", "Que suba demasiado lento.",
                  "Que baje la volatilidad implícita.", "Que llegue el vencimiento antes de tiempo."],
    "Long Put": ["Que {ticker} no baje.", "Que baje demasiado lento.",
                 "Que baje la volatilidad implícita.", "Que llegue el vencimiento antes de tiempo."],
    "Bull Call Spread": ["Que {ticker} no suba lo suficiente.",
                          "Que llegue el vencimiento antes de cruzar el punto de equilibrio."],
    "Bear Put Spread": ["Que {ticker} no baje lo suficiente.",
                         "Que llegue el vencimiento antes de cruzar el punto de equilibrio."],
    "Bull Put Spread": ["Que {ticker} caiga con fuerza antes del vencimiento.",
                         "Que la caída sea más rápida de lo esperado."],
    "Bear Call Spread": ["Que {ticker} suba con fuerza antes del vencimiento.",
                          "Que la subida sea más rápida de lo esperado."],
    "Covered Call": ["Que {ticker} caiga (la prima solo amortigua parte de la pérdida).",
                      "Que suba mucho más allá del strike vendido (ganancia limitada)."],
    "Cash Secured Put": ["Que {ticker} caiga con fuerza y termines comprando por encima del precio de mercado."],
    "Iron Condor": ["Que {ticker} se mueva con fuerza en cualquier dirección antes del vencimiento."],
}

_ESTRATEGIAS_REQUIEREN_MOVIMIENTO = frozenset({"Long Call", "Long Put", "Bull Call Spread", "Bear Put Spread"})


def _explicacion_estrategia(nombre: str, ticker: str) -> str:
    plantilla = _EXPLICACION_ESTRATEGIA.get(nombre)
    return plantilla.format(ticker=ticker) if plantilla else ""


def _riesgos_estrategia(nombre: str, ticker: str) -> list[str]:
    return [p.format(ticker=ticker) for p in _RIESGOS_ESTRATEGIA.get(nombre, [])]


def _condicion_para_ganar(top: EstrategiaOpciones, ticker: str, vencimiento_txt: str) -> str:
    """Condición de ganancia en lenguaje llano, sobre el breakeven y el
    vencimiento REALES de la estrategia -- nunca un nivel inventado. Las
    estrategias que cobran una prima (crédito) ganan si el precio se
    MANTIENE del lado bueno del breakeven; las que la pagan (débito)
    necesitan que el precio SE MUEVA hasta cruzarlo."""
    if len(top.breakevens) == 2:
        lo, hi = min(top.breakevens), max(top.breakevens)
        return (f"Que {ticker} se mantenga entre {_fmt_price_round(lo)} y {_fmt_price_round(hi)} "
                f"hasta el {vencimiento_txt}.")
    if not top.breakevens:
        return "No pude determinar una condición clara de ganancia con los datos de hoy."
    b = top.breakevens[0]
    direccion = direccion_estrategia(top.nombre)
    if top.nombre in _ESTRATEGIAS_REQUIEREN_MOVIMIENTO:
        palabra = "suba arriba de" if direccion != "bajista" else "baje por debajo de"
        return f"Que {ticker} {palabra} {_fmt_price_round(b)} antes del {vencimiento_txt}."
    palabra = "se mantenga arriba de" if direccion != "bajista" else "se mantenga abajo de"
    return f"Que {ticker} {palabra} {_fmt_price_round(b)} hasta el {vencimiento_txt}."


def _por_que_bullets(rsi: float | None, fund: Fundamentales, spot: float) -> list[str]:
    """Hechos reales del día (no genéricos de la estrategia) -- ningún
    LLM interviene aquí (ver docstring del módulo)."""
    bullets = []
    if rsi is not None and rsi >= 70:
        bullets.append(f"RSI en sobrecompra ({rsi:.0f}).")
    elif rsi is not None and rsi <= 30:
        bullets.append(f"RSI en sobreventa ({rsi:.0f}).")
    if fund.analista_precio_objetivo:
        objetivo = fund.analista_precio_objetivo
        if spot > objetivo:
            bullets.append(f"El precio ya está por encima del objetivo promedio de analistas "
                          f"({_fmt_price_round(objetivo)}).")
        elif spot < objetivo:
            bullets.append(f"El precio está por debajo del objetivo promedio de analistas "
                          f"({_fmt_price_round(objetivo)}), con espacio para subir según el consenso.")
        else:
            bullets.append(f"El precio está prácticamente en el objetivo promedio de analistas "
                          f"({_fmt_price_round(objetivo)}).")
    return bullets


def _plan_de_accion(ticker: str, top: EstrategiaOpciones | None, sma50: float | None,
                     maximo_52s: float | None, fecha_resultados: str | None) -> list[str]:
    """Solo se llama cuando la conclusión es "esperar" -- qué tendría que
    pasar para volver a considerar la operación. sma50 (media móvil de 50
    días) y maximo_52s (máximo de 52 semanas) son niveles técnicos reales,
    no porcentajes inventados; si no se pueden calcular (pocos datos),
    esas líneas simplemente no aparecen. El nivel de ruptura usa el
    máximo de 52 semanas -- no el breakeven de la estrategia -- porque el
    breakeven mide dónde una estrategia de OPCIONES específica empieza a
    ganar, no dónde técnicamente se confirma que la tesis alcista de la
    ACCIÓN se reactivó (para una estrategia de ingreso como Covered Call,
    el breakeven queda por debajo del spot y no serviría como señal de
    ruptura al alza)."""
    lineas = ["📝 Plan de acción", "", "Esperaría. No abriría posición hoy."]
    if sma50 is not None:
        lineas += ["", "Precio ideal para volver a revisar:",
                  f"cerca de {_fmt_price_round(sma50)} (media móvil de 50 días)"]
    if maximo_52s is not None and top is not None:
        lineas += ["", f"Si rompe arriba de {_fmt_price_round(maximo_52s)} (máximo de 52 semanas), "
                       f"volvería a analizar {top.nombre}."]
    if fecha_resultados:
        lineas += ["", f"Volver a revisar: 2 días antes de resultados ({_fmt_fecha_es(fecha_resultados)})."]
    return lineas


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
        lineas.append(f"{_emoji_score(score_screener)} Score cuantitativo del modelo: {score_screener:.0f}/100")
    else:
        lineas.append("⚪ Score cuantitativo del modelo: No disponible (no está en la shortlist de hoy)")
    lineas += ["", SEP, "", "📌 Mi conclusión", ""]

    preambulo, veredicto_acciones = _conclusion_acciones(tesis_categoria)
    if preambulo:
        lineas.append(preambulo)
        lineas.append("")
    lineas.append(veredicto_acciones)
    lineas.append(_conclusion_opciones(estrategias, score_100))
    lineas += ["", SEP, "", "🎯 Tesis del modelo", "",
               f"{_TESIS_EMOJI[tesis_categoria]} {_TESIS_DISPLAY[tesis_categoria]}", "↓"]

    if top is None:
        lineas.append("No pude calcular estrategias de opciones para hoy (cadena insuficiente o no disponible).")
    else:
        direccion_top = direccion_estrategia(top.nombre)
        coincide = _tesis_coincide_con_estrategia(tesis_categoria, direccion_top)
        lineas.append("Mejor estrategia" if coincide else "Si aun así quieres operar...")
        lineas += [top.nombre, "", f"Score cuantitativo: {score_100}/100", "", f"¿Por qué {top.nombre}?", ""]

        explicacion = _explicacion_estrategia(top.nombre, ticker)
        if explicacion:
            lineas.append(explicacion)
            lineas.append("")
        for b in _por_que_bullets(rsi, fund, spot):
            lineas.append(b)
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
            "", SEP, "", "¿Qué tiene que pasar para que esta estrategia gane?", "",
        ]

        vencimiento_txt = _fmt_fecha_es(cadena.vencimiento)
        lineas.append("✅ Para ganar necesitas:")
        lineas.append(_condicion_para_ganar(top, ticker, vencimiento_txt))
        riesgos_top = _riesgos_estrategia(top.nombre, ticker)
        if riesgos_top:
            lineas += ["", "Lo que puede salir mal", ""]
            for r in riesgos_top:
                lineas.append(f"• {r}")
        lineas.append("")

    lineas += [SEP, "", "⚠️ Riesgos", ""]
    if riesgos_reales:
        for r in riesgos_reales:
            lineas.append(f"• {r}")
    else:
        lineas.append("Sin banderas de riesgo técnico relevantes detectadas hoy.")
    lineas.append("")

    if tesis_categoria == "esperar":
        sma50 = tech.sma(barras.close, 50)
        maximo_52s = tech.maximo_52s(barras)
        fecha_resultados = _obtener_fecha_resultados(ticker)
        lineas += [SEP, ""]
        lineas += _plan_de_accion(ticker, top, sma50, maximo_52s, fecha_resultados)
        lineas.append("")

    lineas += [SEP, "", "Próximo paso", "", f"/report {ticker}", f"/options {ticker} --full", ""]
    lineas.append("Esto no es una recomendación de compra/venta.")

    return "\n".join(lineas)
