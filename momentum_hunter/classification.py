"""Detección de patrones -- Prompt 4, pregunta 4: "¿Está formando un
patrón claro?" Los seis patrones son el vocabulario real de un trader de
momentum (Ross Cameron / Warrior Trading), no las categorías genéricas
que tenía este módulo antes de 2026-07-26 (breakout/news momentum/
earnings play/reversal/short squeeze).

Pivote deliberado: ese vocabulario anterior describía RESULTADOS ("la
acción subió con volumen") sobre barras DIARIAS -- pensaba como
screener. Estos seis patrones describen FORMAS de la acción del precio
en los últimos minutos, y solo se pueden ver con velas intradía
(`BarraIntradia`) -- es la pieza central del pivote de "screener" a
"trader" (ver el análisis de Prompt 1). El desequilibrio oferta/demanda
(float bajo + interés en corto -- lo que antes era la categoría
"short_squeeze") ya NO es un patrón aparte: es la pregunta 3 del
evaluador (`evaluator.py`), porque en la mentalidad de Ross Cameron el
float bajo es un MULTIPLICADOR de cualquier patrón, no un patrón en sí
mismo.

Como máximo un patrón por ticker, en orden de prioridad fijo (el más
específico/explosivo primero) -- mismo principio que la versión anterior
de este módulo: nunca se mezclan dos etiquetas el mismo día.

Limitación honesta: son aproximaciones deterministas sobre velas de 1
minuto gratis (ruidosas) -- no el reconocimiento visual que hace un
trader humano viendo el gráfico. Cada función exige condiciones
numéricas explícitas y documentadas, nunca "se ve parecido a")."""

from __future__ import annotations

from momentum_hunter.models import BarraIntradia, FactoresIntradia

ETIQUETAS: dict[str, str] = {
    "high_tight_flag": "🚩 HIGH TIGHT FLAG",
    "gap_and_go": "🚀 GAP AND GO",
    "opening_range_breakout": "📊 OPENING RANGE BREAKOUT",
    "bull_flag": "🏁 BULL FLAG",
    "micro_pullback": "🔁 MICRO PULLBACK",
    "trend_continuation": "📈 TREND CONTINUATION",
}

# Pivote 2026-07-26 (pedido explícito: "el usuario nunca debería sentir
# que necesita saber análisis técnico"): `ETIQUETAS` (nombre en inglés,
# jerga de trading) queda solo para logs/depuración -- todo lo que se
# manda a Telegram (`report.py`, `radar.py`) usa esta traducción a
# lenguaje llano en su lugar. Frases cortas, en minúscula (se insertan
# dentro de una oración), sin nombrar el patrón técnico.
DESCRIPCION_HUMANA: dict[str, str] = {
    "high_tight_flag": "subiendo muy rápido y haciendo una pausa mínima",
    "gap_and_go": "rompiendo con fuerza justo al abrir",
    "opening_range_breakout": "rompiendo el techo de los primeros minutos",
    "bull_flag": "tomando un respiro después de subir fuerte",
    "micro_pullback": "recuperando tras un respiro corto",
    "trend_continuation": "subiendo de forma constante",
}

# Más específico/explosivo primero -- un High Tight Flag real también
# cumpliría las condiciones de un Bull Flag genérico, así que tiene que
# evaluarse antes para no perder la etiqueta más informativa.
ORDEN_PRIORIDAD: tuple[str, ...] = (
    "high_tight_flag", "gap_and_go", "opening_range_breakout",
    "bull_flag", "micro_pullback", "trend_continuation",
)

UMBRAL_GAP_MINIMO = 0.08            # 8%+ de gap para Gap and Go
UMBRAL_RVOL_PATRON = 2.0            # confirmación mínima de volumen para cualquier ruptura
UMBRAL_IMPULSO_BULL_FLAG = 0.05     # 5%+ de impulso en 3 velas antes de la bandera
UMBRAL_IMPULSO_HTF = 0.50           # 50%+ de impulso reciente -- lo que hace "tight" al high
BANDA_BULL_FLAG_PCT = 0.03          # rango de la bandera <= 3% del precio
BANDA_HTF_PCT = 0.015               # HTF: consolidación todavía más angosta


def _rango_relativo(bi: BarraIntradia, n: int) -> float | None:
    if len(bi.close) < n or bi.close[-1] <= 0:
        return None
    ventana_high = bi.high[-n:]
    ventana_low = bi.low[-n:]
    return (max(ventana_high) - min(ventana_low)) / bi.close[-1]


