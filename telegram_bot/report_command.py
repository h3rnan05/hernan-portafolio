"""Genera el memo de /report TICKER bajo demanda.

Filosofía (redefinida tras feedback directo del dueño del producto,
2026-07-23): el objetivo de /report NO es mostrar todos los datos
disponibles -- es ayudar a un inversionista a decidir en menos de un
minuto si vale la pena seguir investigando esa empresa. El modo por
defecto ("simple") responde, en este orden: (1) ¿vale la pena
investigarla?, (2) ¿comprar hoy, esperar o descartarla?, (3) ¿qué me
gusta?, (4) ¿qué no me gusta?, (5) ¿qué es lo importante ahora mismo?,
(6) ¿qué pasó esta semana? (resumen, nunca una lista de titulares suelta),
(7) qué haría yo y a qué precio. Ningún dato faltante se muestra como
"No disponible" en este modo -- si no está, se omite. Solo con
`/report TICKER --full` se despliega el memo exhaustivo de antes (score
breakdown, técnico/fundamentales completos, consenso de analistas
detallado, noticias clasificadas por relevancia) para quien sí quiera
profundizar -- ahí "No disponible" sigue siendo honesto porque esa vista
existe justamente para mostrar TODO lo que se intentó obtener.

Esta es la investigación profunda que el mensaje diario del screener YA NO
manda automáticamente (ver screener/report.texto_telegram_corto): se pide
explícitamente, para un ticker a la vez. Reutiliza los mismos módulos
reales que ya existen (screener, news_analyst) -- nunca corre sobre el
universo completo, así que es rápido y barato de correr on-demand.

Honestidad de los datos: solo se muestra lo que de verdad se pudo obtener.
yfinance da un snapshot real de consenso de analistas y % de tenencia
institucional/insider (no el detalle de transacciones ni el histórico
13F -- eso no está disponible gratis, así que no se inventa).

100% determinístico salvo el resumen de noticias: el veredicto (¿vale la
pena investigar? ¿comprar hoy?), "Lo que me gusta"/"Lo que no me gusta" y
los niveles de precio son texto DETERMINÍSTICO armado con plantillas
sobre números ya calculados -- ningún LLM interviene ahí (no hace falta:
son solo reglas fijas). El LLM SOLO se usa para resumir noticias
(news_analyst.explicador.resumir_noticias/generar_explicacion), con los
mismos guardrails de siempre (prompt + filtro de palabras prohibidas) --
nunca decide si comprar o vender (Principio #3, ver ROADMAP.md). Si el
LLM no está disponible o su respuesta no pasa el filtro, se degrada
mostrando los titulares crudos en vez de dejar la sección vacía en
silencio. Ver telegram_bot/README.md para el detalle de qué es real y qué
quedó fuera.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from news_analyst.explicador import generar_explicacion, resumir_noticias  # noqa: E402
from news_analyst.models import EntradaShortlist, Explicacion  # noqa: E402
from screener.config import CONFIG  # noqa: E402
from screener.data.provider import Barras, Fundamentales, YahooProvider  # noqa: E402
from screener.factors import technical as tech  # noqa: E402
from screener.options_ideas import clasificar_tendencia, proxima_fecha_resultados  # noqa: E402
from screener.report import razones, texto_telegram_lista_completa  # noqa: E402
from screener.scoring import Puntuacion  # noqa: E402
from wizards_bot import titulares_google_news  # noqa: E402

log = logging.getLogger("telegram_bot.report_command")

SHORTLIST_PATH = _REPO_ROOT / "screener" / "shortlist_hoy.json"
MAX_TITULARES_REPORTE = 5

SEP = "━━━━━━━━━━━━━━━━━━"

_NOMBRE_FACTOR = {
    "momentum": "Momentum", "tendencia": "Tendencia", "baja_vol": "Volatilidad",
    "liquidez": "Liquidez", "calidad": "Calidad", "valor": "Valor",
}

DISCLAIMER = (
    "Esto NO es una recomendación de compra/venta. Es un resumen "
    "informativo para que investigues más a fondo."
)


def _fmt_pct(x: float | None) -> str:
    return f"{x:.1%}" if x is not None else "No disponible"


def _fmt_precio(x: float | None) -> str:
    return f"${x:,.2f}" if x is not None else "No disponible"


def _shortlist_entry(ticker: str) -> dict | None:
    """La fila de hoy para este ticker (con su posición), o None si no
    pasó el screener hoy -- eso NO significa que el ticker sea malo, solo
    que no está en la shortlist de hoy."""
    if not SHORTLIST_PATH.exists():
        return None
    try:
        data = json.loads(SHORTLIST_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.debug("no pude leer shortlist_hoy.json: %s", e)
        return None
    for i, fila in enumerate(data.get("shortlist", [])):
        if fila["ticker"] == ticker:
            fila = dict(fila)
            fila["posicion"] = i + 1
            fila["universo_n"] = data.get("universo_escaneado")
            return fila
    return None


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


# ------------------------- interpretaciones (deterministas) -------------------------

def _interpretar_rsi(rsi: float | None) -> str:
    if rsi is None:
        return "No disponible."
    if rsi >= 70:
        return ("Zona de sobrecompra. Esto no significa que vaya a caer, pero "
                "históricamente aumenta la probabilidad de una pausa o corrección "
                "de corto plazo.")
    if rsi <= 30:
        return "Zona de sobreventa. Históricamente asociada a rebotes de corto plazo, aunque no lo garantiza."
    return "En rango neutral, sin señal de sobrecompra ni sobreventa."


def _interpretar_pe(pe: float | None) -> str:
    if pe is None:
        return "No disponible."
    if pe < 15:
        return "Valuación baja frente al promedio histórico del mercado (P/E < 15)."
    if pe <= 30:
        return "Valuación dentro de un rango razonable frente al mercado (P/E 15-30)."
    return ("Valuación exigente: el mercado ya está pagando un múltiplo alto por "
            "cada dólar de ganancia (P/E > 30).")


def _interpretar_roe(roe: float | None) -> str:
    if roe is None:
        return "No disponible."
    if roe >= 0.20:
        return "Muy alta rentabilidad sobre el capital que usa el negocio."
    if roe >= 0.10:
        return "Rentabilidad sólida sobre el capital."
    return "Rentabilidad sobre el capital por debajo de lo que se considera sólido (ROE < 10%)."


def _interpretar_pb(pb: float | None) -> str:
    if pb is None:
        return "No disponible."
    if pb < 1:
        return "Cotiza por debajo de su valor en libros."
    if pb <= 5:
        return "Cotiza en un múltiplo razonable sobre su valor en libros."
    return ("Cotiza muy por encima de su valor en libros -- común en negocios de "
            "alta calidad, pero encarece el margen de seguridad.")


def _interpretar_precio_objetivo(precio_objetivo: float | None, precio_actual: float | None) -> str:
    """Compara el precio objetivo promedio contra el precio actual -- el
    dato ya se mostraba, pero sin decir qué implica frente al precio de
    hoy, que es la pregunta real que le importa a quien lo lee."""
    if precio_objetivo is None or not precio_actual:
        return "No disponible."
    diff_pct = (precio_objetivo - precio_actual) / precio_actual
    if diff_pct < 0:
        return (f"El consenso de analistas está aproximadamente {abs(diff_pct):.1%} por "
                f"debajo del precio actual. Esto sugiere que, en promedio, el mercado ya "
                f"refleja gran parte de las expectativas positivas.")
    return (f"El consenso de analistas está aproximadamente {diff_pct:.1%} por encima del "
            f"precio actual. Esto sugiere que, en promedio, los analistas ven más recorrido al alza.")


def _texto_calidad(score: float | None) -> str:
    if score is None:
        return "desconocida"
    if score >= 90:
        return "muy alta"
    if score >= 70:
        return "alta"
    if score >= 40:
        return "media"
    if score >= 20:
        return "baja"
    return "muy baja"


def _clasificar_valoracion(pe: float | None, valor_subscore: float | None) -> str:
    """Prefiere el sub-score cross-sectional (valor_subscore, percentil
    contra el resto de la shortlist) cuando existe -- es más riguroso que
    un umbral fijo de P/E. Si el ticker no está en la shortlist, cae a
    umbrales fijos razonables sobre P/E."""
    if valor_subscore is not None:
        if valor_subscore >= 70:
            return "Atractiva"
        if valor_subscore >= 30:
            return "Razonable"
        return "Exigente"
    if pe is not None:
        if pe < 15:
            return "Atractiva"
        if pe <= 30:
            return "Razonable"
        return "Exigente"
    return "No determinable"


def _estrellas_100(score: float | None) -> str:
    if score is None:
        return "No disponible"
    # int(x + 0.5) en vez de round(): redondeo "hacia arriba" en los .5,
    # más intuitivo para una calificación en estrellas que el redondeo
    # bancario de Python (round(4.5) da 4, no 5).
    n = max(0, min(5, int(score / 20 + 0.5)))
    return "⭐" * n + "☆" * (5 - n)


# ------------------------- riesgos / catalizadores (deterministas) -------------------------

def _riesgos(rsi: float | None, pe: float | None, vol: float | None, tendencia_label: str) -> list[str]:
    """Banderas de riesgo por reglas fijas, en orden de severidad -- nunca
    el juicio de un LLM. Máximo 3, y si ninguna aplica se dice
    explícitamente (no forzar un riesgo que no existe)."""
    candidatos = []
    if rsi is not None and rsi >= 70:
        candidatos.append(f"RSI en {rsi:.0f} -- zona de sobrecompra, aumenta la "
                          f"probabilidad de una pausa o corrección de corto plazo.")
    if rsi is not None and rsi <= 30:
        candidatos.append(f"RSI en {rsi:.0f} -- zona de sobreventa.")
    if pe is not None and pe > 30:
        candidatos.append(f"Valuación exigente (P/E {pe:.1f}) frente a lo que sería razonable históricamente.")
    if vol is not None and vol >= 0.40:
        candidatos.append(f"Volatilidad histórica alta ({vol:.0%} anual) -- movimientos de precio más bruscos que el promedio.")
    if tendencia_label == "bajista":
        candidatos.append("Tendencia técnica bajista -- el precio está por debajo de sus promedios de largo plazo.")
    if not candidatos:
        candidatos.append("Sin banderas de riesgo técnico relevantes detectadas hoy con los datos disponibles.")
    return candidatos[:3]


def _catalizadores(fecha_resultados: str | None) -> list[str]:
    """Solo catalizadores que se pueden confirmar con datos reales -- no se
    inventan lanzamientos de producto ni recompras de acciones sin una
    fuente que los sostenga."""
    if fecha_resultados:
        return [f"Próximos resultados trimestrales: {fecha_resultados}."]
    return [
        "No identifiqué catalizadores confirmados con los datos disponibles "
        "hoy (solo se verifica la fecha de próximos resultados; no se "
        "inventan catalizadores -- lanzamientos de producto, recompras -- "
        "sin una fuente real que los confirme).",
    ]


# ------------------------- veredicto conciso (modo "simple", por defecto) -------------------------

def _veredicto_investigar(calidad_score: float | None, entrada: dict | None) -> tuple[str, str]:
    """(emoji, texto) -- ¿vale la pena investigar el NEGOCIO? Se basa en
    calidad fundamental (el factor "calidad" del screener, o si ya pasó
    todos los filtros hoy), NO en si es un buen momento para comprar --
    eso es una pregunta de timing separada (ver _timing). Un negocio
    puede valer la pena investigar incluso el día que no pasa el
    screener (ranking relativo contra el resto del mercado ese día en
    particular), como puede no valerlo aunque hoy sí haya pasado."""
    if entrada is not None or (calidad_score is not None and calidad_score >= 60):
        return "🟢", "Sí la investigaría."
    if calidad_score is not None and calidad_score < 30:
        return "🔴", "No la investigaría por ahora."
    return "🟡", "La investigaría con cautela."


def _timing(entrada: dict | None, tendencia_label: str, rsi: float | None) -> tuple[str, str]:
    """(código, texto) -- ¿comprar hoy, esperar o no por ahora? Puramente
    determinístico sobre si pasó el screener hoy (todos los filtros
    cuantitativos), la tendencia técnica y el RSI. El código se reutiliza
    después para decidir qué mostrar en "Qué no me gusta" y "Qué haría
    yo" sin repetir esta misma lógica dos veces."""
    if tendencia_label == "bajista":
        return "bajista", "🔴 No compraría hoy -- la tendencia técnica es bajista."
    if rsi is not None and rsi >= 70:
        return "sobrecomprado", f"🟡 No compraría hoy -- RSI en sobrecompra ({rsi:.0f}). Esperaría una corrección."
    if entrada is None:
        return "no_paso", "🟡 No compraría hoy porque no pasó el screener."
    return "compraria", "🟢 Sí, la compraría hoy."


