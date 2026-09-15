from __future__ import annotations

import pytest

from momentum_paper_trader.config import PaperTraderConfig, banda_de


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


def test_por_defecto_solo_se_opera_small_cap():
    # La tesis es small caps. Si alguien cambia este default, que sea a
    # propósito y que un test lo obligue a decirlo.
    assert PaperTraderConfig().bandas_operables == ("small",)


def test_bandas_operables_vacio_es_invalido():
    # Vacío significaría "no operar nunca nada" en silencio -- mejor
    # fallar fuerte al arrancar.
    with pytest.raises(ValueError):
        PaperTraderConfig(bandas_operables=()).validar()


def test_banda_desconocida_es_invalida():
    with pytest.raises(ValueError):
        PaperTraderConfig(bandas_operables=("small", "mid")).validar()


def test_las_dos_bandas_es_valido_y_es_el_revert():
    PaperTraderConfig(bandas_operables=("small", "large")).validar()


def test_banda_de_none_es_small():
    # Entrada anterior al campo `es_large_cap`: la banda de siempre.
    assert banda_de(None) == "small"
    assert banda_de(False) == "small"
    assert banda_de(True) == "large"
