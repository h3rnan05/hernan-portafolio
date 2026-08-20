from __future__ import annotations

import pytest

from momentum_paper_trader.config import PaperTraderConfig


def test_config_default_es_valida():
    PaperTraderConfig().validar()   # no debe lanzar


def test_riesgo_debe_ser_positivo():
    with pytest.raises(ValueError):
        PaperTraderConfig(riesgo_dolares_por_operacion=0).validar()


def test_riesgo_negativo_invalido():
    with pytest.raises(ValueError):
        PaperTraderConfig(riesgo_dolares_por_operacion=-50).validar()


def test_minimo_acciones_debe_ser_al_menos_uno():
    with pytest.raises(ValueError):
        PaperTraderConfig(minimo_acciones=0).validar()