def _en_20_segundos(
    investigar_emoji: str, investigar_texto: str, timing_codigo: str, timing_texto: str,
    precio_actual: float, niveles: dict[str, float | None], precio_objetivo: float | None,
) -> list[str]:
    """El resumen ejecutivo -- para quien tiene prisa, lee solo esto y ya
    sabe si vale la pena seguir, si compraría hoy, a qué precio le
    interesaría y contra qué objetivo de analistas lo mide. Ningún dato
    faltante se rellena: los niveles que no se pudieron calcular
    simplemente no aparecen."""
    lineas = ["📌 En 20 segundos", "", f"{investigar_emoji} {investigar_texto}", "", timing_texto]
    if timing_codigo != "compraria":
        lineas.append("La tendría en mi lista de seguimiento.")
    lineas += ["", "Precio actual:", _fmt_precio(precio_actual)]

    entrada_nivel, ideal_nivel = niveles.get("entrada"), niveles.get("ideal")
    if entrada_nivel is not None and ideal_nivel is not None and entrada_nivel != ideal_nivel:
        lo, hi = sorted((entrada_nivel, ideal_nivel))
        lineas += ["", "Me interesaría cerca de:", f"{_fmt_precio(lo)}-{_fmt_precio(hi)}"]
    elif entrada_nivel is not None:
        lineas += ["", "Me interesaría cerca de:", _fmt_precio(entrada_nivel)]

    if precio_objetivo is not None:
        lineas += ["", "Objetivo de analistas:", _fmt_precio(precio_objetivo)]
    return lineas