def _impulso(bi: BarraIntradia, inicio: int, fin: int) -> float | None:
    """Retorno entre `bi.close[-inicio]` y `bi.close[-fin]` (índices
    negativos, `inicio` más lejano que `fin`)."""
    if len(bi.close) < inicio:
        return None
    base = bi.close[-inicio]
    if base <= 0:
        return None
    return (bi.close[-fin] - base) / base


def _es_high_tight_flag(bi: BarraIntradia) -> bool:
    if len(bi.close) < 11:
        return False
    impulso = _impulso(bi, 11, 4)   # impulso de las velas -11..-4
    banda = _rango_relativo(bi, 3)  # bandera: últimas 3 velas
    if impulso is None or banda is None:
        return False
    return impulso >= UMBRAL_IMPULSO_HTF and banda <= BANDA_HTF_PCT


def _es_gap_and_go(factores: FactoresIntradia) -> bool:
    return (
        factores.gap_pct is not None and factores.gap_pct >= UMBRAL_GAP_MINIMO
        and factores.maximo_premarket is not None and factores.precio_actual is not None
        and factores.precio_actual > factores.maximo_premarket
        and factores.rvol_actual is not None and factores.rvol_actual >= UMBRAL_RVOL_PATRON
    )


def _es_opening_range_breakout(factores: FactoresIntradia) -> bool:
    return (
        factores.rango_apertura_max is not None and factores.precio_actual is not None
        and factores.precio_actual > factores.rango_apertura_max
        and factores.rvol_actual is not None and factores.rvol_actual >= UMBRAL_RVOL_PATRON
    )


def _es_bull_flag(bi: BarraIntradia) -> bool:
    if len(bi.close) < 8:
        return False
    impulso = _impulso(bi, 8, 5)    # impulso de las velas -8..-5
    banda = _rango_relativo(bi, 3)  # bandera: últimas 3 velas
    if impulso is None or banda is None:
        return False
    volumen_bandera = sum(bi.volume[-3:]) / 3
    volumen_impulso = sum(bi.volume[-6:-3]) / 3 if len(bi.volume) >= 6 else None
    volumen_decae = volumen_impulso is None or volumen_bandera < volumen_impulso
    return impulso >= UMBRAL_IMPULSO_BULL_FLAG and banda <= BANDA_BULL_FLAG_PCT and volumen_decae


def _es_micro_pullback(bi: BarraIntradia, factores: FactoresIntradia) -> bool:
    """Impulso de dos velas -> una vela de pullback con menos volumen ->
    la vela actual ya recupera, todo sin perder la EMA9 -- la entrada
    clásica de Ross Cameron dentro de una tendencia ya en marcha."""
    if len(bi.close) < 5 or factores.ema9 is None:
        return False
    c, v = bi.close, bi.volume
    impulso_previo = c[-5] < c[-4] < c[-3]
    vela_pullback = c[-3] > c[-2]
    volumen_pullback_bajo = v[-2] < v[-3]
    recuperando = c[-1] > c[-2]
    sobre_ema9 = c[-1] >= factores.ema9 * 0.995
    return impulso_previo and vela_pullback and volumen_pullback_bajo and recuperando and sobre_ema9


def _es_trend_continuation(bi: BarraIntradia, factores: FactoresIntradia) -> bool:
    """Catch-all: la tendencia sigue intacta (sobre VWAP y EMA9 en las
    últimas velas) aunque no haya una forma más específica -- la señal
    más débil de las seis, por eso va última en la prioridad."""
    if len(bi.close) < 6 or factores.vwap is None or factores.ema9 is None:
        return False
    ventana = bi.close[-6:]
    sobre_vwap = all(x >= factores.vwap * 0.995 for x in ventana)
    sobre_ema9 = all(x >= factores.ema9 * 0.99 for x in ventana)
    tendencia_al_alza = ventana[-1] > ventana[0]
    return sobre_vwap and sobre_ema9 and tendencia_al_alza


def detectar_patron(bi_hoy: BarraIntradia, factores: FactoresIntradia) -> str | None:
    """Único punto de entrada -- como máximo un patrón por ticker, en
    orden de prioridad. None si ninguno aplica (evaluator.py lo trata
    como una respuesta negativa a la pregunta 4, penalización fuerte)."""
    if _es_high_tight_flag(bi_hoy):
        return "high_tight_flag"
    if _es_gap_and_go(factores):
        return "gap_and_go"
    if _es_opening_range_breakout(factores):
        return "opening_range_breakout"
    if _es_bull_flag(bi_hoy):
        return "bull_flag"
    if _es_micro_pullback(bi_hoy, factores):
        return "micro_pullback"
    if _es_trend_continuation(bi_hoy, factores):
        return "trend_continuation"
    return None


def etiqueta(clave: str) -> str:
    return ETIQUETAS[clave]
