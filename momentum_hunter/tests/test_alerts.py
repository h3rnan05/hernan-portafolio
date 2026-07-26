"""Pruebas del filtro de envío -- las CUATRO condiciones del Prompt 7
son todas obligatorias, y el tope diario recorta por score."""

from __future__ import annotations

from dataclasses import replace

from momentum_hunter.alerts import Candidato, califica_para_alerta, filtrar_alertas
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion

CFG = MomentumConfig()


def _candidato(ticker="TST", score=90.0, rvol=5.0, catalizador_confirmado=True,
              vol_prom=500_000.0) -> Candidato:
    catalizador = (
        Catalizador(tipo="fda", titular="x", fuente="Reuters", confirmado=catalizador_confirmado)
        if catalizador_confirmado is not None else None
    )
    return Candidato(
        ticker=ticker, nombre=None, precio=10.0, volumen_promedio=vol_prom,
        factores=FactoresMomentum(rvol=rvol), catalizador=catalizador,
        meta=Metadata(ticker=ticker),
        puntuacion=Puntuacion(ticker=ticker, score_total=score, sub={}),
    )


def test_califica_con_las_cuatro_condiciones():
    assert califica_para_alerta(_candidato(), CFG) is True


def test_no_califica_con_score_bajo():
    assert califica_para_alerta(_candidato(score=50.0), CFG) is False


def test_no_califica_sin_catalizador():
    c = replace(_candidato(), catalizador=None)
    assert califica_para_alerta(c, CFG) is False


def test_no_califica_con_catalizador_no_confirmado():
    assert califica_para_alerta(_candidato(catalizador_confirmado=False), CFG) is False


def test_no_califica_con_rvol_bajo():
    assert califica_para_alerta(_candidato(rvol=1.0), CFG) is False


def test_no_califica_con_liquidez_insuficiente():
    assert califica_para_alerta(_candidato(vol_prom=1_000.0), CFG) is False


def test_filtrar_alertas_ordena_por_score_y_recorta_al_tope():
    cfg = MomentumConfig(limite_diario_alertas=2)
    candidatos = [_candidato(ticker=f"T{i}", score=s) for i, s in enumerate([90.0, 99.0, 86.0, 95.0])]
    resultado = filtrar_alertas(candidatos, cfg)
    assert [c.ticker for c in resultado] == ["T1", "T3"]


def test_filtrar_alertas_excluye_los_que_no_califican():
    candidatos = [_candidato(ticker="BUENO"), _candidato(ticker="MALO", score=10.0)]
    resultado = filtrar_alertas(candidatos, CFG)
    assert [c.ticker for c in resultado] == ["BUENO"]