def _lo_que_me_gusta(
    tendencia_label: str, roe: float | None, crecimiento: float | None,
    valoracion: str, pe: float | None, liquidez_score: float | None,
) -> list[str]:
    """Fortalezas concretas -- cada bullet lleva el número real que lo
    sostiene (no una etiqueta genérica como "negocio de calidad"), para
    que la razón sea verificable de un vistazo."""
    items = []
    if tendencia_label == "alcista":
        items.append("Tendencia alcista.")
    if roe is not None and roe >= 0.15:
        items.append(f"Negocio rentable (ROE {roe:.1%}).")
    if crecimiento is not None and crecimiento >= 0.10:
        items.append(f"Crecimiento de ingresos de {crecimiento:.0%}.")
    if valoracion == "Atractiva" and pe is not None:
        items.append(f"Valuación barata (P/E {pe:.1f}).")
    if liquidez_score is not None and liquidez_score >= 70:
        items.append("Alta liquidez.")
    return items


def _lo_que_no_me_gusta(
    timing_codigo: str, rsi: float | None, valoracion: str, pe: float | None, vol: float | None,
) -> list[str]:
    """Debilidades concretas -- igual criterio que _lo_que_me_gusta: solo
    aparece lo que hay un dato real detrás, con el número incluido."""
    items = []
    if timing_codigo == "no_paso":
        items.append("No pasó todos los filtros cuantitativos del modelo hoy.")
    elif timing_codigo == "sobrecomprado":
        items.append(f"RSI en sobrecompra ({rsi:.0f}) -- no hay un punto de entrada atractivo todavía.")
    elif timing_codigo == "bajista":
        items.append("Tendencia técnica bajista.")
    if valoracion == "Exigente" and pe is not None:
        items.append(f"Valuación elevada (P/E {pe:.1f}).")
    if vol is not None and vol >= 0.40:
        items.append(f"Volatilidad histórica alta ({vol:.0%}).")
    return items


