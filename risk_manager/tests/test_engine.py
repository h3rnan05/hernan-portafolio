"""Pruebas del Risk Manager. Todo sintético, determinístico, sin red — cada
regla de veto se prueba de forma aislada, más los casos de aprobación."""

from __future__ import annotations

import pytest

from risk_manager.config import RiskLimits
from risk_manager.engine import RiskManager
from risk_manager.models import PortfolioState, Posicion, TradeCandidate


def _portafolio(equity=44_000.0, cash=None, posiciones=None) -> PortfolioState:
    return PortfolioState(equity=equity, cash=cash if cash is not None else equity,
                          posiciones=posiciones or {})


def _rm(**overrides) -> RiskManager:
    return RiskManager(RiskLimits(**overrides))


# ------------------------- aprobación básica -------------------------

def test_aprueba_y_calcula_sizing_por_riesgo():
    rm = _rm(riesgo_por_trade_pct=0.01)  # 1% de $44,000 = $440
    cand = TradeCandidate("AAPL", precio=200.0, atr=5.0)  # stop=190, riesgo/acc=10
    v = rm.evaluar(cand, _portafolio())
    assert v.aprobado
    assert v.qty == 44  # $440 / $10 = 44 acciones
    assert v.stop == 190.0
    assert v.riesgo_usd == pytest.approx(440.0, abs=1.0)


def test_riesgo_pct_real_no_excede_el_objetivo():
    rm = _rm(riesgo_por_trade_pct=0.01)
    cand = TradeCandidate("X", precio=100.0, atr=3.0)
    v = rm.evaluar(cand, _portafolio(equity=10_000.0, cash=10_000.0))
    assert v.aprobado
    assert v.riesgo_pct_equity <= 0.01 + 1e-6


# ------------------------- vetos, uno por regla -------------------------

def test_veto_nunca_promediar():
    rm = _rm()
    pos = {"AAPL": Posicion(qty=10, entrada=190.0, stop=180.0)}
    v = rm.evaluar(TradeCandidate("AAPL", 200.0, 5.0), _portafolio(posiciones=pos))
    assert not v.aprobado and "promediar" in v.motivo


def test_veto_max_posiciones():
    rm = _rm(max_posiciones=2)
    pos = {"A": Posicion(1, 10, 9), "B": Posicion(1, 10, 9)}
    v = rm.evaluar(TradeCandidate("C", 50.0, 2.0), _portafolio(posiciones=pos))
    assert not v.aprobado and "máximo de posiciones" in v.motivo


def test_veto_atr_invalido():
    rm = _rm()
    v = rm.evaluar(TradeCandidate("X", 100.0, 0.0), _portafolio())
    assert not v.aprobado and "inválidos" in v.motivo


def test_veto_calor_maximo():
    rm = _rm(riesgo_por_trade_pct=0.5, max_heat_pct=0.05)  # riesgo objetivo enorme
    # posición existente ya usa casi todo el calor permitido
    pos = {"A": Posicion(qty=100, entrada=100.0, stop=99.5)}  # riesgo=$50 de equity=$10,000 -> 0.5%
    port = _portafolio(equity=10_000.0, cash=10_000.0, posiciones=pos)
    cand = TradeCandidate("B", precio=100.0, atr=50.0)  # stop=0, riesgo/acc=100 -> muy caro
    v = rm.evaluar(cand, port)
    assert not v.aprobado and "calor máximo" in v.motivo


def test_veto_sector_maximo():
    rm = _rm(max_sector_pct=0.20, riesgo_por_trade_pct=0.5, max_posicion_pct=1.0,
             max_heat_pct=1.0)  # calor sin límite práctico: aísla la regla de sector
    pos = {"XOM": Posicion(qty=15, entrada=100.0, stop=90.0, sector="Energy")}  # $1,500 de $10,000 = 15%
    port = _portafolio(equity=10_000.0, cash=10_000.0, posiciones=pos)
    cand = TradeCandidate("CVX", precio=100.0, atr=5.0, sector="Energy")
    v = rm.evaluar(cand, port)
    assert not v.aprobado and "sector" in v.motivo


def test_sin_sector_no_aplica_veto_sectorial():
    """Si el candidato no trae sector, esa regla simplemente no se evalúa."""
    rm = _rm(max_sector_pct=0.01)  # tope absurdamente bajo
    v = rm.evaluar(TradeCandidate("X", 100.0, 3.0, sector=None), _portafolio())
    assert v.aprobado  # no se rechaza por sector porque no se conoce


def test_reserva_minima_reduce_qty_en_vez_de_rechazar():
    """Con poco cash disponible, se compra lo que la reserva permite, no cero."""
    rm = _rm(riesgo_por_trade_pct=0.5, max_posicion_pct=1.0, min_cash_pct=0.90,
             max_heat_pct=1.0)  # calor sin límite práctico: aísla la regla de reserva
    port = _portafolio(equity=10_000.0, cash=10_000.0)
    cand = TradeCandidate("X", precio=10.0, atr=1.0)  # sizing crudo pediría mucho volumen
    v = rm.evaluar(cand, port)
    assert v.aprobado
    # con min_cash_pct=0.90, solo se puede gastar hasta $1,000
    assert v.qty * 10.0 <= 1000.0 + 1e-6


def test_reserva_minima_rechaza_si_no_alcanza_ni_una_accion():
    rm = _rm(min_cash_pct=0.9999)
    v = rm.evaluar(TradeCandidate("X", 1000.0, 10.0), _portafolio(equity=10_000.0, cash=10_000.0))
    assert not v.aprobado and "reserva mínima" in v.motivo


def test_cash_insuficiente_limita_qty_sin_agotarlo():
    """Con poco cash (pero suficiente para 2 acciones), el tope de cash gana
    sobre el sizing por riesgo, y la reserva mínima no interfiere (0%)."""
    rm = _rm(riesgo_por_trade_pct=0.5, max_posicion_pct=1.0, min_cash_pct=0.0,
             max_heat_pct=1.0)
    port = _portafolio(equity=10_000.0, cash=250.0)  # alcanza para 2 acciones a $100
    v = rm.evaluar(TradeCandidate("X", 100.0, 5.0), port)
    assert v.aprobado
    assert v.qty == 2  # int(250 / 100), no las ~500 que pediría el riesgo puro


def test_cash_insuficiente_para_ni_una_accion_rechaza():
    rm = _rm(riesgo_por_trade_pct=0.5, max_posicion_pct=1.0, min_cash_pct=0.0,
             max_heat_pct=1.0)
    port = _portafolio(equity=10_000.0, cash=50.0)  # no alcanza ni 1 acción a $100
    v = rm.evaluar(TradeCandidate("X", 100.0, 5.0), port)
    assert not v.aprobado and "0 acciones" in v.motivo


def test_tope_notional_limita_posicion():
    rm = _rm(riesgo_por_trade_pct=1.0, max_posicion_pct=0.10, min_cash_pct=0.0)
    port = _portafolio(equity=10_000.0, cash=10_000.0)
    cand = TradeCandidate("X", precio=100.0, atr=1.0)  # riesgo/acc chico -> sizing crudo enorme
    v = rm.evaluar(cand, port)
    assert v.aprobado
    assert v.qty * 100.0 <= 1_000.0 + 1e-6  # 10% de $10,000


# ------------------------- config -------------------------

def test_limites_invalidos_explotan_temprano():
    with pytest.raises(ValueError):
        RiskManager(RiskLimits(riesgo_por_trade_pct=1.5))  # >100%
    with pytest.raises(ValueError):
        RiskManager(RiskLimits(max_posiciones=0))
