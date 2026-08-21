"""Pruebas del banco de pruebas sobre la auditoría -- sin red, con
archivos de auditoría fabricados en tmp_path."""

from __future__ import annotations

import json

from momentum_hunter.replay import (
    Evaluacion,
    barrido,
    cargar_evaluaciones,
    dias_auditados,
    formatear_barrido,
    simular,
)


def _ev(ticker="X", score=70.0, patron="gap_and_go", temprano=True, riesgo=True, dia="2026-08-21"):
    return Evaluacion(dia=dia, timestamp=f"{dia}T14:00:00+00:00", ticker=ticker,
                       score_ajustado=score, patron=patron, temprano=temprano,
                       riesgo_definido=riesgo)


def _escribir_auditoria(tmp_path, dia, candidatos):
    (tmp_path / f"{dia}.json").write_text(json.dumps({"corridas": [
        {"timestamp": f"{dia}T14:00:00+00:00", "candidatos": candidatos},
    ]}))


# ------------------------- carga -------------------------

def test_cargar_directorio_inexistente_no_lanza(tmp_path):
    assert cargar_evaluaciones(tmp_path / "no_existe") == []


def test_cargar_lee_todos_los_dias(tmp_path):
    for dia in ("2026-08-20", "2026-08-21"):
        _escribir_auditoria(tmp_path, dia, [{"ticker": "AAA", "evaluacion": {
            "score_ajustado": 70.0, "patron": "gap_and_go", "temprano": True,
            "riesgo_definido": True}}])
    evs = cargar_evaluaciones(tmp_path)
    assert len(evs) == 2
    assert dias_auditados(evs) == 2


def test_cargar_omite_archivo_corrupto_sin_tumbar_el_resto(tmp_path):
    _escribir_auditoria(tmp_path, "2026-08-20", [{"ticker": "AAA", "evaluacion": {
        "score_ajustado": 70.0, "patron": "gap_and_go", "temprano": True, "riesgo_definido": True}}])
    (tmp_path / "2026-08-21.json").write_text("{roto")
    assert len(cargar_evaluaciones(tmp_path)) == 1


def test_cargar_omite_candidatos_sin_evaluacion(tmp_path):
    _escribir_auditoria(tmp_path, "2026-08-20", [
        {"ticker": "AAA"},                       # sin clave 'evaluacion'
        {"ticker": "BBB", "evaluacion": None},   # nula
        {"ticker": "CCC", "evaluacion": {"score_ajustado": 70.0, "patron": "x",
                                          "temprano": True, "riesgo_definido": True}},
    ])
    evs = cargar_evaluaciones(tmp_path)
    assert [e.ticker for e in evs] == ["CCC"]


def test_cargar_registro_viejo_sin_riesgo_definido_queda_en_none(tmp_path):
    # Los datos anteriores al 2026-08-21 no tienen el campo -- debe
    # quedar como "no se sabe", nunca asumirse True.
    _escribir_auditoria(tmp_path, "2026-08-20", [{"ticker": "AAA", "evaluacion": {
        "score_ajustado": 70.0, "patron": "gap_and_go", "temprano": True}}])
    assert cargar_evaluaciones(tmp_path)[0].riesgo_definido is None


# ------------------------- simulación -------------------------

def test_simular_cuenta_la_que_pasa_todo():
    r = simular([_ev(score=70.0)], umbral=60.0)
    assert r.alertas == 1
    assert r.tickers == ("X",)
    assert r.indeterminadas == 0


def test_simular_descarta_por_debajo_del_umbral():
    assert simular([_ev(score=59.9)], umbral=60.0).alertas == 0


def test_simular_incluye_el_umbral_exacto():
    assert simular([_ev(score=60.0)], umbral=60.0).alertas == 1


def test_simular_exige_patron():
    assert simular([_ev(patron=None)], umbral=60.0).alertas == 0


def test_simular_exige_temprano():
    assert simular([_ev(temprano=False)], umbral=60.0).alertas == 0


def test_simular_exige_riesgo_definido():
    assert simular([_ev(riesgo=False)], umbral=60.0).alertas == 0


def test_riesgo_desconocido_no_cuenta_como_alerta_ni_como_descarte():
    # El punto central: sobre datos viejos, "no se sabe" es su propia
    # categoría. Asumirlo True fue el supuesto no declarado que infló el
    # análisis a mano del 2026-08-21.
    r = simular([_ev(riesgo=None)], umbral=60.0)
    assert r.alertas == 0
    assert r.indeterminadas == 1


def test_riesgo_desconocido_solo_cuenta_si_pasa_lo_demas():
    # Si ya falla por patrón, no es "indeterminada" -- es un descarte claro.
    r = simular([_ev(riesgo=None, patron=None)], umbral=60.0)
    assert r.alertas == 0 and r.indeterminadas == 0


def test_simular_cuenta_dias_y_tickers_distintos():
    evs = [_ev("AAA", dia="2026-08-20"), _ev("AAA", dia="2026-08-21"), _ev("BBB", dia="2026-08-21")]
    r = simular(evs, umbral=60.0)
    assert r.alertas == 3
    assert r.tickers == ("AAA", "BBB")
    assert r.dias == 2


def test_score_none_no_alerta():
    assert simular([_ev(score=None)], umbral=60.0).alertas == 0


def test_umbral_85_reproduce_el_cero_historico():
    # Regresión del bug real: con el umbral viejo, ni la mejor candidata
    # observada (81,2) alertaba.
    assert simular([_ev(score=81.2)], umbral=85.0).alertas == 0


# ------------------------- presentación -------------------------

def test_barrido_devuelve_un_resultado_por_umbral():
    assert len(barrido([_ev()], (50.0, 60.0, 70.0))) == 3


def test_formatear_muestra_rango_cuando_hay_indeterminadas():
    texto = formatear_barrido(barrido([_ev(riesgo=None)], (60.0,)), dias=12)
    assert "0 - 1" in texto


def test_formatear_muestra_numero_solo_cuando_no_hay_indeterminadas():
    texto = formatear_barrido(barrido([_ev(riesgo=True)], (60.0,)), dias=12)
    assert "0 - 1" not in texto
    assert " 1" in texto


def test_formatear_sin_dias_no_divide_por_cero():
    formatear_barrido(barrido([_ev()], (60.0,)), dias=0)   # no debe lanzar