def _lo_importante(
    precio: float, pe: float | None, roe: float | None, crecimiento: float | None,
    rsi: float | None, analista_recomendacion: str | None, precio_objetivo: float | None,
) -> list[str]:
    """La conclusión en lenguaje llano va ARRIBA (ej. "Empresa rentable.",
    "Acción barata."), el dato técnico crudo (ROE/P/E/RSI) va ABAJO en una
    sola línea compacta -- pedido explícito: la mayoría de la gente no
    sabe interpretar un ROE o un P/E sueltos, así que el bot debe pensar
    como un asesor, no como una terminal de Bloomberg. A diferencia del
    modo --full, cualquier dato que no esté disponible se OMITE (nunca se
    muestra "No disponible"): esta sección existe para decidir rápido, no
    para auditar qué faltó."""
    conclusiones = [f"Precio: {_fmt_precio(precio)}"]
    if roe is not None:
        if roe >= 0.15:
            conclusiones.append("Empresa rentable.")
        elif roe < 0.05:
            conclusiones.append("Rentabilidad débil.")
        else:
            conclusiones.append("Rentabilidad moderada.")
    if pe is not None:
        if pe < 15:
            conclusiones.append("Acción barata.")
        elif pe <= 30:
            conclusiones.append("Valuación razonable.")
        else:
            conclusiones.append("Acción cara.")
    if rsi is not None:
        if rsi >= 70:
            conclusiones.append("Sobrecomprada -- cuidado con una pausa.")
        elif rsi <= 30:
            conclusiones.append("En sobreventa -- posible rebote.")
        else:
            conclusiones.append("No está sobrecomprada.")
    if crecimiento is not None:
        if crecimiento >= 0.10:
            conclusiones.append(f"Creciendo rápido ({crecimiento:+.0%}).")
        elif crecimiento < 0:
            conclusiones.append("Ingresos en contracción.")

    datos = []
    if pe is not None:
        datos.append(f"P/E {pe:.1f}")
    if roe is not None:
        datos.append(f"ROE {roe:.1%}")
    if rsi is not None:
        datos.append(f"RSI {rsi:.0f}")
    if crecimiento is not None:
        datos.append(f"Ingresos {crecimiento:+.0%}")
    if analista_recomendacion:
        datos.append(f"Analistas {analista_recomendacion.capitalize()}")
    if precio_objetivo is not None and precio:
        datos.append(f"Potencial {(precio_objetivo - precio) / precio:+.0%}")

    lineas = ["📊 Lo importante", ""] + conclusiones
    if datos:
        lineas += ["", " · ".join(datos)]
    return lineas


def _por_que_una_linea(timing_codigo: str, gusta: list[str], no_gusta: list[str]) -> str | None:
    """Compone UNA frase de justificación reusando los mismos hechos ya
    calculados en _lo_que_me_gusta()/_lo_que_no_me_gusta() -- pedido
    explícito: "Sí la compraría hoy" sin decir por qué no basta. Ningún
    dato nuevo ni LLM: solo une hasta 3 de los mismos bullets en una
    frase. None si no hay ningún hecho real que la sostenga (nunca se
    inventa una razón)."""
    base = gusta if timing_codigo == "compraria" else no_gusta
    if not base:
        return None
    # No se cambia la capitalización de cada fragmento: varios empiezan
    # con una sigla (ej. "RSI en sobrecompra...") y minusculizar a ciegas
    # la rompería ("rSI en sobrecompra...").
    fragmentos = [b.rstrip(".") for b in base[:3]]
    cuerpo = fragmentos[0] if len(fragmentos) == 1 else ", ".join(fragmentos[:-1]) + " y " + fragmentos[-1]
    return f"Porque {cuerpo}."


