"""Genera /trade TICKER [--full].

Filosofía (redefinida 2026-07-23 tras feedback directo: "no quiero leer
60 líneas, quiero saber si compro o no, a qué precio, dónde el stop, cuál
el objetivo, qué estrategia, cuánto capital y qué alerta pongo"): el modo
por defecto ("simple") responde exactamente esas 7 preguntas en menos de
20 líneas -- "🎯 Mi decisión" con un solo emoji + veredicto, Entrada
ideal/Stop/Objetivo (mismos niveles ATR/SMA50/máximo de 52 semanas de
siempre), Horizonte (vencimiento real), Capital mínimo (solo la acción y
la estrategia top, no las 9), La estrategia que usaría, un "¿Por qué?" de
una sola frase que reusa hechos ya calculados (tendencia, valuación,
crecimiento, RSI, objetivo de analistas -- nunca un dato nuevo ni un
LLM), y un Próximo paso con la alerta a crear.

Nada del detalle anterior se perdió: `/trade TICKER --full` sigue
mostrando el tablero completo (Semáforo del modelo, Confianza en el
plan, Plan de acción, Qué tiene que pasar para ganar, Capital mínimo de
las 9 estrategias, Alertas con estimado de días, etc. -- ver
`_ensamblar_full`) para quien sí quiera profundizar. /report y /options
siguen existiendo tal cual para cuando SÍ quieras profundizar aún más
por ese lado -- /trade no los reemplaza.

Principio #3 (igual que /report y /options): NINGÚN número de este
comando es una predicción de resultado ni una recomendación de compra/
venta. "Score cuantitativo" (antes "Convicción") es el score real del
screener (0-100, `screener.scoring.puntuar()`) o el score compuesto real
del ranking de estrategias (`options_strategies.puntuar()`) -- ninguno
de los dos es una probabilidad de éxito. "Semáforo del modelo" (antes
"Score de oportunidad hoy", con un % que se prestaba a leerse como
probabilidad de ganar -- corregido tras ver el primer output real) usa
emoji + texto cualitativo en vez de un porcentaje, y resuelve en el
mismo texto la aparente contradicción de "opciones sí, pero no hoy": si
la tesis es "esperar", la línea de Opciones lo dice explícitamente en
vez de dejarlo para que el usuario lo infiera.

100% determinístico, sin LLM: ninguna sección depende de una llamada a
Claude. El bloque "¿Por qué [estrategia]?" combina una explicación FIJA
por tipo de estrategia (qué gana, qué arriesga, cuál es el problema
estructural) con hechos reales del día (RSI, precio vs. objetivo de
analistas). "Confianza en este plan" es lo mismo en espíritu: una lista
fija de factores reales a favor/en contra (tendencia, RSI, valuación,
liquidez, crecimiento de ingresos) con un % de cuántos aplican -- se
etiqueta explícitamente como eso, nunca como probabilidad de éxito.

Coherencia con la tesis: antes de presentar la estrategia top se compara
la tesis del screener (Alcista/Bajista/Esperar/Neutral) con la dirección
real de esa estrategia (`options_strategies.direccion_estrategia`). El
ranking matemático NUNCA cambia por esto -- ver `_tesis_coincide_con_
estrategia`. Solo cambia cómo se presenta.

Plan de acción / Plan del trade / Alertas para Yahoo Finance: solo
aparecen cuando la conclusión es "esperar". Los niveles de precio
(entrada, ideal, cancelar, objetivo 1, objetivo 2) salen de reglas
objetivas -- ATR (misma convención de 2×ATR de stop que ya usa
wizards_bot.py), media móvil de 50 días y máximo de 52 semanas -- nunca
un número inventado. Objetivo 2 es una extensión de igual distancia más
allá del objetivo 1 (medida desde la entrada), una convención técnica
estándar, no un número arbitrario. Cada línea de "Alertas para Yahoo
Finance" incluye de dónde sale ese nivel, para que quede claro que no es
arbitrario.

Horizonte esperado: el vencimiento REAL de la opción elegida (nunca un
rango de tiempo inventado como "3-6 semanas" sin base) -- el trade está
atado a esa fecha, se dice explícitamente para que no se confunda con
una tesis de largo plazo.

Capital mínimo recomendado: reempaqueta `riesgo_maximo` (el capital real
comprometido, ya calculado por el motor de opciones para cada estrategia
construida) más el costo de comprar 100 acciones al precio actual --
ningún número nuevo, solo agrupado para planear cuánto capital necesitas
antes de decidir cuál estrategia perseguir. Se muestra siempre que haya
estrategias, no solo cuando la conclusión es "esperar".

Alertas para Yahoo Finance: cada alerta ahora también incluye un
estimado (nunca una garantía) de cuántos días de operación podría tardar
en activarse, a partir de la volatilidad anualizada real -- ver
_dias_estimados(). Se devuelve como un rango, no un solo número, porque
el movimiento de precios es aleatorio y una sola cifra daría una falsa
precisión.

Mi decisión hoy: última sección del mensaje, resume todo el reporte en
una sola acción concreta (para quien ya leyó todo, o se saltó directo al
final) -- no agrega ningún dato nuevo, reempaqueta la misma tesis y los
mismos niveles ya calculados arriba.
"""

