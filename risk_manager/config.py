"""Límites del Risk Manager — el único lugar donde se tocan los números.

Valores por defecto tomados del ejemplo real que definió el dueño del
proyecto para el portafolio de $44,000 (ROADMAP.md, Fase Portfolio
Optimizer): riesgo 1% por operación, máx. 5 posiciones, máx. 30% en un
sector, mínimo 15% en efectivo. `ATR_MULT_STOP` y el resto vienen de la
lógica ya probada en wizards_bot.py, extraída aquí como módulo propio.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    riesgo_por_trade_pct: float = 0.01     # 1% del equity por operación
    atr_mult_stop: float = 2.0             # stop = entrada - atr_mult × ATR
    max_heat_pct: float = 0.06             # riesgo abierto total del portafolio
    max_posiciones: int = 5
    max_posicion_pct: float = 0.30         # ninguna posición > 30% del equity
    max_sector_pct: float = 0.30           # ningún sector > 30% del equity
    min_cash_pct: float = 0.15             # reserva mínima de efectivo

    def validar(self) -> None:
        # Deben ser > 0: un límite en 0 haría imposible cualquier trade.
        positivos = {
            "riesgo_por_trade_pct": self.riesgo_por_trade_pct,
            "max_heat_pct": self.max_heat_pct,
            "max_posicion_pct": self.max_posicion_pct,
            "max_sector_pct": self.max_sector_pct,
        }
        for nombre, v in positivos.items():
            if not (0.0 < v <= 1.0):
                raise ValueError(f"{nombre}={v} debe estar en (0, 1]")
        # min_cash_pct=0 sí es válido: "sin reserva mínima obligatoria".
        if not (0.0 <= self.min_cash_pct <= 1.0):
            raise ValueError(f"min_cash_pct={self.min_cash_pct} debe estar en [0, 1]")
        if self.atr_mult_stop <= 0:
            raise ValueError("atr_mult_stop debe ser > 0")
        if self.max_posiciones < 1:
            raise ValueError("max_posiciones debe ser >= 1")


LIMITES_DEFAULT = RiskLimits()
