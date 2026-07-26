"""Clasifica QUÉ TIPO de oportunidad es -- la idea explícita del dueño
del producto de no limitarse a "compra XYZ" sino decir también "esto es
un short squeeze" o "esto es un breakout", porque como trader no todas
las oportunidades se operan igual.

Como máximo una etiqueta por ticker, en un orden de prioridad fijo
(short squeeze > earnings play > breakout > news momentum > reversal >
trend continuation) -- mismo principio que `screener/opportunity_hunter.
detectar_patron`: nunca se mezclan dos etiquetas para el mismo ticker el
mismo día.

`breakout` va ANTES que el `news_momentum` genérico a propósito: como
Prompt 4 exige un catalizador confirmado para CUALQUIER alerta (incluida
una ruptura técnica), casi cualquier breakout real también tendría un
catalizador detrás -- si `news_momentum` fuera más prioritario, la
etiqueta "breakout" casi nunca aparecería, aunque la estructura técnica
(nuevo máximo + volumen) sea la señal más específica y accionable de las
dos. `news_momentum` queda como el catch-all para catalizadores
confirmados que NO muestran esa estructura técnica limpia.

`clasificar()` se llama SOLO sobre oportunidades que ya pasaron el
filtro de alertas (`alerts.py`: catalizador confirmado + RVOL alto) --
por eso el fallback final es "news momentum" (siempre hay al menos un
catalizador real disponible en ese punto), nunca un patrón inventado.

Limitación honesta: `_es_reversal` verifica el ESTADO actual del cruce
MACD (línea por encima de la señal) porque este proyecto no conserva la
serie histórica completa de MACD -- solo el punto final que calcula
`factors/momentum.py`. Es una aproximación razonable ("el cruce ya
ocurrió recientemente y sigue vigente"), no una detección exacta del
momento exacto del cruce."""

from __future__ import annotations

from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata

ETIQUETAS: dict[str, str] = {
    "short_squeeze": "🚀 SHORT SQUEEZE",
    "earnings_play": "💰 EARNINGS PLAY",
    "news_momentum": "⚡ NEWS MOMENTUM",
    "breakout": "🔥 BREAKOUT",
    "reversal": "🔄 REVERSAL",
    "trend_continuation": "📈 TREND CONTINUATION",
}

ORDEN_PRIORIDAD: tuple[str, ...] = (
    "short_squeeze", "earnings_play", "breakout",
    "news_momentum", "reversal", "trend_continuation",
)

# Umbrales fijos y documentados -- decisiones editoriales, nunca
# ajustados por ticker ni "aprendidos" por un modelo.
UMBRAL_FLOAT_BAJO = 20_000_000          # acciones -- low float clásico
UMBRAL_SHORT_INTERES_ALTO = 0.20        # 20% del float en corto
UMBRAL_RVOL_SQUEEZE = 3.0
UMBRAL_GAP_EARNINGS = 0.05              # 5% de gap el día del reporte
UMBRAL_RVOL_NOTICIA = 3.0
UMBRAL_RVOL_BREAKOUT = 2.0
UMBRAL_PROXIMIDAD_52S_BREAKOUT = 0.98
RSI_REVERSAL_MIN, RSI_REVERSAL_MAX = 30.0, 50.0
RSI_SANO_MIN, RSI_SANO_MAX = 40.0, 65.0
BANDA_PULLBACK_EMA20 = 0.05              # ±5% de la EMA20

_TIPOS_CATALIZADOR_NOTICIA = {
    "fda", "adquisicion", "contrato", "regulatorio", "guidance",
    "nuevo_cliente", "patente", "buyback", "insider_buying",
    "upgrade_analista", "rumor",
}


def _es_short_squeeze(meta: Metadata, factores: FactoresMomentum) -> bool:
    return (
        meta.shares_float is not None and meta.shares_float <= UMBRAL_FLOAT_BAJO
        and meta.short_pct_float is not None and meta.short_pct_float >= UMBRAL_SHORT_INTERES_ALTO
        and factores.rvol is not None and factores.rvol >= UMBRAL_RVOL_SQUEEZE
    )


def _es_earnings_play(catalizador: Catalizador | None, factores: FactoresMomentum) -> bool:
    return (
        catalizador is not None and catalizador.tipo == "earnings"
        and factores.gap_pct is not None and abs(factores.gap_pct) >= UMBRAL_GAP_EARNINGS
    )


def _es_news_momentum(catalizador: Catalizador | None, factores: FactoresMomentum) -> bool:
    return (
        catalizador is not None and catalizador.tipo in _TIPOS_CATALIZADOR_NOTICIA
        and factores.rvol is not None and factores.rvol >= UMBRAL_RVOL_NOTICIA
    )


def _es_breakout(factores: FactoresMomentum) -> bool:
    return (
        factores.breakout_20d
        and factores.distancia_max_52s is not None
        and factores.distancia_max_52s >= UMBRAL_PROXIMIDAD_52S_BREAKOUT
        and factores.rvol is not None and factores.rvol >= UMBRAL_RVOL_BREAKOUT
    )


def _es_reversal(factores: FactoresMomentum) -> bool:
    return (
        factores.rsi is not None and RSI_REVERSAL_MIN <= factores.rsi <= RSI_REVERSAL_MAX
        and factores.macd is not None and factores.macd_signal is not None
        and factores.macd > factores.macd_signal
    )


def _es_trend_continuation(spot: float, factores: FactoresMomentum) -> bool:
    if factores.ema20 is None or factores.ema50 is None or factores.rsi is None or factores.ema20 <= 0:
        return False
    tendencia_alcista = factores.ema20 > factores.ema50
    dentro_de_banda = abs(spot - factores.ema20) / factores.ema20 <= BANDA_PULLBACK_EMA20
    rsi_sano = RSI_SANO_MIN <= factores.rsi <= RSI_SANO_MAX
    return tendencia_alcista and dentro_de_banda and rsi_sano


def tipo_oportunidad(
    spot: float, factores: FactoresMomentum, catalizador: Catalizador | None, meta: Metadata,
) -> str:
    """Clave interna (ver ETIQUETAS para el texto con emoji). Único punto
    de entrada de clasificación -- como máximo un tipo por ticker."""
    if _es_short_squeeze(meta, factores):
        return "short_squeeze"
    if _es_earnings_play(catalizador, factores):
        return "earnings_play"
    if _es_breakout(factores):
        return "breakout"
    if _es_news_momentum(catalizador, factores):
        return "news_momentum"
    if _es_reversal(factores):
        return "reversal"
    if _es_trend_continuation(spot, factores):
        return "trend_continuation"
    return "news_momentum"  # ver docstring del módulo: siempre hay catalizador confirmado aquí


def clasificar(
    spot: float, factores: FactoresMomentum, catalizador: Catalizador | None, meta: Metadata,
) -> str:
    return ETIQUETAS[tipo_oportunidad(spot, factores, catalizador, meta)]