def _mi_plan_para_hoy(
    timing_codigo: str, por_que: str | None, niveles: dict[str, float | None], maximo_52s: float | None,
) -> list[str]:
    """Caja resumen al INICIO del mensaje -- para quien solo lee las
    primeras líneas: la decisión, el porqué en una frase, y una sola
    alerta ya lista para crear ("Ya puedes crear esta alerta" -- pedido
    explícito: esa es literalmente la siguiente acción). Reempaqueta los
    mismos valores que _timing()/_por_que_una_linea()/niveles_precio() --
    ningún cálculo nuevo."""
    lineas = ["🎯 Mi plan para hoy", ""]
    lineas.append("Sí, la compraría hoy." if timing_codigo == "compraria" else "No haría nada. Esperaría.")
    if por_que:
        lineas.append(por_que)
    nivel_alerta = maximo_52s if timing_codigo == "compraria" else niveles.get("entrada")
    if nivel_alerta is not None:
        lineas += ["", f"✅ Ya puedes crear esta alerta: {_fmt_precio(nivel_alerta)}",
                  "Si llega ahí, la vuelvo a analizar."]
    return lineas


def _que_haria_yo(
    timing_codigo: str, por_que: str | None, niveles: dict[str, float | None], maximo_52s: float | None,
) -> list[str]:
    """Cierre determinístico -- reusa los mismos niveles ya calculados
    arriba, ningún número nuevo. Incluye el mismo "Porque..." que
    _mi_plan_para_hoy() (misma frase, no se recalcula)."""
    lineas = ["🎯 Qué haría yo", ""]
    if timing_codigo == "compraria":
        lineas.append("Sí, la compraría hoy.")
        if por_que:
            lineas.append(por_que)
        return lineas
    lineas.append("No compraría hoy.")
    if por_que:
        lineas.append(por_que)
    if niveles.get("ideal") is not None:
        lineas.append(f"Esperaría una corrección hacia {_fmt_precio(niveles['ideal'])}.")
    else:
        lineas.append("Esperaría una mejor entrada.")
    if niveles.get("entrada") is not None:
        lineas.append(f"Si baja hacia {_fmt_precio(niveles['entrada'])} volvería a analizar.")
    if maximo_52s is not None:
        lineas.append(f"Si rompe nuevos máximos con fuerza (arriba de {_fmt_precio(maximo_52s)}) "
                      f"también volvería a revisar.")
    return lineas


def _alertas_reporte(
    niveles: dict[str, float | None], maximo_52s: float | None, fecha_resultados: str | None,
) -> list[str]:
    """Reencuadra los niveles de _niveles_precio()/maximo_52s como una
    lista de condiciones "o" -- "Comprar si ocurre UNA de estas cosas" --
    en vez de una tabla de precios sueltos sin conexión entre sí (pedido
    explícito: así es como de verdad se piensa una alerta). Vacía si no
    hay ninguna condición disponible (nunca se muestra un encabezado sin
    contenido)."""
    condiciones = []
    if niveles.get("entrada") is not None:
        condiciones.append(f"Corrige hacia {_fmt_precio(niveles['entrada'])}")
    if niveles.get("ideal") is not None and niveles["ideal"] != niveles.get("entrada"):
        condiciones.append(f"Rebota con fuerza en el soporte ({_fmt_precio(niveles['ideal'])})")
    if maximo_52s is not None:
        condiciones.append(f"Rompe el máximo de 52 semanas ({_fmt_precio(maximo_52s)})")
    if fecha_resultados:
        condiciones.append(f"Después de los resultados trimestrales ({fecha_resultados})")
    if not condiciones:
        return []
    lineas = ["🔔 Comprar si ocurre UNA de estas cosas", ""]
    lineas += [f"✅ {c}" for c in condiciones]
    if niveles.get("cancelar") is not None:
        lineas += ["", f"❌ Cancelar la idea si cae debajo de {_fmt_precio(niveles['cancelar'])}"]
    return lineas


def _seccion_noticias_resumen(ticker: str, nombre: str | None, entrada: dict | None) -> list[str]:
    """El resumen de noticias que se muestra por defecto -- una frase de
    tono general + hasta 3 hechos concretos (news_analyst.explicador.
    resumir_noticias), no una lista de titulares sueltos. Si el LLM no
    está disponible o su respuesta no pasa el filtro de seguridad, se
    degrada mostrando los titulares crudos en vez de dejar la sección
    vacía en silencio."""
    query = f"{nombre or ticker} stock"
    titulares = titulares_google_news(query, maximo=MAX_TITULARES_REPORTE)
    lineas = ["📰 Lo que pasó esta semana", ""]
    if not titulares:
        lineas.append("No encontré noticias recientes.")
        return lineas

    contexto = None
    if entrada:
        contexto = EntradaShortlist(
            ticker=ticker, posicion=entrada["posicion"], score=entrada["score"],
            sector=entrada.get("sector"), nombre=nombre, industria=entrada.get("industria"),
            sub_scores=entrada.get("sub_scores", {}))

    resumen = resumir_noticias(titulares, contexto)
    if resumen:
        lineas.append(resumen.resumen)
        if resumen.puntos:
            lineas.append("")
            lineas += [f"• {p}" for p in resumen.puntos]
    else:
        lineas.append("No pude generar un resumen (requiere ANTHROPIC_API_KEY, o la "
                      "respuesta no pasó el filtro de seguridad). Titulares recientes:")
        lineas += [f'• "{t}"' for t in titulares[:3]]
    return lineas


