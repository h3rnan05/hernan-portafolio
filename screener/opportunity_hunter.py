"""Opportunity Hunter -- el screener deja de ser "reportar qué pasó el
filtro" y pasa a "cazar oportunidades excepcionales". Corre inmediatamente
después del screener diario (`screener/run.py`), reutilizando los mismos
datos ya calculados ese día (barras, sub_scores cross-sectional del
ranking completo, fundamentales, opciones) -- ningún cálculo nuevo salvo
la detección de patrones en sí.

Filosofía (pedido explícito, 2026-07-23): "Quiero que elimines el 99% del
ruido... una oportunidad solo debe enviarse cuando múltiples factores
independientes estén alineados... Prefiero recibir 2 oportunidades
excelentes por semana que 20 mediocres por día." Por eso cada patrón
exige 3-4 condiciones INDEPENDIENTES a la vez -- nunca un score alto
solo -- y si nada dispara, la respuesta del día es explícitamente "no
encontré ninguna oportunidad", un no-resultado válido y esperado (mismo
espíritu que el Decision Engine del ROADMAP: "no trades today" es una
salida legítima, nunca se fuerza una).

100% determinístico, sin LLM: cada patrón es una combinación fija de
umbrales sobre datos ya reales -- los mismos percentiles cross-sectional
0-100 que ya calcula `scoring.puntuar()` (momentum/tendencia/calidad/
valor/liquidez), y ATR/SMA/RSI/máximo de 52 semanas/volumen real de
`screener.factors.technical` sobre las barras ya descargadas. "Convicción
del modelo" reusa esos mismos sub_scores -- nunca un número inventado.

Patrones de fase 1 -- los que son honestamente calculables HOY con datos
gratis de yfinance (deliberadamente NO incluidos todavía, anotados para
una fase 2: "earnings sorpresa + guía al alza" porque no hay historial
estructurado de guidance, y "volumen de opciones inusual" porque no hay
histórico de IV/volumen de opciones recolectado -- ver el docstring de
`screener/options_ideas.py` sobre por qué IV Rank tampoco existe todavía):

- **Ruptura confirmada**: precio en/cerca del máximo de 52 semanas +
  volumen inusual + tendencia alcista fuerte (medias alineadas) +
  valuación no exigente.
- **Pullback sano**: tendencia alcista fuerte + precio corrigiendo hacia
  la media de 50 días SIN romperla + RSI en zona saludable (ni
  sobrecomprado ni en pánico) + negocio de calidad o creciendo.
- **Infravalorada con impulso**: barata + calidad + momentum ya positivo
  -- los tres percentiles cross-sectional del día, sin depender de
  historial de días anteriores (evita el problema de que
  `shortlist_hoy.json` solo persiste el Top N, no el universo completo).

Cada ticker dispara COMO MÁXIMO un patrón (el primero que aplica, en
orden ruptura > pullback > valor_impulso) para no mandar dos alertas del
mismo ticker el mismo día.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from screener.data.provider import Barras, Fundamentales
from screener.factors import technical as tech
from screener.options_ideas import obtener_cadena, proxima_fecha_resultados
from screener.options_strategies import construir_estrategias, direccion_estrategia, rankear
from screener.scoring import Puntuacion

log = logging.getLogger("screener.opportunity_hunter")

SIN_OPORTUNIDADES = (
    "Hoy no encontré ninguna oportunidad que cumpla mis estándares. "
    "No abriría ninguna posición."
)

SEP = "━━━━━━━━━━━━━━━━━━"

# Umbrales de cada patrón -- fijos y documentados, nunca ajustados por
# ticker ni por un LLM. Cambiarlos es una decisión de producto explícita,
# no un parámetro que el modelo "aprende".
UMBRAL_RUPTURA_PROXIMIDAD = 0.98        # % del máximo de 52 semanas
UMBRAL_VOLUMEN_INUSUAL = 1.5            # x el promedio de 20 días
UMBRAL_PULLBACK_BANDA = 0.03            # ±3% de la media de 50 días
RSI_PULLBACK_MIN, RSI_PULLBACK_MAX = 40.0, 60.0
UMBRAL_VALOR_BARATA = 70.0              # percentil cross-sectional
UMBRAL_CALIDAD_SOLIDA = 60.0
UMBRAL_MOMENTUM_FUERTE = 70.0
UMBRAL_LIQUIDEZ_MINIMA = 20.0
DIAS_EARNINGS_EVITAR = 5

_URGENCIA = {"ruptura": "Alta", "pullback": "Media", "valor_impulso": "Baja"}


@dataclass(frozen=True)
class Oportunidad:
    ticker: str
    nombre: str | None
    patron: str                 # "ruptura" | "pullback" | "valor_impulso"
    conviccion: int             # 0-100, reusa los sub_scores del día
    urgencia: str                # "Alta" | "Media" | "Baja"
    decision: str                 # "Comprar hoy" | "Esperar" | "No operar"
    motivo_decision: str | None
    que_ocurrio: str
    que_invalida: str
    entrada: float
    stop: float | None
    objetivo: float | None
    horizonte_dias: int | None
    capital_acciones: float
    capital_estrategia: float | None
    estrategia_nombre: str | None


def _volumen_ratio(b: Barras, ventana: int = 20) -> float | None:
    """Volumen de la última barra vs. el promedio de las `ventana`
    anteriores -- de las mismas barras ya descargadas, ningún dato nuevo."""
    if len(b.volume) < ventana + 1:
        return None
    anteriores = b.volume[-ventana - 1:-1]
    promedio = sum(anteriores) / len(anteriores)
    return b.volume[-1] / promedio if promedio > 0 else None


def _detectar_ruptura(b: Barras) -> bool:
    prox = tech.proximidad_maximo_52s(b)
    vol_ratio = _volumen_ratio(b)
    tendencia = tech.score_tendencia(b)
    return (
        prox is not None and prox >= UMBRAL_RUPTURA_PROXIMIDAD
        and vol_ratio is not None and vol_ratio >= UMBRAL_VOLUMEN_INUSUAL
        and tendencia == 3.0
    )


def _detectar_pullback(b: Barras, sub: dict[str, float], fund: Fundamentales) -> bool:
    tendencia = tech.score_tendencia(b)
    sma50 = tech.sma(b.close, 50)
    rsi = tech.rsi(b)
    spot = b.close[-1] if b.close else None
    if tendencia != 3.0 or sma50 is None or rsi is None or spot is None or sma50 <= 0:
        return False
    dentro_de_banda = abs(spot - sma50) / sma50 <= UMBRAL_PULLBACK_BANDA
    rsi_sano = RSI_PULLBACK_MIN <= rsi <= RSI_PULLBACK_MAX
    calidad = sub.get("calidad")
    negocio_solido = (calidad is not None and calidad >= UMBRAL_CALIDAD_SOLIDA) or \
        (fund.crecimiento_ingresos is not None and fund.crecimiento_ingresos >= 0.10)
    return dentro_de_banda and rsi_sano and negocio_solido


def _detectar_valor_impulso(sub: dict[str, float]) -> bool:
    valor, calidad, momentum = sub.get("valor"), sub.get("calidad"), sub.get("momentum")
    return (
        valor is not None and valor >= UMBRAL_VALOR_BARATA
        and calidad is not None and calidad >= UMBRAL_CALIDAD_SOLIDA
        and momentum is not None and momentum >= UMBRAL_MOMENTUM_FUERTE
    )


def detectar_patron(b: Barras, sub: dict[str, float], fund: Fundamentales) -> str | None:
    """Único punto de entrada de detección -- como máximo un patrón por
    ticker, en orden de prioridad (ruptura > pullback > valor_impulso)."""
    if _detectar_ruptura(b):
        return "ruptura"
    if _detectar_pullback(b, sub, fund):
        return "pullback"
    if _detectar_valor_impulso(sub):
        return "valor_impulso"
    return None


def _conviccion(patron: str, sub: dict[str, float]) -> int:
    """Reusa los mismos sub_scores 0-100 cross-sectional que ya calcula
    scoring.puntuar() para ESTE patrón -- nunca un número inventado.
    Pondera los factores más relevantes para por qué este patrón en
    particular importa."""
    pesos = {
        "ruptura": (("momentum", 0.5), ("tendencia", 0.3), ("liquidez", 0.2)),
        "pullback": (("tendencia", 0.4), ("calidad", 0.3), ("valor", 0.3)),
        "valor_impulso": (("valor", 0.5), ("calidad", 0.3), ("momentum", 0.2)),
    }[patron]
    disponibles = [(sub.get(f), w) for f, w in pesos if sub.get(f) is not None]
    if not disponibles:
        return 50
    peso_total = sum(w for _, w in disponibles)
    return round(sum(v * w for v, w in disponibles) / peso_total)


def _que_ocurrio_y_invalida(patron: str, ticker: str, b: Barras, sub: dict[str, float]) -> tuple[str, str]:
    if patron == "ruptura":
        max52 = tech.maximo_52s(b)
        vol_ratio = _volumen_ratio(b)
        return (
            f"{ticker} rompe su máximo de 52 semanas (${max52:,.0f}) con un volumen "
            f"{vol_ratio:.1f}x el promedio de los últimos 20 días, dentro de una tendencia "
            f"alcista confirmada.",
            f"Si vuelve a cerrar debajo de ${max52:,.0f} en los próximos días, la ruptura se invalida.",
        )
    if patron == "pullback":
        sma50 = tech.sma(b.close, 50)
        rsi = tech.rsi(b)
        return (
            f"{ticker} corrige hacia su media de 50 días (${sma50:,.0f}) dentro de una "
            f"tendencia alcista fuerte, con RSI en zona saludable ({rsi:.0f}).",
            f"Si rompe con fuerza por debajo de ${sma50:,.0f}, o el RSI cae debajo de 30, "
            f"la corrección deja de ser sana.",
        )
    valor, calidad, momentum = sub.get("valor"), sub.get("calidad"), sub.get("momentum")
    return (
        f"{ticker} está entre las más baratas del universo analizado hoy (percentil valor "
        f"{valor:.0f}/100), con negocio de calidad (percentil {calidad:.0f}/100) y momentum "
        f"ya positivo (percentil {momentum:.0f}/100).",
        "Si el momentum se revierte o deja de estar entre las más baratas del universo, "
        "la tesis pierde base.",
    )


def _objetivo(spot: float, max52: float | None, cancelar: float | None) -> float | None:
    """El máximo de 52 semanas si todavía queda meaningfully arriba del
    spot (nivel técnico real); si no (ej. ya estamos ahí, como en una
    ruptura), una extensión de igual distancia que el riesgo real hasta
    el stop -- la misma convención de "measured move" que ya usa
    /trade -- nunca un número inventado."""
    if max52 is not None and max52 > spot * 1.02:
        return max52
    if cancelar is not None:
        riesgo = spot - cancelar
        if riesgo > 0:
            return spot + riesgo
    return None


def _decision(sub_liquidez: float | None, dias_a_resultados: int | None) -> tuple[str, str | None]:
    if sub_liquidez is not None and sub_liquidez < UMBRAL_LIQUIDEZ_MINIMA:
        return "No operar", "Liquidez muy baja -- dificulta entrar o salir a buen precio."
    if dias_a_resultados is not None and 0 <= dias_a_resultados <= DIAS_EARNINGS_EVITAR:
        return "Esperar", f"Resultados en {dias_a_resultados} días -- prefiero evitar la volatilidad de earnings."
    return "Comprar hoy", None


def _dias_a_resultados(ticker: str) -> int | None:
    try:
        import yfinance as yf
        fecha = proxima_fecha_resultados(yf.Ticker(ticker))
    except Exception as e:
        log.debug("fecha de resultados de %s falló: %s", ticker, e)
        return None
    if not fecha:
        return None
    try:
        return (date.fromisoformat(fecha) - date.today()).days
    except ValueError:
        return None


def _estrategia_recomendada(ticker: str, spot: float) -> tuple[str | None, float | None, int | None]:
    """Cadena de opciones SOLO para el ticker que ya disparó un patrón
    (nunca para el universo completo -- son caras y frágiles). Filtra a
    estrategias de dirección no-bajista (los 3 patrones son
    estructuralmente alcistas) y toma la mejor por el ranking real de
    options_strategies.rankear() -- nunca cambia ese ranking."""
    try:
        cadena = obtener_cadena(ticker)
    except Exception as e:
        log.debug("cadena de opciones de %s falló: %s", ticker, e)
        return None, None, None
    if cadena is None:
        return None, None, None
    estrategias = rankear(construir_estrategias(cadena, spot))
    alineadas = [e for e in estrategias if direccion_estrategia(e.nombre) != "bajista"]
    if not alineadas:
        return None, None, None
    top = alineadas[0]
    return top.nombre, top.riesgo_maximo, cadena.dias_a_vencimiento


def construir_oportunidad(p: Puntuacion, b: Barras, fund: Fundamentales, patron: str) -> Oportunidad:
    """Ensambla una Oportunidad ya detectada -- reutiliza los mismos
    niveles de precio (ATR/SMA50) que /trade y /report
    (`screener.factors.technical.niveles_precio`), nunca un cálculo
    paralelo."""
    spot = b.close[-1]
    sma50 = tech.sma(b.close, 50)
    atr_val = tech.atr(b)
    niveles = tech.niveles_precio(spot, atr_val, sma50)
    max52 = tech.maximo_52s(b)
    objetivo = _objetivo(spot, max52, niveles.get("cancelar"))
    que_ocurrio, que_invalida = _que_ocurrio_y_invalida(patron, p.ticker, b, p.sub)
    conviccion = _conviccion(patron, p.sub)
    dias_resultados = _dias_a_resultados(p.ticker)
    decision, motivo_decision = _decision(p.sub.get("liquidez"), dias_resultados)
    estrategia_nombre, capital_estrategia, horizonte_dias = _estrategia_recomendada(p.ticker, spot)

    return Oportunidad(
        ticker=p.ticker, nombre=p.nombre, patron=patron, conviccion=conviccion,
        urgencia=_URGENCIA[patron], decision=decision, motivo_decision=motivo_decision,
        que_ocurrio=que_ocurrio, que_invalida=que_invalida, entrada=spot,
        stop=niveles.get("cancelar"), objetivo=objetivo, horizonte_dias=horizonte_dias,
        capital_acciones=spot * 100, capital_estrategia=capital_estrategia,
        estrategia_nombre=estrategia_nombre,
    )


def buscar_oportunidades(
    ranking: list[Puntuacion], barras_por_ticker: dict[str, Barras], fund_por_ticker: dict[str, Fundamentales],
) -> list[Oportunidad]:
    """Punto de entrada del pipeline diario -- escanea el universo
    COMPLETO ya validado (no solo el Top N de la shortlist, que es la
    limitación real de shortlist_hoy.json: solo persiste el Top N).
    Nunca lanza por un ticker individual -- un fallo aislado no debe
    tumbar la corrida completa."""
    encontradas = []
    for p in ranking:
        b = barras_por_ticker.get(p.ticker)
        if b is None or not b.close:
            continue
        fund = fund_por_ticker.get(p.ticker, Fundamentales(p.ticker))
        try:
            patron = detectar_patron(b, p.sub, fund)
            if patron is None:
                continue
            encontradas.append(construir_oportunidad(p, b, fund, patron))
        except Exception as e:
            log.warning("detección de oportunidad falló para %s: %s", p.ticker, e)
    return encontradas


def _fmt(x: float) -> str:
    return f"${x:,.0f}"


def formatear_oportunidad(o: Oportunidad) -> str:
    lineas = [SEP, "", "🚨 Oportunidad detectada", "", o.ticker]
    if o.nombre:
        lineas.append(o.nombre)
    lineas += ["", f"Convicción del modelo: {o.conviccion}/100", "", f"Mi decisión: {o.decision}"]
    if o.motivo_decision:
        lineas.append(o.motivo_decision)

    lineas += ["", "¿Por qué?", "", o.que_ocurrio, o.que_invalida]

    lineas += ["", f"Entrada ideal: {_fmt(o.entrada)}"]
    if o.stop is not None:
        lineas.append(f"Stop: {_fmt(o.stop)}")
    if o.objetivo is not None:
        lineas.append(f"Objetivo: {_fmt(o.objetivo)}")
    if o.horizonte_dias is not None:
        lineas.append(f"Horizonte esperado: {o.horizonte_dias} días (vencimiento de la opción elegida)")

    lineas += ["", f"Capital mínimo recomendado: {_fmt(o.capital_acciones)} (comprar 100 acciones)"]
    if o.capital_estrategia is not None and o.estrategia_nombre:
        lineas.append(f"o {_fmt(o.capital_estrategia)} ({o.estrategia_nombre})")
        lineas.append(f"Estrategia recomendada: {o.estrategia_nombre}")
    else:
        lineas.append("Estrategia recomendada: Comprar acciones directamente (opciones no disponibles hoy)")

    niveles_alerta = sorted({round(x) for x in (o.entrada, o.stop, o.objetivo) if x is not None})
    if niveles_alerta:
        lineas += ["", f"Crear alertas en estos niveles: {', '.join(_fmt(x) for x in niveles_alerta)}"]

    lineas += ["", f"Nivel de urgencia: {o.urgencia}"]
    accion = {"Comprar hoy": "Comprar hoy", "Esperar": "Crear alertas y esperar confirmación",
             "No operar": "No abrir posición hoy"}[o.decision]
    lineas += ["", f"Acción siguiente: {accion}", "", SEP]
    return "\n".join(lineas)


def mensaje_oportunidades(oportunidades: list[Oportunidad]) -> str:
    """Texto final a mandar por Telegram -- el no-resultado
    (SIN_OPORTUNIDADES) es una salida tan válida como encontrar varias."""
    if not oportunidades:
        return SIN_OPORTUNIDADES
    return "\n\n".join(formatear_oportunidad(o) for o in oportunidades)
