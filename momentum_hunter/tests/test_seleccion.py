"""Pruebas de `run.seleccionar_y_auditar` -- el tramo final del pipeline:
competencia relativa + abogado del diablo + memoria + auditoría, todo
inyectado (sin red, sin reloj real, sin tocar el tracker del repo)."""

from __future__ import annotations

from dataclasses import replace

from momentum_hunter import audit
from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.early_opportunity import EarlyOpportunity
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata
from momentum_hunter.run import seleccionar_y_auditar

CFG = MomentumConfig()
HORA_MEDIA_SESION = 15.0


def _bi(ticker) -> BarraIntradia:
    return BarraIntradia(ticker, ["2026-07-27T13:30:00+00:00"], [5.2], [5.2], [5.25], [5.15], [5000.0])


def _candidato(ticker, accionable=True, score=90.0, con_stop=True) -> CandidatoIntradia:
    # Con vwap/ema9 por debajo del precio hay stop (~2% de riesgo); con
    # con_stop=False se quitan las anclas Y el ATR diario (el fallback),
    # así niveles_entrada_salida da stop=None -> el abogado del diablo
    # la mata con "sin_salida".
    factores = FactoresIntradia(
        precio_actual=5.20,
        vwap=5.10 if con_stop else None, ema9=5.10 if con_stop else None,
        rvol_actual=4.0, aceleracion_volumen=1.5, gap_pct=0.10,
        maximo_premarket=5.00, maximo_dia=5.25, velas_desde_ruptura=1,
    )
    early = EarlyOpportunity(score=90.0, veredicto="temprano", razon="ok", motivo_veredicto="x")
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True,
        patron="gap_and_go" if accionable else None, temprano=True,
        early=early, penalizaciones=[] if accionable else ["No hay un patrón técnico claro formándose todavía."],
        score_base=score, score_ajustado=score if accionable else 0.0, accionable=accionable,
    )
    return CandidatoIntradia(
        ticker=ticker, nombre=None,
        catalizador=Catalizador(tipo="fda", titular="x", fuente="Reuters"),
        minutos_desde_catalizador=10.0, factores=factores, bi_hoy=_bi(ticker),
        meta=Metadata(ticker=ticker), atr_diario=0.30 if con_stop else None,
        resultado=resultado,
    )


def test_solo_la_mejor_gana_la_competencia_relativa():
    candidatos = [_candidato("SEGUNDA", score=90.0), _candidato("MEJOR", score=99.0)]
    oportunidades, vetadas, snapshots = seleccionar_y_auditar(
        candidatos, CFG, historial=[], hora_utc=HORA_MEDIA_SESION)
    assert [o.ticker for o in oportunidades] == ["MEJOR"]
    assert vetadas == {}
    decisiones = {s["ticker"]: s["decision"] for s in snapshots}
    assert decisiones["MEJOR"] == audit.DECISION_ALERTADA
    assert decisiones["SEGUNDA"] == audit.DECISION_PERDIO_COMPETENCIA


def test_si_la_mejor_muere_en_el_debate_la_siguiente_hereda_el_turno():
    candidatos = [_candidato("MEJOR_SIN_SALIDA", score=99.0, con_stop=False),
                  _candidato("SEGUNDA", score=90.0)]
    oportunidades, vetadas, snapshots = seleccionar_y_auditar(
        candidatos, CFG, historial=[], hora_utc=HORA_MEDIA_SESION)
    assert [o.ticker for o in oportunidades] == ["SEGUNDA"]
    assert "MEJOR_SIN_SALIDA" in vetadas
    assert "salida clara" in vetadas["MEJOR_SIN_SALIDA"]
    decisiones = {s["ticker"]: s["decision"] for s in snapshots}
    assert decisiones["MEJOR_SIN_SALIDA"] == audit.DECISION_VETADA