# ------------------------- secciones del modo --full -------------------------

def _resumen_ejecutivo(
    entrada: dict | None, pe: float | None, tendencia_label: str, riesgos: list[str],
) -> list[str]:
    calidad_score = (entrada or {}).get("sub_scores", {}).get("calidad")
    valor_score = (entrada or {}).get("sub_scores", {}).get("valor")
    valoracion = _clasificar_valoracion(pe, valor_score)
    detalle_pe = f" (P/E {pe:.1f})" if pe is not None else ""

    lineas = ["📋 Executive Summary"]
    if calidad_score is not None:
        lineas.append(f"  Calidad del negocio: {_estrellas_100(calidad_score)}")
    lineas.append(f"  Tendencia: {tendencia_label.capitalize()}")
    lineas.append(f"  Valoración: {valoracion}{detalle_pe}")
    lineas.append(f"  Riesgo principal: {riesgos[0]}")
    lineas.append(
        f"  Conclusión: Negocio de calidad {_texto_calidad(calidad_score)}, tendencia técnica "
        f"{tendencia_label}, valoración {valoracion.lower()}. {riesgos[0]}")
    return lineas


def _score_breakdown(entrada: dict) -> list[str]:
    """Desglosa el score exactamente con la misma fórmula de
    screener/scoring.puntuar() (suma ponderada / peso disponible) -- no es
    una aproximación, es la cuenta real hecha transparente."""
    sub = entrada.get("sub_scores", {})
    lineas = [f"Score breakdown (cómo se arma el {entrada['score']:.0f}/100):"]
    for factor, peso in CONFIG.pesos.items():
        nombre = _NOMBRE_FACTOR.get(factor, factor)
        s = sub.get(factor)
        if s is None:
            lineas.append(f"  {nombre} (peso {peso:.0%}): No disponible ese día -- "
                          f"se excluyó del total (no se penaliza con 0)")
            continue
        contrib = s * peso
        lineas.append(f"  {nombre} (peso {peso:.0%}): {s:.0f}/100 -> {contrib:.1f}/{peso * 100:.0f} pts")
    lineas.append(f"  TOTAL: {entrada['score']:.0f}/100")
    return lineas


def _seccion_shortlist(entrada: dict | None) -> list[str]:
    if not entrada:
        return [
            "No está en la shortlist de hoy (no pasó todos los filtros "
            "cuantitativos del screener) -- lo de abajo es informativo, "
            "sin score comparativo contra el resto del mercado.",
        ]
    p = Puntuacion(ticker=entrada["ticker"], score_total=entrada["score"],
                   sub=entrada.get("sub_scores", {}), sector=entrada.get("sector"))
    lineas = [
        f"Posición #{entrada['posicion']} de la shortlist de hoy "
        f"({entrada['score']:.0f}/100, sobre {entrada.get('universo_n', '?')} "
        f"empresas analizadas).",
    ]
    r = razones(p, 3)
    if r:
        lineas.append("¿Por qué pasó el screener?")
        lineas += [f"  • {frase}" for frase in r]
    return lineas


def _seccion_tecnico(barras: Barras) -> list[str]:
    tendencia = tech.score_tendencia(barras)
    vol = tech.volatilidad_anual(barras)
    prox = tech.proximidad_maximo_52s(barras)
    rsi = tech.rsi(barras)
    lineas = ["Técnico:", f"  Precio actual: ${barras.close[-1]:.2f}"]
    lineas.append(f"  Tendencia (medias móviles): {tendencia:.0f}/3 condiciones alcistas"
                  if tendencia is not None else "  Tendencia: No disponible (poca historia)")
    lineas.append(f"  Volatilidad histórica anual: {_fmt_pct(vol)}")
    lineas.append(f"  A qué % del máximo de 52 semanas: {prox:.1%}" if prox is not None
                  else "  Máximo de 52 semanas: No disponible")
    lineas.append(f"  RSI (14): {rsi:.0f}" if rsi is not None else "  RSI: No disponible")
    lineas.append(f"  Interpretación RSI: {_interpretar_rsi(rsi)}")
    return lineas


def _seccion_fundamentales(f: Fundamentales) -> list[str]:
    return [
        "Fundamentales:",
        f"  P/E: {f.pe:.1f}" if f.pe else "  P/E: No disponible",
        f"    Interpretación: {_interpretar_pe(f.pe)}",
        f"  P/B: {f.pb:.1f}" if f.pb else "  P/B: No disponible",
        f"    Interpretación: {_interpretar_pb(f.pb)}",
        f"  ROE: {_fmt_pct(f.roe)}",
        f"    Interpretación: {_interpretar_roe(f.roe)}",
        f"  Margen operativo: {_fmt_pct(f.margen_operativo)}",
        f"  Crecimiento de ingresos (interanual): {_fmt_pct(f.crecimiento_ingresos)}",
    ]