from __future__ import annotations

import logging
import math
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
from screener.options_ideas import CadenaOpciones, clasificar_tendencia, obtener_cadena, proxima_fecha_resultados  # noqa: E402
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
_VEREDICTO_EMOJI = {
    "alcista": "✅", "bajista": "❌", "esperar": "🟡",
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


def _emoji_opciones(estrategias: list[EstrategiaOpciones], score_100: float | None) -> str:
    if not estrategias:
        return "🔴"
    if score_100 is not None and score_100 < 40:
        return "🟡"
    return "🟢"


def _texto_opciones_semaforo(estrategias: list[EstrategiaOpciones], score_100: float | None,
                              tesis_categoria: str) -> str:
    """Versión corta para el Semáforo -- resuelve en la misma línea la
    aparente contradicción "opciones sí, pero no hoy": si la tesis es
    "esperar", lo dice explícitamente en vez de dejar que el usuario
    infiera por qué "Sí investigaría" convive con "No abriría hoy"."""
    if not estrategias:
        return "No hay estrategias disponibles hoy"
    base = "Con cautela" if (score_100 is not None and score_100 < 40) else "Sí investigaría"
    if tesis_categoria == "esperar":
        return f"{base}, pero no abriría hoy"
    return base


def _riesgo_nivel(n_riesgos_reales: int) -> tuple[str, str]:
    if n_riesgos_reales == 0:
        return "🟢", "Bajo"
    if n_riesgos_reales <= 2:
        return "🟡", "Medio"
    return "🔴", "Alto"


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


# ATR_MULT_ENTRADA/ATR_MULT_STOP/_niveles_precio ahora viven en
# screener.factors.technical (niveles_precio) porque telegram_bot/
# report_command.py también los reutiliza -- report_command.py ya importa
# de este módulo, así que moverlos ahí evita un import circular. Se
# mantienen estos nombres localmente para no tocar los ~90 usos/tests
# existentes en este archivo.
ATR_MULT_ENTRADA = tech.ATR_MULT_ENTRADA
ATR_MULT_STOP = tech.ATR_MULT_STOP
_niveles_precio = tech.niveles_precio


def _objetivo_2(objetivo_1: float | None, entrada: float | None) -> float | None:
    """Extensión de igual distancia más allá del objetivo 1, medida desde
    la entrada -- convención técnica estándar ("measured move"), no un
    número arbitrario."""
    if objetivo_1 is None or entrada is None:
        return None
    return objetivo_1 + (objetivo_1 - entrada)


def _relacion_riesgo_beneficio(entrada: float | None, objetivo_1: float | None,
                               stop: float | None) -> float | None:
    if entrada is None or objetivo_1 is None or stop is None:
        return None
    riesgo = entrada - stop
    beneficio = objetivo_1 - entrada
    if riesgo <= 0 or beneficio <= 0:
        return None
    return beneficio / riesgo


def _dias_hasta(fecha_iso: str | None, hoy: date | None = None) -> int | None:
    if not fecha_iso:
        return None
    try:
        return (date.fromisoformat(fecha_iso) - (hoy or date.today())).days
    except ValueError:
        return None


_DIAS_ESTIMADOS_MAX = 500  # ~2 años bursátiles -- más allá de esto el estimado deja de ser útil


def _dias_estimados(spot: float | None, nivel: float | None, vol_anual: float | None) -> tuple[int, int] | None:
    """Estimado (nunca una garantía) de cuántos días de operación podría
    tardar el precio en recorrer la distancia hasta `nivel`, a partir de
    la volatilidad anualizada real (`tech.volatilidad_anual`) -- la
    distancia recorrida escala con σ√t, así que despejando t se obtiene
    un estimado central. Se devuelve un rango (la mitad al doble de ese
    estimado) en vez de un solo número: el movimiento de precios es
    aleatorio, y un solo día daría una falsa precisión que no existe."""
    if not spot or not nivel or spot <= 0 or nivel <= 0 or not vol_anual or vol_anual <= 0:
        return None
    distancia_log = abs(math.log(nivel / spot))
    if distancia_log == 0:
        return None
    sigma_diaria = vol_anual / math.sqrt(252)
    dias_central = (distancia_log / sigma_diaria) ** 2
    if dias_central > _DIAS_ESTIMADOS_MAX:
        # Con la volatilidad medida, el nivel queda tan lejos que el
        # estimado deja de ser útil (leería como "millones de días") --
        # mejor omitir la línea que mostrar una cifra sin sentido.
        return None
    lo = max(1, round(dias_central * 0.5))
    hi = max(lo + 1, round(dias_central * 2.0))
    return (lo, hi)


def _plan_de_accion(ticker: str, top: EstrategiaOpciones | None, niveles: dict[str, float | None],
                     maximo_52s: float | None, fecha_resultados: str | None) -> list[str]:
    """Solo se llama cuando la conclusión es "esperar" -- qué tendría que
    pasar para volver a considerar la operación, en 4 preguntas fijas.
    Todos los niveles vienen de _niveles_precio()/maximo_52s (reglas
    objetivas, nunca inventadas); las líneas cuyo nivel no se pudo
    calcular simplemente no aparecen. El nivel de ruptura usa el máximo
    de 52 semanas -- no el breakeven de la estrategia -- porque el
    breakeven mide dónde una estrategia de OPCIONES específica empieza a
    ganar, no dónde técnicamente se confirma que la tesis alcista de la
    ACCIÓN se reactivó (para una estrategia de ingreso como Covered Call,
    el breakeven queda por debajo del spot y no serviría como señal de
    ruptura al alza)."""
    entrada, ideal, cancelar = niveles["entrada"], niveles["ideal"], niveles["cancelar"]
    lineas = ["🎯 Plan de acción", "", "No abriría posición hoy."]

    if ideal is not None:
        lineas += ["", "✅ Me interesaría comprar si:"]
        if entrada is not None and entrada > ideal:
            lineas.append(f"{ticker} corrige entre {_fmt_price_round(ideal)} y {_fmt_price_round(entrada)}.")
        else:
            lineas.append(f"{ticker} corrige a {_fmt_price_round(ideal)}.")

    if maximo_52s is not None and top is not None:
        lineas += ["", "🚀 También me interesaría si:",
                  f"Rompe arriba de {_fmt_price_round(maximo_52s)} (máximo de 52 semanas) -- "
                  f"volvería a analizar {top.nombre}."]

    if cancelar is not None:
        lineas += ["", "❌ Cancelaría la idea si:",
                  f"Cae debajo de {_fmt_price_round(cancelar)}, o si cambian los fundamentales."]

    condiciones = []
    if entrada is not None or maximo_52s is not None:
        condiciones.append("llega a alguno de estos niveles")
    if fecha_resultados:
        condiciones.append(f"en earnings (2 días antes, {_fmt_fecha_es(fecha_resultados)})")
    if condiciones:
        texto = " o ".join(condiciones)
        lineas += ["", "⏰ Volver a revisar:", f"Si {texto}."]
    return lineas


def _fmt_objetivo_con_rr(objetivo: float, rr: float | None) -> str:
    return f"{_fmt_price_round(objetivo)} (relación riesgo/beneficio {rr:.1f} : 1)" if rr is not None \
        else _fmt_price_round(objetivo)


def _plan_del_trade(entrada: float | None, objetivo_1: float | None, objetivo_2: float | None,
                     stop: float | None) -> list[str]:
    """Tabla compacta de referencia (entrada/objetivos/stop) -- mismos
    niveles que _plan_de_accion(), en formato numérico rápido de leer en
    vez de narrativo. La relación riesgo/beneficio se muestra junto a
    CADA objetivo (no como una sola línea ambigua) porque objetivo 1
    (el máximo de 52 semanas, un nivel técnico real) puede quedar cerca
    de la entrada justo en escenarios donde el precio ya está cerca de
    máximos -- exactamente el caso donde esta sección se activa (tesis
    "esperar" por sobrecompra). Mostrar la relación real de cada
    objetivo, aunque sea modesta, es más honesto que forzar un múltiplo
    fijo que no refleje el nivel técnico real. Vacía si no hay
    suficientes niveles calculados (nunca rellena con un valor
    inventado)."""
    if entrada is None or stop is None:
        return []
    lineas = ["🧮 Plan del trade", "", "Entrada:", _fmt_price_round(entrada)]
    if objetivo_1 is not None:
        rr1 = _relacion_riesgo_beneficio(entrada, objetivo_1, stop)
        lineas += ["", "Objetivo 1:", _fmt_objetivo_con_rr(objetivo_1, rr1)]
    if objetivo_2 is not None:
        rr2 = _relacion_riesgo_beneficio(entrada, objetivo_2, stop)
        lineas += ["", "Objetivo 2:", _fmt_objetivo_con_rr(objetivo_2, rr2)]
    lineas += ["", "Stop:", _fmt_price_round(stop)]
    return lineas


def _alertas_yahoo(niveles: dict[str, float | None], maximo_52s: float | None,
                    spot: float | None = None, vol_anual: float | None = None) -> list[str]:
    """Reempaqueta los mismos niveles ya calculados en _niveles_precio()
    en formato listo para configurar alertas de precio -- ningún cálculo
    nuevo. Cada alerta incluye de dónde sale ese nivel (ATR, media móvil
    de 50 días, máximo de 52 semanas) para que quede claro que no es un
    número arbitrario, y -- si hay spot/volatilidad disponibles -- un
    estimado (nunca una garantía) de cuántos días de operación podría
    tardar en activarse, vía _dias_estimados()."""
    entrada, ideal, cancelar = niveles["entrada"], niveles["ideal"], niveles["cancelar"]
    items = []
    if entrada is not None:
        items.append((f"🟢 Comprar si baja a: {_fmt_price_round(entrada)}",
                      "Es un retroceso de 1×ATR desde el precio actual.", entrada))
    if ideal is not None and ideal != entrada:
        items.append((f"🟢 Comprar con fuerza si baja a: {_fmt_price_round(ideal)}",
                      "Está cerca de la media móvil de 50 días, un soporte técnico real.", ideal))
    if maximo_52s is not None:
        items.append((f"🟡 Revisar si rompe: {_fmt_price_round(maximo_52s)}",
                      "Es el máximo de 52 semanas.", maximo_52s))
    if cancelar is not None:
        items.append((f"🔴 Cancelar la idea si cae debajo de: {_fmt_price_round(cancelar)}",
                      "Es 2×ATR por debajo del nivel ideal (mismo margen que usa el sistema para acciones).", cancelar))
    lineas = ["🔔 Alertas para Yahoo Finance", ""]
    for i, (item, porque, nivel) in enumerate(items):
        if i > 0:
            lineas.append("")
        lineas += [item, "¿Por qué?", porque]
        rango = _dias_estimados(spot, nivel, vol_anual)
        if rango:
            lineas.append(f"¿Cuándo? Podría activarse en aproximadamente {rango[0]}-{rango[1]} días "
                          f"de operación (estimación según la volatilidad reciente, no una garantía).")
    return lineas


def _capital_minimo(estrategias: list[EstrategiaOpciones], spot: float) -> list[str]:
    """Capital real requerido por estrategia -- ningún cálculo nuevo,
    reempaqueta `riesgo_maximo` (que para Covered Call y Cash Secured Put
    ya representa el capital comprometido, no solo la prima) de cada
    estrategia ya construida y rankeada, más "Comprar acciones" (spot ×
    100, el lote estándar de un contrato de opciones). Para planear
    cuánto capital necesitas antes de decidir cuál estrategia perseguir."""
    if not estrategias:
        return []
    lineas = ["💵 Capital mínimo recomendado", "", f"Comprar acciones (100): {_fmt_price_round(spot * 100)}"]
    for e in estrategias:
        if e.riesgo_maximo is not None:
            lineas.append(f"{e.nombre}: {_fmt_price_round(e.riesgo_maximo)}")
    return lineas


def _horizonte(cadena: CadenaOpciones) -> list[str]:
    """El vencimiento REAL de la opción elegida -- nunca un rango de
    tiempo inventado ("3-6 semanas" sin base). El trade está atado a esa
    fecha, se dice explícitamente para que no se confunda con una tesis
    de inversión de largo plazo."""
    return [
        "⏳ Horizonte esperado", "",
        "Este trade está atado al vencimiento de la opción elegida:",
        f"{_fmt_fecha_es(cadena.vencimiento)} (en {cadena.dias_a_vencimiento} días) -- "
        f"no es una inversión de largo plazo.",
    ]


def _factores_plan(tendencia_label: str, rsi: float | None, valoracion_label: str,
                    top: EstrategiaOpciones | None, fund: Fundamentales) -> tuple[list[str], list[str]]:
    """Factores reales a favor/en contra del plan -- mismo criterio de
    honestidad que el resto del motor: cada factor solo aparece si hay un
    dato real que lo sostenga, nunca se fuerza uno neutral hacia un lado
    u otro."""
    a_favor: list[str] = []
    en_contra: list[str] = []
    if tendencia_label == "alcista":
        a_favor.append("Tendencia alcista")
    elif tendencia_label == "bajista":
        en_contra.append("Tendencia bajista")
    if rsi is not None:
        if rsi >= 70:
            en_contra.append(f"RSI elevado ({rsi:.0f})")
        elif rsi <= 30:
            a_favor.append(f"RSI bajo ({rsi:.0f}) -- posible rebote")
    if valoracion_label == "Atractiva":
        a_favor.append("Valuación atractiva")
    elif valoracion_label == "Exigente":
        en_contra.append("Valuación exigente")
    if top is not None and top.liquidez_score is not None:
        if top.liquidez_score >= 60:
            a_favor.append("Liquidez alta en las opciones")
        elif top.liquidez_score < 40:
            en_contra.append("Liquidez baja en las opciones")
    if fund.crecimiento_ingresos is not None:
        if fund.crecimiento_ingresos >= 0.10:
            a_favor.append(f"Crecimiento de ingresos fuerte ({fund.crecimiento_ingresos:.0%})")
        elif fund.crecimiento_ingresos < 0:
            en_contra.append("Ingresos en contracción")
    return a_favor, en_contra


def _confianza_plan(a_favor: list[str], en_contra: list[str]) -> int | None:
    total = len(a_favor) + len(en_contra)
    return round(100 * len(a_favor) / total) if total else None


def _seccion_confianza_plan(a_favor: list[str], en_contra: list[str]) -> list[str]:
    if not a_favor and not en_contra:
        return []
    confianza = _confianza_plan(a_favor, en_contra)
    lineas = [
        "📋 Confianza en este plan",
        "(factores objetivos a favor vs. en contra -- no una probabilidad de éxito)",
        "",
        f"{confianza}/100" if confianza is not None else "No disponible",
        "",
    ]
    if a_favor:
        lineas.append("Factores a favor")
        for f in a_favor:
            lineas.append(f"✔ {f}")
        lineas.append("")
    if en_contra:
        lineas.append("Factores en contra")
        for f in en_contra:
            lineas.append(f"✘ {f}")
    return lineas


def _resumen_15s(tesis_categoria: str, veredicto_acciones: str, preambulo: str | None,
                  niveles: dict[str, float | None], maximo_52s: float | None,
                  top: EstrategiaOpciones | None, dias_vencimiento: int | None,
                  riesgo_emoji: str, riesgo_label: str) -> list[str]:
    """El resumen de 15 segundos -- para quien tiene prisa, lee solo esto
    y ya sabe qué hacer, a qué precio, con qué estrategia, en qué
    horizonte y con qué riesgo. Todo reusa valores ya calculados más
    abajo en el mensaje -- ningún cálculo nuevo."""
    emoji = _VEREDICTO_EMOJI.get(tesis_categoria, "⚪")
    lineas = ["📌 En 15 segundos", "", f"{emoji} {veredicto_acciones}"]
    if niveles.get("ideal") is not None:
        lineas.append(f"Esperaría una corrección hacia {_fmt_price_round(niveles['ideal'])}.")
    elif preambulo:
        lineas.append(preambulo)
    if maximo_52s is not None and top is not None:
        lineas.append(f"Si rompe {_fmt_price_round(maximo_52s)} volvería a analizar.")
    if top is not None:
        lineas += ["", "La mejor estrategia hoy:", top.nombre]
    if dias_vencimiento is not None:
        lineas += ["", "Horizonte:", f"{dias_vencimiento} días (vencimiento de la opción)"]
    lineas += ["", "Riesgo:", f"{riesgo_emoji} {riesgo_label}"]
    return lineas


def _mi_decision_hoy(tesis_categoria: str, niveles: dict[str, float | None],
                      maximo_52s: float | None, top: EstrategiaOpciones | None) -> list[str]:
    """Última sección del mensaje -- resume todo el reporte en una sola
    acción concreta, para quien ya leyó todo (o se saltó directo al
    final). No agrega ningún dato nuevo: reempaqueta la misma tesis y los
    mismos niveles ya calculados arriba en el mensaje."""
    lineas = ["✅ Mi decisión hoy", ""]
    if tesis_categoria == "esperar":
        lineas.append("No hago nada por ahora.")
        alertas = []
        if niveles.get("entrada") is not None:
            alertas.append(f"Comprar si baja a {_fmt_price_round(niveles['entrada'])}")
        if maximo_52s is not None and top is not None:
            alertas.append(f"Revisar si rompe {_fmt_price_round(maximo_52s)}")
        if alertas:
            lineas.append("Tengo alertas puestas:")
            for a in alertas[:2]:
                lineas.append(f"• {a}")
    elif tesis_categoria == "alcista":
        lineas.append("Consideraría abrir posición hoy (acción u opciones, según mi perfil de riesgo).")
    elif tesis_categoria == "bajista":
        lineas.append("No abro posición larga hoy -- la tendencia técnica es bajista.")
    else:
        lineas.append("No hay una señal lo suficientemente clara para actuar hoy.")
    return lineas


_MI_DECISION_TEXTO = {
    "alcista": ("🟢", "Sí compraría hoy."),
    "esperar": ("🟡", "No compraría hoy. Esperaría."),
    "bajista": ("🔴", "No compraría hoy -- la tendencia técnica es bajista."),
    "neutral": ("⚪", "No compraría hoy -- no hay una señal técnica clara."),
    "no_determinable": ("⚪", "No tengo suficiente información técnica para decidir hoy."),
}


def _por_que_decision(ticker: str, tendencia_label: str, valoracion_label: str,
                       fund: Fundamentales, rsi: float | None, spot: float) -> str | None:
    """Una sola frase de justificación de "Mi decisión" -- reusa hechos ya
    calculados (tendencia, valuación, crecimiento, RSI, objetivo de
    analistas), nunca un dato nuevo ni un LLM. None si no hay ningún
    hecho real que la sostenga (nunca se inventa una razón)."""
    fragmentos = []
    if tendencia_label == "alcista":
        fragmentos.append("está en tendencia alcista")
    elif tendencia_label == "bajista":
        fragmentos.append("está en tendencia bajista")
    if valoracion_label == "Atractiva":
        fragmentos.append("la valuación es atractiva")
    elif valoracion_label == "Exigente":
        fragmentos.append("la valuación es exigente")
    if fund.crecimiento_ingresos is not None and fund.crecimiento_ingresos >= 0.10:
        fragmentos.append(f"está creciendo {fund.crecimiento_ingresos:.0%}")
    if rsi is not None and rsi >= 70:
        fragmentos.append(f"el RSI está en sobrecompra ({rsi:.0f})")
    elif rsi is not None and rsi <= 30:
        fragmentos.append(f"el RSI está en sobreventa ({rsi:.0f})")
    if fund.analista_precio_objetivo is not None and spot and fund.analista_precio_objetivo > spot:
        fragmentos.append("todavía tiene recorrido según los analistas")
    if not fragmentos:
        return None
    cuerpo = fragmentos[0] if len(fragmentos) == 1 else ", ".join(fragmentos[:-1]) + " y " + fragmentos[-1]
    return f"Porque {ticker} {cuerpo}."


def _mensaje_simple(
    ticker: str, nombre: str, spot: float, tesis_categoria: str, por_que: str | None,
    niveles: dict[str, float | None], objetivo_1: float | None, cadena: CadenaOpciones | None,
    top: EstrategiaOpciones | None, direccion_top_coincide: bool | None,
) -> str:
    """Modo por defecto de /trade -- responde 7 preguntas en menos de 20
    líneas: ¿compro o no?, ¿a qué precio?, ¿dónde el stop?, ¿cuál el
    objetivo?, ¿qué estrategia?, ¿cuánto capital?, ¿qué alerta pongo?
    Pedido explícito: el diseño anterior (Semáforo, Confianza, Plan de
    acción, etc.) tenía demasiadas secciones para leer todos los días --
    ese detalle sigue disponible en /trade TICKER --full, no se pierde,
    solo se deja de mostrar por defecto."""
    emoji, texto = _MI_DECISION_TEXTO.get(
        tesis_categoria, ("⚪", "No tengo suficiente información técnica para decidir hoy."))
    lineas = [f"📊 {ticker} — {nombre}", "", SEP, "", "🎯 Mi decisión", "", f"{emoji} {texto}"]

    if niveles.get("entrada") is not None:
        lineas += ["", "Entrada ideal:", _fmt_price_round(niveles["entrada"])]
    else:
        lineas += ["", "Precio actual:", _fmt_price_round(spot)]

    if niveles.get("cancelar") is not None:
        lineas += ["", "Stop:", _fmt_price_round(niveles["cancelar"])]

    if objetivo_1 is not None:
        rr = _relacion_riesgo_beneficio(niveles.get("entrada"), objetivo_1, niveles.get("cancelar"))
        lineas += ["", "Objetivo:", _fmt_objetivo_con_rr(objetivo_1, rr)]

    if cadena is not None:
        lineas += ["", "Horizonte:", f"{cadena.dias_a_vencimiento} días (vencimiento de la opción elegida)"]

    if top is not None:
        lineas += ["", "Capital mínimo:", f"{_fmt_price_round(spot * 100)} (comprar 100 acciones)"]
        if top.riesgo_maximo is not None:
            lineas += ["o", f"{_fmt_price_round(top.riesgo_maximo)} ({top.nombre})"]
        lineas += ["", "La estrategia que usaría:", top.nombre]
        if direccion_top_coincide is False:
            lineas.append("(no coincide con la tesis de hoy -- ver /trade TICKER --full)")
    else:
        lineas += ["", "Estrategia con opciones:", "No disponible hoy (cadena insuficiente)."]

    if por_que:
        lineas += ["", "¿Por qué?", "", por_que]

    if niveles.get("entrada") is not None:
        lineas += ["", "Próximo paso:", "", f"✅ Crear alerta en {_fmt_price_round(niveles['entrada'])}."]

    lineas += ["", SEP, "",
              f"Para el detalle completo (Semáforo, Confianza en el plan, Qué tiene que pasar "
              f"para ganar, Plan de acción, Alertas con estimado de días, Riesgos): "
              f"/trade {ticker} --full"]
    lineas += ["", SEP, "", "Esto no es una recomendación de compra/venta."]
    return "\n".join(lineas)


def _ensamblar_full(
    ticker: str, nombre: str, spot: float, fund: Fundamentales, score_screener: float | None,
    rsi: float | None, tendencia_label: str, riesgos_reales: list[str], riesgo_emoji: str,
    riesgo_label: str, cadena: CadenaOpciones | None, estrategias: list[EstrategiaOpciones],
    top: EstrategiaOpciones | None, score_100: int | None, tesis_categoria: str,
    fecha_resultados: str | None, valoracion_label: str, maximo_52s: float | None,
    niveles: dict[str, float | None], objetivo_1: float | None, objetivo_2: float | None,
    vol: float | None,
) -> str:
    """El tablero completo de antes (/trade TICKER --full): Semáforo,
    Tesis + estrategia con la capa de coherencia, Plan del trade, Ejemplo,
    Capital mínimo (las 9 estrategias), Qué tiene que pasar para ganar,
    Horizonte, Confianza en el plan, Riesgos, Plan de acción + Alertas
    (solo si la tesis es "esperar"), y Mi decisión hoy de cierre."""
    preambulo, veredicto_acciones = _conclusion_acciones(tesis_categoria)

    lineas = [f"📊 {ticker} — {nombre}", "", SEP, ""]
    lineas += _resumen_15s(tesis_categoria, veredicto_acciones, preambulo, niveles, maximo_52s,
                           top, cadena.dias_a_vencimiento if cadena else None, riesgo_emoji, riesgo_label)
    lineas += ["", SEP, "", "📊 Semáforo del modelo", "",
               f"Acciones: {_TESIS_EMOJI[tesis_categoria]} {_TESIS_DISPLAY[tesis_categoria]}",
               f"Opciones: {_emoji_opciones(estrategias, score_100)} "
               f"{_texto_opciones_semaforo(estrategias, score_100, tesis_categoria)}"]
    if score_screener is not None:
        lineas.append(f"Score cuantitativo del modelo: {_emoji_score(score_screener)} {score_screener:.0f}/100")
    else:
        lineas.append("Score cuantitativo del modelo: No disponible (no está en la shortlist de hoy)")

    lineas += ["", SEP, "", "📌 Mi conclusión", ""]
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

        if tesis_categoria == "esperar":
            plan_trade = _plan_del_trade(niveles["entrada"], objetivo_1, objetivo_2, niveles["cancelar"])
            if plan_trade:
                lineas += ["", SEP, ""]
                lineas += plan_trade

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
        ]

        capital = _capital_minimo(estrategias, spot)
        if capital:
            lineas += ["", SEP, ""]
            lineas += capital

        lineas += ["", SEP, "", "¿Qué tiene que pasar para que esta estrategia gane?", ""]

        vencimiento_txt = _fmt_fecha_es(cadena.vencimiento)
        lineas.append("✅ Para ganar necesitas:")
        lineas.append(_condicion_para_ganar(top, ticker, vencimiento_txt))
        riesgos_top = _riesgos_estrategia(top.nombre, ticker)
        if riesgos_top:
            lineas += ["", "Lo que puede salir mal", ""]
            for r in riesgos_top:
                lineas.append(f"• {r}")
        lineas.append("")

        lineas += [SEP, ""]
        lineas += _horizonte(cadena)
        lineas.append("")

        a_favor, en_contra = _factores_plan(tendencia_label, rsi, valoracion_label, top, fund)
        seccion_confianza = _seccion_confianza_plan(a_favor, en_contra)
        if seccion_confianza:
            lineas += [SEP, ""]
            lineas += seccion_confianza
            lineas.append("")

    lineas += [SEP, "", "⚠️ Riesgos", ""]
    if riesgos_reales:
        for r in riesgos_reales:
            lineas.append(f"• {r}")
    else:
        lineas.append("Sin banderas de riesgo técnico relevantes detectadas hoy.")
    lineas.append("")

    if tesis_categoria == "esperar":
        lineas += [SEP, ""]
        lineas += _plan_de_accion(ticker, top, niveles, maximo_52s, fecha_resultados)
        lineas.append("")

        if any(v is not None for v in niveles.values()) or maximo_52s is not None:
            lineas += [SEP, ""]
            lineas += _alertas_yahoo(niveles, maximo_52s, spot, vol)
            lineas.append("")

    lineas += [SEP, ""]
    lineas += _mi_decision_hoy(tesis_categoria, niveles, maximo_52s, top)
    lineas.append("")

    lineas += [SEP, "", "Próximo paso", "", f"/report {ticker}", f"/options {ticker} --full", ""]
    lineas.append("Esto no es una recomendación de compra/venta.")

    return "\n".join(lineas)


