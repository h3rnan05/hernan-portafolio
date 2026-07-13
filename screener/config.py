"""Configuración del screener cuantitativo — un solo lugar para tocar pesos,
universo y umbrales. Todo lo que un analista querría ajustar vive aquí, no
disperso en el código (principio: 'configuration files')."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScreenerConfig:
    # --- Universo ---
    universe: str = "SP500"           # SP500 | un archivo de tickers propio
    min_dollar_volume: float = 5e6    # liquidez mínima (precio × volumen medio)
    min_price: float = 5.0            # descarta penny stocks
    min_barras: int = 200             # historia mínima para 200 DMA / momentum 12m

    # --- Pesos del score compuesto (deben sumar 1.0) ---
    # Empezamos sin fundamentales de peso porque en datos gratis son parciales;
    # el motor de factores fundamentales existe y se puede subir de peso cuando
    # se conecte una fuente de datos de pago (ver data/provider.py).
    pesos: dict[str, float] = field(default_factory=lambda: {
        "momentum":     0.30,
        "tendencia":    0.20,   # posición vs medias móviles (trend-following)
        "baja_vol":     0.15,   # preferencia por menor volatilidad (defensivo)
        "liquidez":     0.10,
        "calidad":      0.15,   # fundamental, best-effort (ROE, márgenes)
        "valor":        0.10,   # fundamental, best-effort (P/E, P/B invertidos)
    })

    # --- Salida ---
    top_n: int = 20                   # tamaño de la shortlist
    razones_por_accion: int = 3       # cuántos factores destacados explicar

    def validar(self) -> None:
        total = sum(self.pesos.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Los pesos suman {total:.3f}, deben sumar 1.0")


CONFIG = ScreenerConfig()