def _seccion_analistas_y_tenencia(f: Fundamentales, precio_actual: float | None) -> list[str]:
    lineas = [
        "Consenso de analistas y tenencia (snapshot de hoy, no histórico):",
        f"  Recomendación: {f.analista_recomendacion or 'No disponible'}",
    ]
    if f.analista_precio_objetivo:
        lineas.append(f"  Precio objetivo promedio: ${f.analista_precio_objetivo:.2f} "
                      f"({f.analista_num_opiniones or '?'} analistas)")
        lineas.append(f"    Interpretación: {_interpretar_precio_objetivo(f.analista_precio_objetivo, precio_actual)}")
    else:
        lineas.append("  Precio objetivo: No disponible")
    lineas.append(f"  % en manos institucionales: {_fmt_pct(f.pct_institucional)}")
    lineas.append(f"  % en manos de insiders: {_fmt_pct(f.pct_insiders)}")
    return lineas


def _bloque_noticias(titulo: str, items: list[tuple[str, Explicacion]]) -> list[str]:
    if not items:
        return []
    out = [f"  {titulo}:"]
    for titular, explicacion in items:
        out.append(f'    📰 "{titular}"')
        out.append(f"       {explicacion.texto}")
    return out


def _seccion_noticias(ticker: str, nombre: str | None, entrada: dict | None) -> list[str]:
    """Vista exhaustiva de noticias (modo --full): cada titular clasificado
    por relevancia, con su explicación individual -- a diferencia de
    _seccion_noticias_resumen(), que es la vista condensada del modo
    simple."""
    query = f"{nombre or ticker} stock"
    titulares = titulares_google_news(query, maximo=MAX_TITULARES_REPORTE)
    if not titulares:
        return ["Noticias: no se encontraron titulares recientes."]

    lineas = ["Noticias recientes:"]
    if not entrada:
        # Sin contexto de shortlist no hay con qué fundamentar una
        # explicación honesta del LLM -- se listan los titulares crudos
        # en vez de forzar una conexión que no existe.
        lineas += [f'  📰 "{t}"' for t in titulares]
        return lineas

    contexto = EntradaShortlist(
        ticker=ticker, posicion=entrada["posicion"], score=entrada["score"],
        sector=entrada.get("sector"), nombre=nombre, industria=entrada.get("industria"),
        sub_scores=entrada.get("sub_scores", {}),
    )
    altas, medias, bajas, sin_explicar = [], [], [], []
    for titular in titulares:
        explicacion = generar_explicacion(titular, contexto)
        if explicacion is None:
            sin_explicar.append(titular)
        elif explicacion.nivel_importancia >= 4:
            altas.append((titular, explicacion))
        elif explicacion.nivel_importancia >= 2:
            medias.append((titular, explicacion))
        else:
            bajas.append((titular, explicacion))

    lineas += _bloque_noticias("Alta relevancia", altas)
    lineas += _bloque_noticias("Media relevancia", medias)
    lineas += _bloque_noticias("Baja relevancia", bajas)
    if sin_explicar:
        lineas.append("  Estado: ⚠️ Las noticias se encontraron correctamente, pero aún no "
                      "se pudieron clasificar por impacto. Mostrando solo titulares.")
        lineas += [f'    📰 "{t}"' for t in sin_explicar]
    return lineas


def _seccion_full(
    ticker: str, nombre: str | None, entrada: dict | None, fund: Fundamentales, barras: Barras,
    precio_actual: float, tendencia_label: str, riesgos: list[str], fecha_resultados: str | None,
) -> list[str]:
    """Todo lo que el modo simple omite a propósito -- se llama solo con
    /report TICKER --full. Aquí "No disponible" sigue siendo honesto: esta
    vista existe para mostrar TODO lo que se intentó obtener, no para
    decidir rápido."""
    lineas = [SEP, "", "Si quieres profundizar", "", "📋 Todos los fundamentales", ""]
    lineas += _resumen_ejecutivo(entrada, fund.pe, tendencia_label, riesgos)
    lineas.append("")
    lineas += _seccion_shortlist(entrada)
    if entrada:
        lineas.append("")
        lineas += _score_breakdown(entrada)
    lineas.append("")
    lineas += _seccion_tecnico(barras)
    lineas.append("")
    lineas += _seccion_fundamentales(fund)
    lineas.append("")
    lineas += _seccion_analistas_y_tenencia(fund, precio_actual)
    lineas.append("")
    lineas.append("Riesgos principales:")
    lineas += [f"  • {r}" for r in riesgos]
    lineas.append("")
    lineas.append("Catalizadores:")
    lineas += [f"  • {c}" for c in _catalizadores(fecha_resultados)]
    lineas.append("")
    lineas += _seccion_noticias(ticker, nombre, entrada)
    return lineas


# ------------------------- punto de entrada -------------------------

