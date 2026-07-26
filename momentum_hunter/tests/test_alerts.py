"""Pruebas del filtro en dos etapas -- etapa 1 (recorte a candidatos con
catalizador para pasar a intradía) y etapa 2 (accionable, según ya lo
decidió `evaluator.evaluar`)."""

from __future__ import annotations

from momentum_hunter.alerts import (
    CandidatoDiario,
    CandidatoIntradia,
    candidatos_para_etapa_intradia,
    filtrar_alertas,
)
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion

CFG = MomentumConfig()


def _bi(ticker="TST") -> BarraIntradia:
    return BarraIntradia(ticker, ["2026-07-26T13:30:00+00:00"], [1.0], [1.0], [1.0], [1.0], [100.0])


def _candidato_diario(ticker="TST", score=90.0, con_catalizador=True) -> CandidatoDiario:
    catalizador = Catalizador(tipo="fda", titular="x", fuente="Reuters") if con_catalizador else None
    return CandidatoDiario(
        ticker=ticker, nombre=None, precio=10.0, volumen_promedio=500_000.0,
        factores=FactoresMomentum(), catalizador=catalizador, meta=Metadata(ticker=ticker),
        puntuacion=Puntuacion(ticker=ticker, score_total=score, sub={}),
    )


def _candidato_intradia(ticker="TST", accionable=True, score_ajustado=90.0) -> CandidatoIntradia:
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True, patron="gap_and_go",
        temprano=True, early=None, penalizaciones=[], score_base=90.0,
        score_ajustado=score_ajustado, accionable=accionable,
    )
    return CandidatoIntradia(
        ticker=ticker, nombre=None, catalizador=Catalizador(tipo="fda", titular="x", fuente="Reuters"),
        minutos_desde_catalizador=5.0, factores=FactoresIntradia(), bi_hoy=_bi(ticker),
        meta=Metadata(ticker=ticker), atr_diario=None, resultado=resultado,
    )


def test_candidatos_para_etapa_intradia_exige_catalizador():
    con = _candidato_diario("CON", con_catalizador=True)
    sin = _candidato_diario("SIN", con_catalizador=False)
    resultado = candidatos_para_etapa_intradia([con, sin], CFG)
    assert [c.ticker for c in resultado] == ["CON"]


def test_candidatos_para_etapa_intradia_ordena_por_score_y_recorta():
    cfg = MomentumConfig(max_candidatos_intradia=2)
    candidatos = [_candidato_diario(f"T{i}", score=s) for i, s in enumerate([50.0, 99.0, 70.0])]
    resultado = candidatos_para_etapa_intradia(candidatos, cfg)
    assert [c.ticker for c in resultado] == ["T1", "T2"]


def test_filtrar_alertas_solo_accionables():
    accionable = _candidato_intradia("BUENO", accionable=True)
    no_accionable = _candidato_intradia("MALO", accionable=False)
    resultado = filtrar_alertas([accionable, no_accionable], CFG)
    assert [c.ticker for c in resultado] == ["BUENO"]


def test_filtrar_alertas_ordena_por_score_ajustado_y_recorta():
    cfg = MomentumConfig(limite_diario_alertas=2)
    candidatos = [
        _candidato_intradia("T0", score_ajustado=90.0),
        _candidato_intradia("T1", score_ajustado=99.0),
        _candidato_intradia("T2", score_ajustado=86.0),
    ]
    resultado = filtrar_alertas(candidatos, cfg)
    assert [c.ticker for c in resultado] == ["T1", "T0"]
