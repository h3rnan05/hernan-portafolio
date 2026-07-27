"""Configuración del Momentum Opportunity Hunter -- un solo lugar para
tocar universo, pesos y umbrales de alerta (mismo principio que
screener/config.py).

Deliberadamente NO comparte config con `screener/`: ese motor está
calibrado para el S&P 500 (empresas grandes, valoración, calidad de
negocio). Este vive en un universo completamente distinto -- penny
stocks / small caps / low float -- con una pregunta distinta ("¿puede
explotar en los próximos días?" en vez de "¿es una buena empresa?"),
así que comparte cero fundamentales, cero pesos, cero umbrales con el
screener. Ver momentum_hunter/README.md.

Nota sobre `market_cap_max`: el pedido original decía "menor a 2
billones" -- en el uso real de un Opportunity Hunter de small/micro caps
eso solo tiene sentido como 2,000 millones de dólares (small-cap, no
mega-cap), así que el default es 2e9 USD. Queda como parámetro explícito
por si se quisiera ajustar.

Pivote 2026-07-26 (pedido explícito: "quiero que piense como un trader
de momentum, no como un screener"): la etapa 2 del pipeline (`run.py`)
ya no evalúa con barras diarias -- pide velas intradía SOLO para los
`max_candidatos_intradia` mejores candidatos de la etapa 1 (evitar pedir
esto para miles de tickers, ver `data/provider.py`). `umbral_rvol_intradia`
es una lectura de "¿está entrando dinero AHORA?" (vela actual vs. las
últimas 5), distinta de `rvol_minimo_alerta` (que compara contra el
promedio de 20 DÍAS -- sigue existiendo como filtro grueso de la etapa 1).
`extension_maxima_pct`/`velas_maximas_desde_patron` son los umbrales
duros del Early Opportunity Engine (`early_opportunity.py`) para decidir
"temprano" vs. "tarde" -- un veredicto que puede bajar una alerta aunque
el score compuesto sea alto (Prompt 2)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MomentumConfig:
    # --- Universo ---
    bolsas: tuple[str, ...] = ("NYSE", "NASDAQ", "AMEX")
    precio_min: float = 0.75
    precio_max: float = 20.0
    market_cap_max: float = 2_000_000_000.0   # 2,000 millones USD (ver docstring)
    volumen_promedio_min: float = 300_000.0   # acciones/día, promedio 20 sesiones
    excluir_etf: bool = True
    excluir_spac: bool = True
    excluir_cef: bool = True                  # closed-end funds
    liquidez_minima_adr: float = 300_000.0    # ADRs con menos volumen que esto se excluyen

    # --- Pesos del score compuesto (deben sumar 1.0) ---
    # Nunca incluye fundamentales de valoración (P/E, ROE, dividendos) --
    # esas señales son del Investment Analyst (screener/), no de este bot.
    pesos: dict[str, float] = field(default_factory=lambda: {
        "momentum":    0.40,
        "catalizador": 0.25,
        "liquidez":    0.20,
        "riesgo":      0.15,
    })

    # --- Umbrales de alerta (Prompt 7: calidad antes que cantidad) ---
    score_minimo_alerta: float = 85.0
    rvol_minimo_alerta: float = 4.0           # volumen actual / promedio 20 sesiones (filtro grueso, etapa 1)
    requiere_catalizador_confirmado: bool = True
    # Techo de seguridad, no un objetivo -- la selectividad real la hace
    # el evaluator (5 preguntas secuenciales) y el veredicto de "temprano"
    # del Early Opportunity Engine. Bajado de 5 a 3 (pedido explícito,
    # 2026-07-26: "prefiero una sola oportunidad excelente que veinte
    # promedio").
    limite_diario_alertas: int = 3
    # Principio 4 (pedido 2026-07-27): "cada alerta debe competir contra
    # todas las demás... si hoy solamente pudiera abrir UNA operación,
    # ¿sería esta? Si la respuesta es no, no quiero recibir la alerta."
    # Con True (default), por corrida solo se alerta LA MEJOR candidata
    # que además sobreviva al abogado del diablo (skeptic.py); las demás
    # accionables aparecen en el radar como subcampeonas, con su motivo.
    # limite_diario_alertas queda como techo cuando esto se apague.
    solo_la_mejor: bool = True

    # --- Catalizadores ---
    fuentes_minimas_rumor: int = 2            # un rumor solo cuenta si aparece en >=N fuentes
    dias_ventana_catalizador: int = 3         # noticia debe ser de los últimos N días

    # --- Etapa 2: datos intradía (solo sobre los mejores candidatos) ---
    intervalo_intradia: str = "1m"
    periodo_intradia: str = "5d"               # margen para tener el cierre de ayer disponible
    max_candidatos_intradia: int = 50          # tope duro -- ver data/provider.py
    umbral_rvol_intradia: float = 3.0          # "¿está entrando dinero AHORA?" (Prompt 4, pregunta 2)
    minutos_rango_apertura: int = 5            # ventana del Opening Range Breakout

    # --- Early Opportunity Engine (Prompt 2) ---
    extension_maxima_pct: float = 0.12         # >12% lejos de VWAP/EMA9 -> "ya vamos tarde"
    velas_maximas_desde_patron: int = 8        # más de 8 velas desde la ruptura -> "ya vamos tarde"

    # --- Aprendizaje / tracking ---
    horizontes_seguimiento: tuple[int, ...] = (1, 3, 5, 10)  # días hábiles de seguimiento

    def validar(self) -> None:
        total = sum(self.pesos.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Los pesos suman {total:.3f}, deben sumar 1.0")
        if self.precio_min <= 0 or self.precio_max <= self.precio_min:
            raise ValueError("precio_min debe ser > 0 y menor que precio_max")
        if not (0.0 <= self.score_minimo_alerta <= 100.0):
            raise ValueError("score_minimo_alerta debe estar en [0, 100]")
        if self.limite_diario_alertas < 1:
            raise ValueError("limite_diario_alertas debe ser >= 1")
        if self.max_candidatos_intradia < 1:
            raise ValueError("max_candidatos_intradia debe ser >= 1")
        if self.extension_maxima_pct <= 0:
            raise ValueError("extension_maxima_pct debe ser > 0")
        if self.velas_maximas_desde_patron < 1:
            raise ValueError("velas_maximas_desde_patron debe ser >= 1")


CONFIG = MomentumConfig()
