"""Pruebas de validación de configuración -- mismos invariantes que
`screener/config.py`: los pesos deben sumar 1.0 y los umbrales deben
tener sentido."""

from __future__ import annotations

import pytest

from momentum_hunter.config import CONFIG, MomentumConfig


def test_config_default_es_valida():
    CONFIG.validar()  # no debe lanzar


def test_pesos_deben_sumar_uno():
    cfg = MomentumConfig(pesos={"momentum": 0.5, "catalizador": 0.5, "liquidez": 0.5, "riesgo": 0.0})
    with pytest.raises(ValueError):
        cfg.validar()


def test_precio_min_debe_ser_menor_que_precio_max():
    cfg = MomentumConfig(precio_min=20.0, precio_max=10.0)
    with pytest.raises(ValueError):
        cfg.validar()


def test_score_minimo_fuera_de_rango():
    cfg = MomentumConfig(score_minimo_alerta=150.0)
    with pytest.raises(ValueError):
        cfg.validar()


def test_limite_diario_debe_ser_al_menos_uno():
    cfg = MomentumConfig(limite_diario_alertas=0)
    with pytest.raises(ValueError):
        cfg.validar()


def test_max_candidatos_intradia_debe_ser_al_menos_uno():
    cfg = MomentumConfig(max_candidatos_intradia=0)
    with pytest.raises(ValueError):
        cfg.validar()


def test_extension_maxima_debe_ser_positiva():
    cfg = MomentumConfig(extension_maxima_pct=0.0)
    with pytest.raises(ValueError):
        cfg.validar()


def test_velas_maximas_desde_patron_debe_ser_al_menos_uno():
    cfg = MomentumConfig(velas_maximas_desde_patron=0)
    with pytest.raises(ValueError):
        cfg.validar()
