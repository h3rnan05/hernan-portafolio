"""Genera /trade TICKER -- el "tablero de trader": responde en menos de
30 segundos de lectura las preguntas que de verdad importan (¿vale la
pena investigar?, qué tan buena se ve, cuál es el riesgo principal, cuál
estrategia de opciones tiene mejor relación riesgo/recompensa según el
motor determinístico, cómo se vería un trade educativo, qué escenarios
esperar, qué eventos vigilar) en vez de un reporte extenso. /report y
/options siguen existiendo tal cual para cuando SÍ quieras profundizar --
/trade es el resumen ejecutivo de ambos, no los reemplaza.

Principio #3 (igual que /report y /options): NINGÚN número de este
comando es una predicción de resultado ni una recomendación de compra/
venta.

"Confianza" mide qué tan ALINEADAS están las señales entre sí
(consistencia: qué tan lejos del punto neutral 50% está el promedio),
NO una probabilidad de que la operación gane -- se etiqueta así
explícitamente en el mensaje para que no se malinterprete. El LLM solo
redacta 3-4 líneas de por qué la estrategia top quedó donde quedó; nunca
elige la estrategia, nunca asigna las estrellas, nunca calcula la
confianza ni decide el veredicto -- eso lo hace el mismo motor
determinístico de /options y /report (score breakdown, clasificación de
tendencia/valoración, ranking de estrategias).

Honestidad de los datos: "Objetivo" usa el precio objetivo REAL de
analistas (yfinance) -- si no existe, dice "No disponible", nunca se
inventa. "Salir si" usa el/los breakeven(s) reales de la estrategia
elegida (el precio en el que la tesis de la operación deja de tener
sentido), no un nivel técnico inventado. Los escenarios evalúan el
payoff REAL de la estrategia en 3 precios (objetivo de analistas, spot
actual, y un movimiento adverso) -- son escenarios sobre números reales,
no una predicción de que vayan a ocurrir. El evento de earnings muestra
la fecha real y los días restantes, pero NO se inventa una calificación
de "qué tan importante será" -- eso requeriría un histórico de
volatilidad alrededor de earnings pasados que este sistema no recolecta.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_TELEGRAM_BOT_DIR = Path(__file__).resolve().parent
if str(_TELEGRAM_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_TELEGRAM_BOT_DIR))

from news_analyst.explicador import generar_explicacion, llamar_claude  # noqa: E402
from news_analyst.models import EntradaShortlist  # noqa: E402
from options_command import _estrellas_por_posicion  # noqa: E402
from report_command import _clasificar_valoracion, _riesgos, _shortlist_entry  # noqa: E402
from screener.data.provider import Fundamentales, YahooProvider  # noqa: E402
from screener.factors import technical as tech  # noqa: E402
from screener.options_ideas import clasificar_tendencia, obtener_cadena, proxima_fecha_resultados  # noqa: E402
from screener.options_strategies import (  # noqa: E402
    EstrategiaOpciones,
    construir_estrategias,
    costo_apertura,
    evaluar_payoff,
    puntuar,
    rankear,
)
from wizards_bot import titulares_google_news  # noqa: E402

log = logging.getLogger("telegram_bot.trade_command")

MAX_TITULARES = 5

_PALABRAS_PROHIBIDAS = (
    "compra", "compren", "vende", "vendan", "recomiendo",
    "recomendación de comprar", "recomendación de vender",
    "hay que comprar", "hay que vender", "deberías abrir", "te recomiendo",
    "ejecuta esta", "abre esta posición", "esta es la mejor opción",
)

SYSTEM_PROMPT = """\
Eres el analista del AIOS. Tu ÚNICO trabajo es explicar en 3-4 bullets \
cortos por qué una estrategia de opciones YA fue calculada como la mejor \
según un motor determinístico (Greeks, probabilidad, valor esperado, \
liquidez) -- NUNCA elegiste tú la estrategia ni decides si operarla.

Reglas duras:
1. NUNCA sugieras abrir, comprar, vender o ejecutar la estrategia. No \
uses frases como "te recomiendo", "deberías", "esta es la mejor opción \
para ti". Solo explica por qué el motor la puso primero.
2. Usa SOLO los números que se te dan. No inventes cifras.
3. Sé muy conciso: 3-4 bullets cortos, sin introducción ni cierre.