def test_descartadas_llevan_que_tendria_que_cambiar():
    candidatos = [_candidato("SIN_PATRON", accionable=False)]
    oportunidades, _, snapshots = seleccionar_y_auditar(
        candidatos, CFG, historial=[], hora_utc=HORA_MEDIA_SESION)
    assert oportunidades == []
    s = snapshots[0]
    assert s["decision"] == audit.DECISION_DESCARTADA
    assert any("figuras" in c for c in s["que_tendria_que_cambiar"])


def test_advertencias_del_debate_viajan_al_mensaje():
    candidatos = [_candidato("ACME")]
    oportunidades, _, _ = seleccionar_y_auditar(
        candidatos, CFG, historial=[], hora_utc=19.5)  # última hora de sesión
    assert len(oportunidades) == 1
    assert any("menos de una hora" in a for a in oportunidades[0].advertencias)


def test_sin_historial_la_probabilidad_es_honesta():
    oportunidades, _, _ = seleccionar_y_auditar(
        [_candidato("ACME")], CFG, historial=[], hora_utc=HORA_MEDIA_SESION)
    assert "no puedo darte una probabilidad honesta" in oportunidades[0].probabilidad_historica


def test_n_evaluados_refleja_la_competencia():
    candidatos = [_candidato("A", score=99.0), _candidato("B", score=90.0),
                  _candidato("C", accionable=False)]
    oportunidades, _, _ = seleccionar_y_auditar(
        candidatos, CFG, historial=[], hora_utc=HORA_MEDIA_SESION)
    assert oportunidades[0].n_evaluados == 3


def test_todos_los_candidatos_quedan_en_la_auditoria():
    candidatos = [_candidato("A", score=99.0), _candidato("B", score=90.0),
                  _candidato("C", accionable=False), _candidato("D", score=95.0, con_stop=False)]
    _, _, snapshots = seleccionar_y_auditar(
        candidatos, CFG, historial=[], hora_utc=HORA_MEDIA_SESION)
    assert {s["ticker"] for s in snapshots} == {"A", "B", "C", "D"}


# --------- Refinamiento "Head Trader" (2026-07-27, segunda ronda) ---------

def test_ranking_absoluto_llega_a_la_oportunidad():
    oportunidades, _, _ = seleccionar_y_auditar(
        [_candidato("ACME")], CFG, historial=[], hora_utc=HORA_MEDIA_SESION, n_universo=4300)
    assert oportunidades[0].rank == 1
    assert oportunidades[0].n_universo == 4300


def test_confianza_y_calidad_llegan_a_la_oportunidad():
    oportunidades, _, _ = seleccionar_y_auditar(
        [_candidato("ACME")], CFG, historial=[], hora_utc=HORA_MEDIA_SESION)
    o = oportunidades[0]
    assert "Confianza: Baja" in o.confianza_texto      # sin historial -> honesto
    assert "sin calificar todavía" in o.calidad_historica
    assert o.ventana_texto                                # la hora se pasó, hay ventana


def test_ultima_pregunta_veta_con_demasiadas_dudas():
    # última hora (1 duda) + noticia fría (2) + volumen enfriándose (3)
    # -> más de MAX_DUDAS_PARA_SI_CLARO: no es un "sí claro".
    c = _candidato("DUDOSA")
    factores = replace(c.factores, aceleracion_volumen=0.85)
    c = replace(c, factores=factores, minutos_desde_catalizador=200.0)
    oportunidades, vetadas, snapshots = seleccionar_y_auditar(
        [c], CFG, historial=[], hora_utc=19.5)
    assert oportunidades == []
    assert "DUDOSA" in vetadas
    assert "sí claro" in vetadas["DUDOSA"]
    decisiones = {s["ticker"]: s["decision"] for s in snapshots}
    assert decisiones["DUDOSA"] == audit.DECISION_SIN_CONVICCION


def test_dos_dudas_todavia_es_si_claro():
    # última hora (1) + noticia fría (2) = exactamente el máximo tolerado.
    c = replace(_candidato("ACME"), minutos_desde_catalizador=200.0)
    oportunidades, vetadas, _ = seleccionar_y_auditar(
        [c], CFG, historial=[], hora_utc=19.5)
    assert len(oportunidades) == 1
    assert vetadas == {}
