"""Genera el memo de /report TICKER bajo demanda.

Esta es la investigación profunda que el mensaje diario del screener YA NO
manda automáticamente (ver screener/report.texto_telegram_corto): se pide
explícitamente, para un ticker a la vez. Reutiliza los mismos módulos
reales que ya existen (screener, news_analyst) -- nunca corre sobre el
universo completo, así que es rápido y barato de correr on-demand.

Honestidad de los datos: solo se muestra lo que de verdad se pudo obtener.
yfinance da un snapshot real de consenso de analistas y % de tenencia
institucional/insider (no el detalle de transacciones ni el histórico
13F -- eso no está disponible gratis, así que no se inventa). El Executive
Summary y los Riesgos/Catalizadores son texto DETERMINÍSTICO armado con
plantillas sobre números reales -- ningún LLM interviene en esta parte
(no hace falta: son solo reglas fijas sobre datos ya calculados). El LLM
solo se usa para explicar noticias (news_analyst.explicador), con los
mismos guardrails de siempre. Ver telegram_bot/README.md para el detalle
de qué es real y qué quedó fuera.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from news_analyst.explicador import generar_explicacion  # noqa: E402
from news_analyst.models import EntradaShortlist, Explicacion  # noqa: E402
from screener.config import CONFIG  # noqa: E402
from screener.data.provider import Barras, Fundamentales, YahooProvider  # noqa: E402
from screener.factors import technical as tech  # noqa: E402
from screener.options_ideas import clasificar_tendencia, proxima_fecha_resultados  # noqa: E402
from screener.report import razones  # noqa: E402
from screener.scoring import Puntuacion  # noqa: E402
from wizards_bot import titulares_google_news  # noqa: E402

log = logging.getLogger("telegram_bot.report_command")

SHORTLIST_PATH = _REPO_ROOT / "screener" / "shortlist_hoy.json"
MAX_TITULARES_REPORTE = 5

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


def _resumen_ejecutivo(
    entrada: dict | None, pe: float | None, tendencia_label: str, riesgos: list[str],
) -> list[str]:
    calidad_score = (entrada or {}).get("sub_scores", {}).get("calidad")
    valor_score = (entrada or {}).get("sub_scores", {}).get("valor")
    valoracion = _clasificar_valoracion(pe, valor_score)
    detalle_pe = f" (P/E {pe:.1f})" if pe is not None else ""

    conclusion = (
        f"Negocio de calidad {_texto_calidad(calidad_score)}, tendencia técnica "
        f"{tendencia_label}, valoración {valoracion.lower()}. {riesgos[0]}"
    )
    return [
        "📋 Executive Summary",
        f"  Calidad del negocio: {_estrellas_100(calidad_score)}",
        f"  Tendencia: {tendencia_label.capitalize()}",
        f"  Valoración: {valoracion}{detalle_pe}",
        f"  Riesgo principal: {riesgos[0]}",
        f"  Conclusión: {conclusion}",
    ]


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


# ------------------------- veredicto (deterministas) -------------------------

def _fortalezas(
    tendencia_label: str, calidad_score: float | None, liquidez_score: float | None,
    crecimiento: float | None,
) -> list[str]:
    items = []
    if tendencia_label == "alcista":
        items.append("Tendencia alcista muy sólida.")
    if calidad_score is not None and calidad_score >= 70:
        items.append("Negocio de buena calidad.")
    if liquidez_score is not None and liquidez_score >= 70:
        items.append("Alta liquidez.")
    if crecimiento is not None and crecimiento >= 0.10:
        items.append("Crecimiento de ingresos saludable.")
    return items or ["Sin fortalezas destacadas detectadas hoy con los datos disponibles."]


def _debilidades(
    valoracion: str, rsi: float | None, vol: float | None, tendencia_label: str,
) -> list[str]:
    items = []
    if valoracion == "Exigente":
        items.append("Valuación elevada.")
    if rsi is not None and rsi >= 70:
        items.append("RSI en zona de sobrecompra.")
    if rsi is not None and rsi <= 30:
        items.append("RSI en zona de sobreventa.")
    if vol is not None and vol >= 0.40:
        items.append("Volatilidad histórica alta.")
    if tendencia_label == "bajista":
        items.append("Tendencia técnica bajista.")
    return items or ["Sin debilidades destacadas detectadas hoy con los datos disponibles."]


def _preguntas_pendientes(fecha_resultados: str | None, valoracion: str) -> list[str]:
    """Preguntas genuinamente abiertas -- si ya tenemos el dato (ej. la
    fecha de resultados), no se pregunta lo que ya se respondió."""
    preguntas = []
    if not fecha_resultados:
        preguntas.append("¿Cuándo son los próximos earnings?")
    if valoracion == "Exigente":
        preguntas.append("¿La valuación está justificada por el crecimiento esperado?")
    preguntas.append("¿Los analistas están revisando al alza o a la baja sus estimaciones?")
    return preguntas


def _confianza_reporte(
    entrada: dict | None, fund: Fundamentales, fecha_resultados: str | None, hubo_noticias_explicadas: bool,
) -> int:
    """Cobertura real de datos: de todo lo que este reporte INTENTA
    obtener, ¿cuánto sí llegó? No es un juicio de calidad del análisis --
    es una medida honesta de cuántos huecos de 'No disponible' tiene este
    reporte en particular."""
    checks = [
        entrada is not None,
        fund.pe is not None,
        fund.roe is not None,
        fund.analista_recomendacion is not None,
        fund.pct_institucional is not None,
        fecha_resultados is not None,
        hubo_noticias_explicadas,
    ]
    return round(100 * sum(checks) / len(checks))


def _veredicto(
    entrada: dict | None, fund: Fundamentales, tendencia_label: str, rsi: float | None,
    vol: float | None, valoracion: str, fecha_resultados: str | None, hubo_noticias_explicadas: bool,
) -> list[str]:
    calidad_score = (entrada or {}).get("sub_scores", {}).get("calidad")
    liquidez_score = (entrada or {}).get("sub_scores", {}).get("liquidez")
    fortalezas = _fortalezas(tendencia_label, calidad_score, liquidez_score, fund.crecimiento_ingresos)
    debilidades = _debilidades(valoracion, rsi, vol, tendencia_label)
    preguntas = _preguntas_pendientes(fecha_resultados, valoracion)
    confianza = _confianza_reporte(entrada, fund, fecha_resultados, hubo_noticias_explicadas)

    lineas = ["🎯 Veredicto", "", "Fortalezas:"]
    lineas += [f"✅ {f}" for f in fortalezas]
    lineas.append("")
    lineas.append("Debilidades:")
    lineas += [f"⚠️ {d}" for d in debilidades]
    lineas.append("")
    lineas.append("Preguntas que aún debo responder antes de invertir:")
    lineas += [f"☐ {p}" for p in preguntas]
    lineas.append("")
    lineas.append(f"Nivel de confianza del reporte: {confianza}% (cuántos de los datos que "
                  f"se intentaron obtener sí estuvieron disponibles hoy)")
    return lineas


# ------------------------- secciones -------------------------

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


def _seccion_noticias(ticker: str, nombre: str | None, entrada: dict | None) -> tuple[list[str], bool]:
    """Devuelve (líneas, hubo_al_menos_una_explicada) -- lo segundo
    alimenta la confianza del reporte en _veredicto()."""
    query = f"{nombre or ticker} stock"
    titulares = titulares_google_news(query, maximo=MAX_TITULARES_REPORTE)
    if not titulares:
        return ["Noticias: no se encontraron titulares recientes."], False

    lineas = ["Noticias recientes:"]
    if not entrada:
        # Sin contexto de shortlist no hay con qué fundamentar una
        # explicación honesta del LLM -- se listan los titulares crudos
        # en vez de forzar una conexión que no existe.
        lineas += [f'  📰 "{t}"' for t in titulares]
        return lineas, False

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
    return lineas, bool(altas or medias or bajas)


def generar_reporte(ticker: str) -> str:
    """Punto de entrada de /report TICKER. Nunca lanza: cualquier fallo se
    convierte en un mensaje de error legible para el usuario."""
    ticker = ticker.upper().strip()
    provider = YahooProvider()
    barras_por_ticker = provider.barras([ticker], dias=400)
    if ticker not in barras_por_ticker:
        return (f"No pude obtener datos de precio para {ticker} -- revisa que "
                f"el ticker esté bien escrito, o Yahoo bloqueó la consulta. "
                f"Intenta de nuevo en un momento.")
    barras = barras_por_ticker[ticker]

    fund = provider.fundamentales([ticker]).get(ticker, Fundamentales(ticker))
    entrada = _shortlist_entry(ticker)
    nombre = fund.nombre or ((entrada or {}).get("nombre"))

    tendencia_label = clasificar_tendencia(tech.score_tendencia(barras))
    rsi = tech.rsi(barras)
    vol = tech.volatilidad_anual(barras)
    precio_actual = barras.close[-1]
    riesgos = _riesgos(rsi, fund.pe, vol, tendencia_label)
    fecha_resultados = _obtener_fecha_resultados(ticker)
    valoracion = _clasificar_valoracion(fund.pe, (entrada or {}).get("sub_scores", {}).get("valor"))
    lineas_noticias, hubo_noticias_explicadas = _seccion_noticias(ticker, nombre, entrada)

    lineas = [f"📊 Reporte: {nombre or ticker} ({ticker})", ""]
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
    lineas += lineas_noticias
    lineas.append("")
    lineas += _veredicto(entrada, fund, tendencia_label, rsi, vol, valoracion, fecha_resultados, hubo_noticias_explicadas)
    lineas.append("")
    lineas.append(DISCLAIMER)
    return "\n".join(lineas)
