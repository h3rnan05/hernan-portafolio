"""Restricciones del Portfolio Optimizer — el único lugar donde se tocan
los números. Defaults consistentes con risk_manager/config.py (mismo
portafolio de referencia de $44,000).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizerConstraints:
    max_posiciones: int = 5
    max_sector_pct: float = 0.30
    max_posicion_pct: float = 0.25
    min_cash_pct: float = 0.15
    max_beta_portafolio: float = 1.3
    max_vol_portafolio: float = 0.25  # volatilidad anualizada del portafolio
    max_correlacion_par: float = 0.70  # correlación máxima con cualquier posición ya elegida
    peso_minimo_significativo: float = 0.02  # bajo esto no vale la pena abrir la posición

    # Reservado, NO enforced en esta parte — ver "Fuera de alcance" en el
    # README. Requiere simulación histórica (backtesting/Monte Carlo), que
    # este optimizador no hace: es un cálculo de un solo período, no una
    # serie de tiempo. Existe el campo para que la interfaz no cambie
    # cuando el Validation Pipeline lo implemente.
    max_drawdown_objetivo: float = 0.20

    def validar(self) -> None:
        rango_01_estricto = {
            "max_sector_pct": self.max_sector_pct,
            "max_posicion_pct": self.max_posicion_pct,
            "max_vol_portafolio": self.max_vol_portafolio,
            "peso_minimo_significativo": self.peso_minimo_significativo,
            "max_drawdown_objetivo": self.max_drawdown_objetivo,
        }
        for nombre, v in rango_01_estricto.items():
            if not (0.0 < v <= 1.0):
                raise ValueError(f"{nombre}={v} debe estar en (0, 1]")
        # min_cash_pct=0 es válido: "sin reserva mínima obligatoria".
        if not (0.0 <= self.min_cash_pct <= 1.0):
            raise ValueError(f"min_cash_pct={self.min_cash_pct} debe estar en [0, 1]")
        if not (-1.0 <= self.max_correlacion_par <= 1.0):
            raise ValueError(
                f"max_correlacion_par={self.max_correlacion_par} debe estar en [-1, 1]"
            )
        if self.max_posiciones < 1:
            raise ValueError("max_posiciones debe ser >= 1")
        if self.max_beta_portafolio <= 0:
            raise ValueError("max_beta_portafolio debe ser > 0")


LIMITES_DEFAULT = OptimizerConstraints()
