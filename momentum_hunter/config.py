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
por si se quisiera ajustar."""

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
    rvol_minimo_alerta: float = 4.0           # volumen actual / promedio 20 sesiones
    requiere_catalizador_confirmado: bool = True
    limite_diario_alertas: int = 5

    # --- Catalizadores ---
    fuentes_minimas_rumor: int = 2            # un rumor solo cuenta si aparece en >=N fuentes
    dias_ventana_catalizador: int = 3         # noticia debe ser de los últimos N días

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


CONFIG = MomentumConfig()