def generar_reporte(ticker: str, modo: str = "simple") -> str:
    """Punto de entrada de /report TICKER [--full]. Nunca lanza: cualquier
    fallo se convierte en un mensaje de error legible para el usuario."""
    ticker = ticker.upper().strip()
    provider = YahooProvider()
    barras_por_ticker = provider.barras([ticker], dias=400)
    if ticker not in barras_por_ticker:
        return (f"No pude obtener datos de precio para {ticker} -- revisa que "
                f"el ticker esté bien escrito, o Yahoo bloqueó la consulta. "
                f"Intenta de nuevo en un momento.")
    barras = barras_por_ticker[ticker]
    precio_actual = barras.close[-1]

    fund = provider.fundamentales([ticker]).get(ticker, Fundamentales(ticker))
    entrada = _shortlist_entry(ticker)
    nombre = fund.nombre or ((entrada or {}).get("nombre"))

    tendencia_label = clasificar_tendencia(tech.score_tendencia(barras))
    rsi = tech.rsi(barras)
    vol = tech.volatilidad_anual(barras)
    sma50 = tech.sma(barras.close, 50)
    maximo_52s = tech.maximo_52s(barras)
    atr_val = tech.atr(barras)
    niveles = tech.niveles_precio(precio_actual, atr_val, sma50)

    calidad_score = (entrada or {}).get("sub_scores", {}).get("calidad")
    valor_score = (entrada or {}).get("sub_scores", {}).get("valor")
    liquidez_score = (entrada or {}).get("sub_scores", {}).get("liquidez")
    valoracion = _clasificar_valoracion(fund.pe, valor_score)
    riesgos = _riesgos(rsi, fund.pe, vol, tendencia_label)
    fecha_resultados = _obtener_fecha_resultados(ticker)

    investigar_emoji, investigar_texto = _veredicto_investigar(calidad_score, entrada)
    timing_codigo, timing_texto = _timing(entrada, tendencia_label, rsi)
    gusta = _lo_que_me_gusta(tendencia_label, fund.roe, fund.crecimiento_ingresos, valoracion,
                             fund.pe, liquidez_score)
    no_gusta = _lo_que_no_me_gusta(timing_codigo, rsi, valoracion, fund.pe, vol)
    por_que = _por_que_una_linea(timing_codigo, gusta, no_gusta)

    lineas = [f"📊 {ticker} — {nombre or ticker}", "", SEP, ""]
    lineas += _mi_plan_para_hoy(timing_codigo, por_que, niveles, maximo_52s)

    lineas += ["", SEP, ""]
    lineas += _en_20_segundos(investigar_emoji, investigar_texto, timing_codigo, timing_texto,
                              precio_actual, niveles, fund.analista_precio_objetivo)

    lineas += ["", SEP, "", "🎯 ¿Por qué me gusta?", ""]
    lineas += [f"✅ {g}" for g in gusta] if gusta else \
        ["Sin fortalezas destacadas detectadas hoy con los datos disponibles."]

    lineas += ["", SEP, "", "⚠️ ¿Qué no me gusta?", ""]
    lineas += [f"⚠️ {d}" for d in no_gusta] if no_gusta else \
        ["Sin debilidades destacadas detectadas hoy con los datos disponibles."]

    lineas += ["", SEP, ""]
    lineas += _lo_importante(precio_actual, fund.pe, fund.roe, fund.crecimiento_ingresos, rsi,
                             fund.analista_recomendacion, fund.analista_precio_objetivo)

    lineas += ["", SEP, ""]
    lineas += _seccion_noticias_resumen(ticker, nombre, entrada)

    lineas += ["", SEP, ""]
    lineas += _que_haria_yo(timing_codigo, por_que, niveles, maximo_52s)

    alertas = _alertas_reporte(niveles, maximo_52s, fecha_resultados)
    if alertas:
        lineas += ["", SEP, ""]
        lineas += alertas

    if modo == "full":
        lineas.append("")
        lineas += _seccion_full(ticker, nombre, entrada, fund, barras, precio_actual,
                                tendencia_label, riesgos, fecha_resultados)
    else:
        lineas += ["", SEP, "",
                  f"Para ver todos los fundamentales, score breakdown y consenso de "
                  f"analistas detallado: /report {ticker} --full"]

    lineas += ["", SEP, "", DISCLAIMER]
    return "\n".join(lineas)


def generar_lista_completa() -> str:
    """Punto de entrada de /list: la shortlist de hoy completa (el mensaje
    corto diario solo muestra el Top N; esto es "el resto"). Nunca lanza:
    cualquier fallo se convierte en un mensaje legible."""
    if not SHORTLIST_PATH.exists():
        return "Todavía no hay una shortlist de hoy -- el screener no ha corrido."
    try:
        data = json.loads(SHORTLIST_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return "No pude leer la shortlist de hoy."
    filas = data.get("shortlist", [])
    if not filas:
        return "La shortlist de hoy está vacía."
    ranking = [
        Puntuacion(ticker=f["ticker"], score_total=f["score"], sub=f.get("sub_scores", {}),
                   sector=f.get("sector"), nombre=f.get("nombre"), industria=f.get("industria"))
        for f in filas
    ]
    return texto_telegram_lista_completa(ranking, data.get("universo_escaneado", len(filas)))