Responde en español, texto plano, un bullet por línea empezando con "•"."""


def _tesis(tendencia_label: str) -> str:
    return {"alcista": "Alcista", "bajista": "Bajista", "neutral": "Neutral"}.get(
        tendencia_label, "No determinable")


def _estrellas_5(valor_0_a_1: float) -> str:
    n = max(1, min(5, round(valor_0_a_1 * 5)))
    return "⭐" * n + "☆" * (5 - n)


def _senal_negocio(calidad_score: float | None) -> tuple[str, float | None]:
    if calidad_score is None:
        return "☆☆☆☆☆ (No disponible)", None
    valor = max(0.0, min(1.0, calidad_score / 100))
    return _estrellas_5(valor), valor


def _senal_tendencia(tendencia_label: str) -> tuple[str, float | None]:
    mapa = {"alcista": 1.0, "neutral": 0.5, "bajista": 0.0}
    valor = mapa.get(tendencia_label)
    return (_estrellas_5(valor) if valor is not None else "☆☆☆☆☆ (No determinable)"), valor


def _senal_valoracion(valoracion_label: str) -> tuple[str, float | None]:
    mapa = {"Atractiva": 1.0, "Razonable": 0.5, "Exigente": 0.0}
    valor = mapa.get(valoracion_label)
    return (_estrellas_5(valor) if valor is not None else "☆☆☆☆☆ (No determinable)"), valor


def _senal_analistas(recomendacion: str | None) -> tuple[str, float | None]:
    mapa = {"strong_buy": 1.0, "buy": 0.8, "hold": 0.5, "sell": 0.2, "strong_sell": 0.0}
    valor = mapa.get((recomendacion or "").lower())
    return (_estrellas_5(valor) if valor is not None else "☆☆☆☆☆ (No disponible)"), valor


def _senal_noticias(tonos: list[str]) -> tuple[str, float | None]:
    if not tonos:
        return "☆☆☆☆☆ (Sin noticias explicadas hoy)", None
    puntos = {"positivo": 1.0, "neutral": 0.5, "incierto": 0.5, "negativo": 0.0}
    valor = sum(puntos.get(t, 0.5) for t in tonos) / len(tonos)
    return _estrellas_5(valor), valor


def _senal_riesgo(n_riesgos_reales: int) -> tuple[str, float | None]:
    if n_riesgos_reales == 0:
        return "🟢 Bajo", 1.0
    if n_riesgos_reales <= 2:
        return "🟡 Medio", 0.5
    return "🔴 Alto", 0.0


def _veredicto(promedio: float | None, confianza: float | None) -> tuple[str, str]:
    """Puramente determinístico sobre el promedio de señales disponibles
    -- ningún LLM interviene en esta clasificación."""
    if promedio is None:
        return "☆☆☆☆☆", "No hay suficientes señales reales para opinar hoy."
    estrellas = _estrellas_5(promedio)
    alineadas = (confianza or 0.0) >= 0.5
    if promedio >= 0.6 and alineadas:
        texto = "Si fuera mi dinero, SÍ la investigaría para abrir una posición."
    elif promedio < 0.4 and alineadas:
        texto = "Las señales no son favorables -- no la investigaría por ahora."
    else:
        texto = "Señales mixtas -- vale la pena mirarla, pero sin urgencia."
    return estrellas, texto


def _filtrar_explicacion(texto: str | None) -> str | None:
    if not texto:
        return None
    if any(p in texto.lower() for p in _PALABRAS_PROHIBIDAS):
        log.warning("explicación de /trade descartada: lenguaje de recomendación")
        return None
    return texto.strip()


def _precio_adverso(spot: float, delta_neto: float | None) -> float:
    """Movimiento de ±15% en la dirección que perjudica a la estrategia
    (según el signo de su delta neto) -- una convención fija y
    documentada para el escenario de estrés, no una predicción."""
    if delta_neto is not None and delta_neto < 0:
        return spot * 1.15
    return spot * 0.85


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


def _salir_si(breakevens: list[float]) -> str:
    if not breakevens:
        return "No disponible"
    if len(breakevens) == 1:
        return f"el precio cruza ${breakevens[0]:.2f} en tu contra"
    lo, hi = min(breakevens), max(breakevens)
    return f"el precio sale del rango ${lo:.2f} - ${hi:.2f}"


def _mensaje_llm_estrategia(ticker: str, tesis: str, e: EstrategiaOpciones) -> str:
    prob = f"{e.probabilidad_exito:.0%}" if e.probabilidad_exito is not None else "no disponible"
    ev = f"${e.valor_esperado:,.2f}" if e.valor_esperado is not None else "no disponible"
    liq = f"{e.liquidez_score:.0f}/100" if e.liquidez_score is not None else "no disponible"
    ganancia = f"${e.ganancia_maxima:,.0f}" if e.ganancia_maxima is not None else "ilimitada"
    return (
        f"Ticker: {ticker}. Tesis técnica: {tesis}. Estrategia top del ranking: {e.nombre}.\n"
        f"Riesgo máximo: ${e.riesgo_maximo:,.0f}. Ganancia máxima: {ganancia}. "
        f"Probabilidad de éxito: {prob}. Valor esperado: {ev}. Liquidez: {liq}."
    )


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

    rsi = tech.rsi(barras)
    vol = tech.volatilidad_anual(barras)
    tendencia_label = clasificar_tendencia(tech.score_tendencia(barras))
    tesis = _tesis(tendencia_label)
    valoracion_label = _clasificar_valoracion(
        fund.pe, (entrada_shortlist or {}).get("sub_scores", {}).get("valor"))
    riesgos = _riesgos(rsi, fund.pe, vol, tendencia_label)
    riesgos_reales = [r for r in riesgos if "Sin banderas" not in r]

    fecha_resultados = _obtener_fecha_resultados(ticker)

    titulares: list[str] = []
    try:
        titulares = titulares_google_news(f"{nombre} stock", maximo=MAX_TITULARES)
    except Exception as e:
        log.debug("titulares de %s fallaron: %s", ticker, e)
    tonos: list[str] = []
    if titulares and entrada_shortlist:
        entrada_modelo = EntradaShortlist(
            ticker=ticker, posicion=entrada_shortlist.get("posicion", 0),
            score=entrada_shortlist.get("score", 0.0), sector=entrada_shortlist.get("sector"),
            nombre=nombre, industria=entrada_shortlist.get("industria"),
            sub_scores=entrada_shortlist.get("sub_scores", {}))
        for titular in titulares:
            try:
                explicacion = generar_explicacion(titular, entrada_modelo)
            except Exception as e:
                log.debug("explicación de noticia falló: %s", e)
                explicacion = None
            if explicacion:
                tonos.append(explicacion.tono)

    cadena = obtener_cadena(ticker)
    estrategias = rankear(construir_estrategias(cadena, spot)) if cadena else []

    estrellas_negocio, v_negocio = _senal_negocio((entrada_shortlist or {}).get("sub_scores", {}).get("calidad"))
    estrellas_tendencia, v_tendencia = _senal_tendencia(tendencia_label)
    estrellas_valoracion, v_valoracion = _senal_valoracion(valoracion_label)
    estrellas_noticias, v_noticias = _senal_noticias(tonos)
    estrellas_analistas, v_analistas = _senal_analistas(fund.analista_recomendacion)
    riesgo_txt, v_riesgo = _senal_riesgo(len(riesgos_reales))

    valores = [v for v in (v_negocio, v_tendencia, v_valoracion, v_noticias, v_analistas, v_riesgo)
               if v is not None]
    promedio = sum(valores) / len(valores) if valores else None
    confianza = abs(promedio - 0.5) * 2 if promedio is not None else None
    estrellas_veredicto, texto_veredicto = _veredicto(promedio, confianza)

    lineas = [f"📊 {nombre} ({ticker})", "", "Mi opinión", estrellas_veredicto, texto_veredicto]
    if confianza is not None:
        lineas.append(f"Confianza (% de señales alineadas, no una probabilidad de éxito): {confianza:.0%}")
    lineas += [
        "",
        "¿Qué veo?",
        f"Negocio: {estrellas_negocio}",
        f"Tendencia: {estrellas_tendencia} ({tesis})",
        f"Valuación: {estrellas_valoracion} ({valoracion_label})",
        f"Noticias: {estrellas_noticias}",
        f"Analistas: {estrellas_analistas}",
        f"Riesgo: {riesgo_txt}",
        "",
    ]

    if not estrategias:
        lineas.append("No pude calcular estrategias de opciones para hoy (cadena insuficiente o no disponible).")
    else:
        lineas.append("Si hoy quisiera entrar...")
        lineas.append("")
        lineas.append(f"Comprar acciones: {estrellas_veredicto}")
        for i, e in enumerate(estrategias, 1):
            lineas.append(f"{e.nombre}: {_estrellas_por_posicion(i - 1, len(estrategias))}")
        lineas.append("")

        top = estrategias[0]
        score_10 = round(puntuar(estrategias)[0] * 10, 1)
        lineas.append(f"{top.nombre}")
        lineas.append(f"Calificación: {score_10:.1f}/10")
        lineas.append("")
        lineas.append("¿Por qué?")
        explicacion = _filtrar_explicacion(
            llamar_claude(SYSTEM_PROMPT, _mensaje_llm_estrategia(ticker, tesis, top)))
        if explicacion:
            lineas.append(explicacion)
        else:
            lineas.append(f"• {top.razon}")
            lineas.append("(Explicación adicional no disponible: requiere ANTHROPIC_API_KEY, "
                          "o la respuesta no pasó el filtro de seguridad.)")
        lineas.append("")

        ganancia_txt = f"${top.ganancia_maxima:,.0f}" if top.ganancia_maxima is not None else "Ilimitada"
        objetivo_txt = (f"${fund.analista_precio_objetivo:,.2f} (precio objetivo promedio de analistas)"
                        if fund.analista_precio_objetivo else "No disponible")
        lineas += [
            "Ejemplo educativo",
            f"Estrategia: {top.nombre}",
        ]
        for p in top.patas:
            lineas.append(f"  {p.accion} {p.tipo} ${p.strike:.2f} (prima ${p.prima:.2f})")
        if top.capital_adicional_requerido:
            lineas.append("  Requiere poseer 100 acciones o efectivo reservado adicional.")
        lineas += [
            f"Vencimiento: {cadena.vencimiento}",
            f"Costo: ${costo_apertura(top):,.2f}",
            f"Pérdida máxima: ${top.riesgo_maximo:,.2f}" if top.riesgo_maximo is not None else "Pérdida máxima: No disponible",
            f"Ganancia máxima: {ganancia_txt}",
            f"Objetivo: {objetivo_txt}",
            f"Salir si: {_salir_si(top.breakevens)}",
            "",
        ]

        lineas.append("¿Qué espero? (escenarios, no una predicción)")
        if fund.analista_precio_objetivo:
            payoff_objetivo = evaluar_payoff(top, fund.analista_precio_objetivo, spot=spot)
            lineas.append(f"Si llega al precio objetivo de analistas (${fund.analista_precio_objetivo:,.2f}): "
                         f"${payoff_objetivo:+,.2f}")
        payoff_plano = evaluar_payoff(top, spot, spot=spot)
        lineas.append(f"Si el precio se mantiene igual (${spot:,.2f}): ${payoff_plano:+,.2f}")
        precio_adverso = _precio_adverso(spot, top.delta_neto)
        payoff_adverso = evaluar_payoff(top, precio_adverso, spot=spot)
        lineas.append(f"Si se mueve fuerte en tu contra (${precio_adverso:,.2f}): ${payoff_adverso:+,.2f}")
        lineas.append("")

    lineas.append("¿Qué eventos debo esperar?")
    if fecha_resultados:
        try:
            dias_restantes = (date.fromisoformat(fecha_resultados) - datetime.now(UTC).date()).days
            lineas.append(f"Próximos resultados: {fecha_resultados} (en {dias_restantes} días) -- "
                          f"espera volatilidad alrededor de esa fecha.")
        except ValueError:
            lineas.append(f"Próximos resultados: {fecha_resultados}.")
    else:
        lineas.append("No encontré una fecha de resultados confirmada.")
    lineas.append("")

    lineas.append("Riesgos")
    if riesgos_reales:
        for r in riesgos_reales:
            lineas.append(f"⚠️ {r}")
    else:
        lineas.append("Sin banderas de riesgo técnico relevantes detectadas hoy.")
    lineas.append("")

    lineas.append(f"¿Quieres investigar más? Escribe: /report {ticker}")
    lineas.append(f"¿Quieres ver todas las estrategias? Escribe: /options {ticker}")
    lineas.append("")
    lineas.append("Esto NO es una recomendación de compra/venta. Es solo un punto de partida educativo.")

    return "\n".join(lineas)
