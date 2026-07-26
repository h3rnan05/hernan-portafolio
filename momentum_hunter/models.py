"""Estructuras de datos compartidas por todo el pipeline. Cada campo que
puede no estar disponible con datos gratis es `None` explícito -- nunca
se inventa un valor (mismo principio que `screener/data/provider.py`)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Barras:
    """Serie diaria de un ticker. Listas paralelas, orden cronológico.
    Independiente de `screener.data.provider.Barras` a propósito: los dos
    proyectos no deben acoplarse ni siquiera en sus tipos de datos."""
    ticker: str
    fechas: list[str]
    open: list[float]
    close: list[float]
    high: list[float]
    low: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.close)


@dataclass
class Metadata:
    """Snapshot no-técnico de un ticker -- lo que se necesita para el
    filtro de universo y para el sub-score de riesgo/liquidez. Todo
    best-effort con datos gratis; cualquier campo puede ser None."""
    ticker: str
    nombre: str | None = None
    bolsa: str | None = None                  # NYSE | NASDAQ | AMEX
    es_etf: bool = False
    es_spac: bool = False
    es_cef: bool = False
    es_adr: bool = False
    market_cap: float | None = None
    shares_float: float | None = None         # acciones en circulación libre (float)
    short_pct_float: float | None = None      # % del float en corto
    days_to_cover: float | None = None        # short interest / volumen promedio diario
    borrow_fee_pct: float | None = None       # NO disponible gratis hoy -- siempre None
    cambio_premarket_pct: float | None = None
    cambio_afterhours_pct: float | None = None


@dataclass(frozen=True)
class Catalizador:
    """Un catalizador detectado -- siempre trazable a un titular real,
    nunca inventado. `confirmado` refleja la regla de Prompt 4: para
    rumores, solo cuenta si aparece en >= `fuentes_minimas_rumor` fuentes
    distintas (ver catalysts/detector.py); para el resto de tipos, un solo
    titular confiable ya confirma."""
    tipo: str                 # earnings | fda | contrato | nuevo_cliente | guidance |
                               # adquisicion | patente | buyback | insider_buying |
                               # regulatorio | upgrade_analista | rumor
    titular: str
    fuente: str
    fecha: str | None = None
    confirmado: bool = True
    fuentes_adicionales: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class FactoresMomentum:
    """Factores crudos de momentum -- calculados una sola vez por ticker
    y reutilizados por scoring.py y classification.py (nunca se
    recalculan dos veces con datos distintos)."""
    gap_pct: float | None = None
    rvol: float | None = None                 # volumen de hoy / promedio 20 sesiones
    breakout_20d: bool = False                # nuevo máximo de 20 sesiones con volumen
    distancia_max_52s: float | None = None    # precio / máximo 52 semanas (1.0 = en máximos)
    ema20: float | None = None
    ema50: float | None = None
    vwap_proxy: float | None = None           # aproximación con barras diarias (ver factors/momentum.py)
    atr: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None


@dataclass(frozen=True)
class Oportunidad:
    """Una alerta lista para el mensaje final -- todos los campos del
    Prompt 8, más la clasificación (Prompt "lo que construiría después")
    y la estrategia (Prompt 9)."""
    ticker: str
    nombre: str | None
    clasificacion: str                # emoji + etiqueta, ver classification.py
    score: float
    catalizador: Catalizador | None
    que_ocurrio: str
    por_que_puede_seguir: str
    entrada: float
    stop: float | None
    primer_objetivo: float | None
    segundo_objetivo: float | None
    riesgo_texto: str
    capital_minimo: float
    urgencia: str                      # "Alta" | "Media" | "Baja"
    que_espero: str
    que_invalida: str
    tiempo_esperado: str
    niveles_alerta: list[float]
    estrategia_nombre: str
    estrategia_justificacion: list[str]
    fecha: str
