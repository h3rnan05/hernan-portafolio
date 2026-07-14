"""Implementación de referencia (Parte 1): ranking por retorno ajustado por
riesgo × calidad factorial, agregado en ese orden mientras quepa dentro de
TODAS las restricciones — reduciendo el peso en vez de rechazar de una
cuando es posible (mismo patrón que usa risk_manager.engine con la reserva
de cash).

Esto NO es Mean-Variance Optimization real (no resuelve un problema de
optimización cuadrática sobre el frente eficiente) — es un ranking + una
asignación voraz (greedy). Es un baseline correcto, determinístico y
reemplazable: sirve de piso mientras se construyen MVO/Risk Parity/HRP en
la siguiente parte, implementando la misma interfaz OptimizationStrategy.
"""

from __future__ import annotations

from portfolio_optimizer.config import OptimizerConstraints
from portfolio_optimizer.math_utils import beta_portafolio, correlacion, score_diversificacion, volatilidad_portafolio
from portfolio_optimizer.models import Allocation, Candidate, CandidatoRechazado, Holding, PortafolioOptimo
from portfolio_optimizer.strategies.base import OptimizationStrategy

_PASOS_AJUSTE_RIESGO = 10


class ScoreWeightedGreedy(OptimizationStrategy):
    def construir(
        self,
        candidatos: list[Candidate],
        cartera_actual: list[Holding],
        correlaciones: dict[tuple[str, str], float],
        restricciones: OptimizerConstraints,
    ) -> PortafolioOptimo:
        L = restricciones
        rechazados: list[CandidatoRechazado] = []

        tickers_actuales = {h.ticker for h in cartera_actual}
        peso_actual_total = sum(h.peso for h in cartera_actual)
        sector_actual: dict[str, float] = {}
        for h in cartera_actual:
            sector_actual[h.sector] = sector_actual.get(h.sector, 0.0) + h.peso

        pesos: dict[str, float] = {h.ticker: h.peso for h in cartera_actual}
        volatilidades: dict[str, float] = {h.ticker: h.volatilidad for h in cartera_actual}
        betas: dict[str, float] = {h.ticker: h.beta for h in cartera_actual}

        seleccionados: list[Candidate] = []
        n_posiciones = len(cartera_actual)
        presupuesto_invertible = round(1.0 - L.min_cash_pct - peso_actual_total, 6)

        for c in sorted(candidatos, key=self._atractivo, reverse=True):
            if c.ticker in tickers_actuales:
                rechazados.append(CandidatoRechazado(c.ticker, "ya existe una posición en este ticker (no se promedia)"))
                continue
            if n_posiciones >= L.max_posiciones:
                rechazados.append(
                    CandidatoRechazado(c.ticker, f"máximo de posiciones alcanzado ({n_posiciones}/{L.max_posiciones})")
                )
                continue
            if presupuesto_invertible < L.peso_minimo_significativo:
                rechazados.append(
                    CandidatoRechazado(c.ticker, "no queda presupuesto invertible (reserva mínima de cash al límite)")
                )
                continue

            sector_usado = sector_actual.get(c.sector, 0.0)
            tope_sector = max(0.0, L.max_sector_pct - sector_usado)
            peso_tentativo = min(L.max_posicion_pct, presupuesto_invertible, tope_sector)

            if peso_tentativo < L.peso_minimo_significativo:
                rechazados.append(
                    CandidatoRechazado(
                        c.ticker,
                        f"no cabe un tamaño significativo (disponible {peso_tentativo:.1%} "
                        f"< mínimo {L.peso_minimo_significativo:.1%}, tope sector {tope_sector:.1%})",
                    )
                )
                continue

            corr_maxima, ticker_corr = self._correlacion_maxima(c.ticker, pesos, correlaciones)
            if corr_maxima > L.max_correlacion_par:
                rechazados.append(
                    CandidatoRechazado(
                        c.ticker,
                        f"correlación excesiva con {ticker_corr} (ρ={corr_maxima:.2f} > {L.max_correlacion_par:.2f})",
                    )
                )
                continue

            peso_final = self._ajustar_por_riesgo_portafolio(c, peso_tentativo, pesos, volatilidades, betas, correlaciones, L)
            if peso_final < L.peso_minimo_significativo:
                rechazados.append(
                    CandidatoRechazado(
                        c.ticker,
                        "agregarlo excedería la volatilidad o el beta máximo del portafolio, "
                        "incluso reducido al tamaño mínimo",
                    )
                )
                continue

            pesos[c.ticker] = peso_final
            volatilidades[c.ticker] = c.volatilidad
            betas[c.ticker] = c.beta
            sector_actual[c.sector] = sector_usado + peso_final
            presupuesto_invertible = round(presupuesto_invertible - peso_final, 6)
            n_posiciones += 1
            seleccionados.append(c)

        return self._armar_resultado(cartera_actual, seleccionados, pesos, volatilidades, betas, sector_actual, correlaciones, rechazados, L)

    @staticmethod
    def _atractivo(c: Candidate) -> float:
        """Retorno ajustado por riesgo (proxy tipo Sharpe) × calidad factorial
        normalizada. Nunca rankea por retorno esperado solo — eso es
        exactamente lo que el optimizador tiene prohibido hacer."""
        riesgo_ajustado = c.expected_return / max(c.volatilidad, 1e-6)
        return riesgo_ajustado * (c.score_factorial() / 100.0)

    @staticmethod
    def _correlacion_maxima(
        ticker: str, pesos: dict[str, float], correlaciones: dict[tuple[str, str], float],
    ) -> tuple[float, str | None]:
        maxima, con_ticker = 0.0, None
        for otro, w in pesos.items():
            if w <= 0:
                continue
            rho = correlacion(correlaciones, ticker, otro)
            if rho > maxima:
                maxima, con_ticker = rho, otro
        return maxima, con_ticker

    @staticmethod
    def _ajustar_por_riesgo_portafolio(
        c: Candidate,
        peso_tentativo: float,
        pesos: dict[str, float],
        volatilidades: dict[str, float],
        betas: dict[str, float],
        correlaciones: dict[tuple[str, str], float],
        L: OptimizerConstraints,
    ) -> float:
        """Si el peso tentativo excede el beta o la volatilidad máxima del
        portafolio, lo reduce en vez de rechazar de una — mismo patrón que
        risk_manager con la reserva mínima de cash."""
        paso = peso_tentativo / _PASOS_AJUSTE_RIESGO
        peso = peso_tentativo
        for _ in range(_PASOS_AJUSTE_RIESGO):
            pesos_prueba = {**pesos, c.ticker: peso}
            vols_prueba = {**volatilidades, c.ticker: c.volatilidad}
            betas_prueba = {**betas, c.ticker: c.beta}
            vol_p = volatilidad_portafolio(pesos_prueba, vols_prueba, correlaciones)
            beta_p = beta_portafolio(pesos_prueba, betas_prueba)
            if vol_p <= L.max_vol_portafolio and beta_p <= L.max_beta_portafolio:
                return peso
            peso -= paso
        return 0.0

    @staticmethod
    def _armar_resultado(
        cartera_actual: list[Holding],
        seleccionados: list[Candidate],
        pesos: dict[str, float],
        volatilidades: dict[str, float],
        betas: dict[str, float],
        sector_actual: dict[str, float],
        correlaciones: dict[tuple[str, str], float],
        rechazados: list[CandidatoRechazado],
        L: OptimizerConstraints,
    ) -> PortafolioOptimo:
        asignaciones = [
            Allocation(ticker=h.ticker, sector=h.sector, peso=h.peso, retorno_esperado=0.0, nueva=False)
            for h in cartera_actual
            if h.peso > 0
        ] + [
            Allocation(ticker=c.ticker, sector=c.sector, peso=pesos[c.ticker], retorno_esperado=c.expected_return, nueva=True)
            for c in seleccionados
        ]

        peso_cash = round(1.0 - sum(pesos.values()), 6)
        vol_p = volatilidad_portafolio(pesos, volatilidades, correlaciones)
        beta_p = beta_portafolio(pesos, betas)
        retorno_p = sum(pesos[c.ticker] * c.expected_return for c in seleccionados)
        div_score = score_diversificacion(pesos, correlaciones)

        resumen = (
            f"{len(pesos)} posiciones, cash {peso_cash:.1%} (mín. {L.min_cash_pct:.0%}), "
            f"vol esperada {vol_p:.1%} (tope {L.max_vol_portafolio:.0%}), "
            f"beta esperado {beta_p:.2f} (tope {L.max_beta_portafolio:.2f}), "
            f"diversificación {div_score:.2f}"
        )

        return PortafolioOptimo(
            asignaciones=asignaciones,
            peso_cash=peso_cash,
            retorno_esperado=round(retorno_p, 4),
            volatilidad_esperada=round(vol_p, 4),
            beta_esperado=round(beta_p, 4),
            exposicion_sectorial={k: round(v, 4) for k, v in sector_actual.items() if v > 0},
            score_diversificacion=div_score,
            rechazados=rechazados,
            resumen_riesgo=resumen,
        )
