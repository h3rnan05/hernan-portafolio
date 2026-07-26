"""Pruebas del Early Opportunity Engine (Prompt 2) -- sobre todo, que el
VEREDICTO nunca dependa del score compuesto (un score alto no debe
poder "rescatar" una entrada tardía)."""

from __future__ import annotations

from momentum_hunter import early_opportunity as eo
from momentum_hunter.config import CONFIG
from momentum_hunter.models import FactoresIntradia


def _factores(**kwargs) -> FactoresIntradia:
    return FactoresIntradia(**kwargs)


def test_temprano_con_todo_a_favor():
    f = _factores(vwap=10.0, ema9=10.0, aceleracion_volumen=2.0, precio_actual=10.1,
                 maximo_dia=10.2, velas_desde_ruptura=1)
    r = eo.calcular(10.0, f, entrada=10.1, stop=9.9, objetivo=10.5, cfg=CONFIG)
    assert r.veredicto == "temprano"
    assert r.score > 0


def test_tarde_por_extension_aunque_el_resto_sea_perfecto():
    # Todo perfecto MENOS la extensión: 20% lejos de VWAP/EMA9 (> extension_maxima_pct=0.12).
    f = _factores(vwap=8.0, ema9=8.0, aceleracion_volumen=3.0, precio_actual=10.0,
                 maximo_dia=10.0, velas_desde_ruptura=1)
    r = eo.calcular(5.0, f, entrada=10.0, stop=9.5, objetivo=11.5, cfg=CONFIG)
    assert r.veredicto == "tarde"
    assert "lejos de VWAP" in r.motivo_veredicto


def test_tarde_por_velas_desde_patron_aunque_no_este_extendido():
    f = _factores(vwap=10.0, ema9=10.0, aceleracion_volumen=2.0, precio_actual=10.05,
                 maximo_dia=10.1, velas_desde_ruptura=CONFIG.velas_maximas_desde_patron + 1)
    r = eo.calcular(5.0, f, entrada=10.05, stop=9.9, objetivo=10.5, cfg=CONFIG)
    assert r.veredicto == "tarde"
    assert "velas" in r.motivo_veredicto


def test_score_alto_no_cambia_el_veredicto_tarde():
    """El caso central de Prompt 2: aunque casi todos los componentes
    puntúen alto (score compuesto alto), la regla dura de extensión
    igual dice 'tarde'."""
    f = _factores(vwap=8.0, ema9=8.0, aceleracion_volumen=5.0, precio_actual=10.0,
                 maximo_dia=10.0, velas_desde_ruptura=1)
    r = eo.calcular(2.0, f, entrada=10.0, stop=9.0, objetivo=14.0, cfg=CONFIG)
    assert r.veredicto == "tarde"


def test_extension_pct_toma_la_mayor_distancia_disponible():
    f = _factores(precio_actual=10.0, vwap=9.5, ema9=8.0)
    # distancia a vwap = 5.26%, distancia a ema9 = 25% -- debe tomar la mayor.
    assert abs(eo.extension_pct(f) - 0.25) < 1e-6


def test_extension_pct_none_sin_datos():
    assert eo.extension_pct(_factores()) is None


def test_score_se_renormaliza_sin_datos_de_riesgo_recompensa():
    f = _factores(vwap=10.0, ema9=10.0, precio_actual=10.0)
    r = eo.calcular(None, f, entrada=10.0, stop=None, objetivo=None, cfg=CONFIG)
    assert "riesgo_recompensa" not in r.sub
    assert r.score >= 0.0


def test_sin_ningun_dato_score_cero_y_temprano_por_falta_de_evidencia():
    # Sin extensión ni velas medibles, no hay regla dura que diga "tarde"
    # -- el veredicto por defecto es "temprano" (no se inventa un "tarde").
    r = eo.calcular(None, _factores(), entrada=10.0, stop=None, objetivo=None, cfg=CONFIG)
    assert r.veredicto == "temprano"
    assert r.score == 0.0
