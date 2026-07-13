"""Risk Manager — el módulo con poder de veto.

100% determinístico. Sin llamadas a red, sin LLM, sin aleatoriedad: la misma
entrada produce siempre la misma salida, y eso es deliberado (Principio #2
del roadmap: "toda decisión debe ser determinística").

Reglas de ESTA PARTE (extraídas y formalizadas de wizards_bot.py, que las
tenía dispersas dentro de comprar()/procesar_ideas()):
  1. Nunca promediar una posición perdedora (o cualquiera: no se añade a
     una posición ya abierta en el mismo ticker).
  2. Máximo de posiciones simultáneas.
  3. Stop por ATR (entrada - atr_mult × ATR).
  4. Position sizing por riesgo fijo (% del equity por operación).
  5. Tope de tamaño de posición (% del equity, notional).
  6. No exceder el efectivo disponible.
  7. Calor máximo del portafolio (riesgo abierto total, incluyendo el
     candidato, sobre equity).
  8. Exposición máxima por sector (si se conoce el sector).
  9. Reserva mínima de efectivo tras la compra.

Reglas que NO están aquí todavía (quedan para la siguiente parte, ver
ROADMAP.md): correlación máxima, beta máximo del portafolio, drawdown
objetivo, VaR, stress testing. Requieren datos que el sistema no calcula
todavía (matriz de correlación, betas).
"""

from __future__ import annotations

import logging

from risk_manager.config import RiskLimits
from risk_manager.models import PortfolioState, RiskVerdict, TradeCandidate

log = logging.getLogger("risk_manager")


class RiskManager:
    def __init__(self, limites: RiskLimits) -> None:
        limites.validar()
        self.limites = limites

    def evaluar(
        self, candidato: TradeCandidate, portafolio: PortfolioState,
    ) -> RiskVerdict:
        """Único punto de entrada. Devuelve APROBADO con tamaño/stop
        calculados, o RECHAZADO con el motivo exacto — nunca ambigüedad."""
        L = self.limites
        t = candidato.ticker

        if t in portafolio.posiciones:
            return self._rechazo(t, "ya existe una posición abierta en este "
                                     "ticker (regla dura: nunca promediar)")

        if len(portafolio.posiciones) >= L.max_posiciones:
            return self._rechazo(
                t, f"máximo de posiciones alcanzado "
                   f"({len(portafolio.posiciones)}/{L.max_posiciones})")

        if candidato.precio <= 0 or candidato.atr <= 0:
            return self._rechazo(t, "precio o ATR inválidos (<=0)")

        stop = round(candidato.precio - L.atr_mult_stop * candidato.atr, 2)
        riesgo_por_accion = candidato.precio - stop
        if riesgo_por_accion <= 0:
            return self._rechazo(t, "el stop calculado no queda por debajo "
                                     "del precio de entrada")

        # --- Sizing: el riesgo en $ manda, pero nunca por encima de los topes ---
        riesgo_usd_objetivo = portafolio.equity * L.riesgo_por_trade_pct
        qty = int(riesgo_usd_objetivo / riesgo_por_accion)

        tope_notional = int(portafolio.equity * L.max_posicion_pct / candidato.precio)
        qty = min(qty, tope_notional)

        tope_cash = int(portafolio.cash / candidato.precio)
        qty = min(qty, tope_cash)

        if qty < 1:
            return self._rechazo(
                t, f"sizing dio 0 acciones (precio ${candidato.precio:.2f} vs "
                   f"riesgo objetivo ${riesgo_usd_objetivo:.2f} / cash "
                   f"disponible ${portafolio.cash:.2f})")

        riesgo_usd_real = qty * riesgo_por_accion
        riesgo_pct_real = riesgo_usd_real / portafolio.equity

        # --- Calor del portafolio, proyectado CON este trade ---
        calor_actual = portafolio.riesgo_abierto_usd()
        calor_proyectado = (calor_actual + riesgo_usd_real) / portafolio.equity
        if calor_proyectado > L.max_heat_pct:
            return self._rechazo(
                t, f"excedería el calor máximo del portafolio "
                   f"({calor_proyectado:.1%} > {L.max_heat_pct:.0%})")

        # --- Exposición sectorial, proyectada CON este trade ---
        if candidato.sector:
            expo_actual = portafolio.exposicion_sector_usd(candidato.sector)
            expo_proyectada = (expo_actual + qty * candidato.precio) / portafolio.equity
            if expo_proyectada > L.max_sector_pct:
                return self._rechazo(
                    t, f"excedería la exposición máxima al sector "
                       f"'{candidato.sector}' ({expo_proyectada:.1%} > "
                       f"{L.max_sector_pct:.0%})")

        # --- Reserva mínima de efectivo tras la compra ---
        cash_tras_compra = portafolio.cash - qty * candidato.precio
        cash_pct_tras_compra = cash_tras_compra / portafolio.equity
        if cash_pct_tras_compra < L.min_cash_pct:
            # Reducir qty hasta respetar la reserva, en vez de rechazar de
            # golpe: es preferible una posición más chica que ninguna.
            cash_disponible_real = portafolio.cash - portafolio.equity * L.min_cash_pct
            qty = int(cash_disponible_real / candidato.precio)
            if qty < 1:
                return self._rechazo(
                    t, f"respetar la reserva mínima de efectivo "
                       f"({L.min_cash_pct:.0%}) no deja acciones que comprar")
            riesgo_usd_real = qty * riesgo_por_accion
            riesgo_pct_real = riesgo_usd_real / portafolio.equity

        log.info("APROBADO %s: qty=%d stop=%.2f riesgo=$%.2f (%.2f%% equity)",
                  t, qty, stop, riesgo_usd_real, riesgo_pct_real * 100)
        return RiskVerdict(
            aprobado=True, motivo="cumple todas las reglas de riesgo",
            ticker=t, qty=qty, stop=stop,
            riesgo_usd=round(riesgo_usd_real, 2),
            riesgo_pct_equity=round(riesgo_pct_real, 4),
        )

    def _rechazo(self, ticker: str, motivo: str) -> RiskVerdict:
        log.info("RECHAZADO %s: %s", ticker, motivo)
        return RiskVerdict(aprobado=False, motivo=motivo, ticker=ticker)
