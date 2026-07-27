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
class BarraIntradia:
    """Velas intradía (1m/5m) de un ticker -- listas paralelas, orden
    cronológico, timestamps en ISO 8601 UTC. Deliberadamente genérica:
    NINGÚN campo aquí es específico de Yahoo. `data/provider.py` es la
    única pieza que sabe de dónde salieron estos números; el resto del
    pipeline (factors/intradia.py, patterns, early_opportunity,
    evaluator) solo conoce esta forma, así que cambiar de proveedor
    (Polygon, Alpaca, Tradier) es escribir OTRA implementación de
    `DataProvider.barras_intradia`, nunca tocar la lógica de trading."""
    ticker: str
    timestamps: list[str]     # ISO 8601 UTC, una por vela
    open: list[float]
    close: list[float]
    high: list[float]
    low: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.close)


@dataclass
class FactoresIntradia:
    """Lo que un trader mira en la pantalla en tiempo real -- calculado
    una sola vez por ticker desde `BarraIntradia` (ver
    `factors/intradia.py`) y reutilizado por `classification.py`
    (patrones), `early_opportunity.py` y `evaluator.py`. Ningún campo
    aquí depende de barras diarias."""
    precio_actual: float | None = None
    vwap: float | None = None                  # VWAP real de la sesión de hoy (no un proxy)
    ema9: float | None = None
    rvol_actual: float | None = None           # volumen de la vela actual / promedio de las N anteriores
    aceleracion_volumen: float | None = None   # promedio de las últimas 3 velas / promedio de las 3 previas
    gap_pct: float | None = None               # apertura regular de hoy vs. cierre regular de ayer
    maximo_dia: float | None = None            # incluye premarket
    maximo_premarket: float | None = None
    rango_apertura_max: float | None = None    # high de los primeros N minutos de sesión regular
    rango_apertura_min: float | None = None
    velas_desde_ruptura: int | None = None     # velas desde que cerró por primera vez sobre el nivel relevante


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
    """Una alerta lista para el mensaje final -- pivote 2026-07-26
    ("quiero que piense como un operador profesional... el usuario
    nunca debería sentir que necesita saber análisis técnico"). Ya NO
    guarda indicadores para mostrar (RVOL/EMA9/VWAP/etc.) ni una
    etiqueta de patrón visible -- todo eso se traduce a lenguaje humano
    en `report.py` ANTES de llegar aquí. Los cuatro campos `que_*`
    responden, en orden, la secuencia mental que pidió el dueño del
    producto: qué pasó -> qué hizo el mercado -> qué está pasando ahora
    -> ¿todavía vale la pena?

    `patron_clave`/`hora_utc`/`catalizador_tipo`/`float_acciones`/
    `gap_pct`/`rvol` NO se muestran en el mensaje -- son la materia
    prima que `tracker.py` persiste para el aprendizaje futuro ("qué
    patrón gana más, qué horario funciona mejor..."): la arquitectura
    queda lista para esa pregunta, aunque todavía no se optimiza nada
    con ella (pedido explícito: "no quiero optimizar eso todavía")."""
    ticker: str
    nombre: str | None
    urgencia: str                     # "Muy Alta" | "Alta" | "Media" | "Baja"
    urgencia_emoji: str
    titular_corto: str                 # frase corta en lenguaje llano para el encabezado
    que_paso: str                       # 1) el catalizador, en lenguaje llano
    que_hizo_mercado: str               # 2) volumen/gap, en lenguaje llano
    que_pasa_ahora: str                 # 3) el patrón, en lenguaje llano
    vale_la_pena: bool                   # 4) Early Opportunity Engine -- ¿llegamos a tiempo?
    por_que_vale_la_pena: str
    por_que_esta_alerta: str             # "¿por qué esta y no otra?"
    entrada: float
    stop: float | None
    objetivo: float | None
    invalidacion: str                    # sin nombrar el nivel técnico, solo el precio
    catalizador: Catalizador | None      # se cita la fuente al final, trazabilidad
    score: float                          # Convicción base (scoring.puntuar) -- nunca se muestra
    fecha: str
    # -- Sin mostrar en el mensaje: materia prima para el aprendizaje futuro (ver docstring) --
    patron_clave: str = ""
    hora_utc: int = 0
    catalizador_tipo: str | None = None
    float_acciones: float | None = None
    gap_pct: float | None = None
    rvol: float | None = None
    # -- Pedido 2026-07-27 ("sistema en el que confiaría mi patrimonio") --
    probabilidad_historica: str = ""     # Principio 3: probabilidad real medida, o la admisión de que no hay
    advertencias: list[str] = field(default_factory=list)  # Principio 2/11: "qué tendría que salir mal"
    n_evaluados: int = 0                  # Principio 4: contra cuántas candidatas compitió hoy
    # -- Refinamiento "Head Trader" (2026-07-27, segunda ronda) --
    rank: int = 0                          # lugar del día (#1, #2...) entre las alertadas
    n_universo: int = 0                    # cuántas acciones se escanearon en la etapa 1
    por_que_unica: list[str] = field(default_factory=list)   # qué reunió que las demás no
    confianza_texto: str = ""              # nivel de confianza CON sus razones, prearmado
    calidad_historica: str = ""            # estrellas basadas en historial propio, o "" sin muestra
    ventana_texto: str = ""                # cuánto tiempo estimo que sigue vigente
    señales_confirman: list[str] = field(default_factory=list)  # qué espero ver si es buena
    señales_fallan: list[str] = field(default_factory=list)      # qué me diría que está fallando