def generar_trade(ticker: str, modo: str = "simple") -> str:
    """Punto de entrada de /trade TICKER [--full]. Nunca lanza: cualquier
    fallo se convierte en un mensaje de error legible.

    Modo "simple" (por defecto): responde 7 preguntas en menos de 20
    líneas -- ¿compro o no?, ¿a qué precio?, ¿dónde pongo el stop?,
    ¿cuál es el objetivo?, ¿qué estrategia usar?, ¿cuánto capital
    necesito?, ¿qué alerta pongo? -- pedido explícito tras feedback de
    que el diseño anterior (Semáforo, Confianza en el plan, Plan de
    acción, Qué tiene que pasar para ganar, etc. -- ~12 secciones) era
    demasiado largo para abrir todos los días.

    Modo "full": el tablero completo de antes -- nada de ese detalle se
    perdió, solo se dejó de mostrar por defecto (ver _ensamblar_full)."""
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
    riesgo_emoji, riesgo_label = _riesgo_nivel(len(riesgos_reales))

    cadena = obtener_cadena(ticker)
    estrategias = rankear(construir_estrategias(cadena, spot)) if cadena else []
    top = estrategias[0] if estrategias else None
    score_100 = round(puntuar(estrategias)[0] * 100) if estrategias else None

    tesis_categoria = _tesis_categoria(tendencia_label, valoracion_label, rsi)
    fecha_resultados = _obtener_fecha_resultados(ticker)

    sma50 = tech.sma(barras.close, 50)
    maximo_52s = tech.maximo_52s(barras)
    atr_val = tech.atr(barras)
    niveles = _niveles_precio(spot, atr_val, sma50)
    objetivo_1 = maximo_52s
    objetivo_2 = _objetivo_2(objetivo_1, niveles["entrada"])

    if modo == "full":
        return _ensamblar_full(
            ticker, nombre, spot, fund, score_screener, rsi, tendencia_label, riesgos_reales,
            riesgo_emoji, riesgo_label, cadena, estrategias, top, score_100, tesis_categoria,
            fecha_resultados, valoracion_label, maximo_52s, niveles, objetivo_1, objetivo_2, vol,
        )

    por_que = _por_que_decision(ticker, tendencia_label, valoracion_label, fund, rsi, spot)
    direccion_top_coincide = (
        _tesis_coincide_con_estrategia(tesis_categoria, direccion_estrategia(top.nombre))
        if top is not None else None)
    return _mensaje_simple(ticker, nombre, spot, tesis_categoria, por_que, niveles, objetivo_1,
                           cadena, top, direccion_top_coincide)
