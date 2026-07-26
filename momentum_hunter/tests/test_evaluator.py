"""Pruebas del árbol de decisión de Prompt 4 -- verifica el corte duro
de la pregunta 1 (catalizador), las penalizaciones de las preguntas 2-3,
y que "accionable" exige patrón + temprano + score por encima del
umbral, sin importar qué tan grande sea el score base."""

from __future__ import annotations

from momentum_hunter import evaluator as ev
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import CONFIG
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata


def _bi(n=10, ticker="TST") -> BarraIntradia:
    closes = [1.0] * n
    marcas = [f"2026-07-26T13:{30+i:02d}:00+00:00" for i in range(n)]
    return BarraIntradia(ticker, marcas, closes, closes, closes, closes, [1_000.0] * n)


def _factores_accionables() -> FactoresIntradia:
    # RVOL alto (dinero entrando), gap+ruptura de premarket (gap_and_go),
    # sin extensión ni velas excesivas -- debería terminar "temprano".
    return FactoresIntradia(
        precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=4.0,
        aceleracion_volumen=2.0, gap_pct=0.10, maximo_dia=5.25,
        maximo_premarket=5.00, velas_desde_ruptura=1,
    )


def _meta_desequilibrio() -> Metadata:
    return Metadata(ticker="TST", shares_float=5_000_000, short_pct_float=0.25)


def test_sin_catalizador_corta_el_analisis():
    r = ev.evaluar(None, None, _factores_accionables(), _bi(), Metadata(ticker="TST"),
                   5.20, 5.00, 5.60, 90.0, CONFIG)
    assert r.paso_detenido == "catalizador"
    assert r.accionable is False
    assert r.score_ajustado == 0.0


def test_catalizador_no_confirmado_tambien_corta():
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters", confirmado=False)
    r = ev.evaluar(c, 5.0, _factores_accionables(), _bi(), Metadata(ticker="TST"),
                   5.20, 5.00, 5.60, 90.0, CONFIG)
    assert r.paso_detenido == "catalizador"


def test_accionable_con_todo_a_favor():
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters")
    bi = _bi()
    r = ev.evaluar(c, 5.0, _factores_accionables(), bi, _meta_desequilibrio(),
                   5.20, 5.00, 5.60, 95.0, CONFIG)
    assert r.dinero_entrando is True
    assert r.desequilibrio is True
    assert r.patron == "gap_and_go"
    assert r.temprano is True
    assert r.accionable is True
    assert r.score_ajustado == 95.0  # sin penalizaciones


def test_penaliza_sin_bajar_a_cero_cuando_falta_dinero_o_desequilibrio():
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters")
    # RVOL 2.5x: suficiente para confirmar el patrón (umbral 2.0x en
    # classification.py) pero NO para "dinero entrando" del evaluador
    # (umbral 3.0x, cfg.umbral_rvol_intradia) -- aísla la penalización
    # de la pregunta 2 sin tumbar también la pregunta 4 (patrón).
    f = FactoresIntradia(precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=2.5,
                         gap_pct=0.10, maximo_premarket=5.00, velas_desde_ruptura=1)
    r = ev.evaluar(c, 5.0, f, _bi(), Metadata(ticker="TST"),  # sin desequilibrio (metadata vacía)
                   5.20, 5.00, 5.60, 95.0, CONFIG)
    assert r.dinero_entrando is False
    assert r.desequilibrio is False
    assert r.score_ajustado == 95.0 - ev.PENALIZACION_SIN_DINERO - ev.PENALIZACION_SIN_DESEQUILIBRIO
    assert len(r.penalizaciones) == 2


def test_sin_patron_no_es_accionable_aunque_el_score_sea_altisimo():
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters")
    f = FactoresIntradia(precio_actual=5.20, rvol_actual=4.0)  # nada que arme un patrón
    r = ev.evaluar(c, 5.0, f, _bi(), _meta_desequilibrio(), 5.20, 5.00, 5.60, 100.0, CONFIG)
    assert r.patron is None
    assert r.accionable is False
    assert r.score_ajustado == 0.0  # 100 - 100 (PENALIZACION_SIN_PATRON)


def test_tarde_no_es_accionable_aunque_el_score_sea_altisimo():
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters")
    # Patrón gap_and_go válido, pero extendido 30% de VWAP -- "tarde".
    f = FactoresIntradia(precio_actual=6.50, vwap=5.00, ema9=5.00, rvol_actual=4.0,
                         gap_pct=0.10, maximo_premarket=5.00, velas_desde_ruptura=1)
    r = ev.evaluar(c, 5.0, f, _bi(), _meta_desequilibrio(), 6.50, 6.00, 7.50, 100.0, CONFIG)
    assert r.patron == "gap_and_go"
    assert r.temprano is False
    assert r.accionable is False


def test_score_ajustado_nunca_es_negativo():
    # score_base bajo (5.0) con TODAS las penalizaciones aplicadas (135 en
    # total) -- sin el max(0, ...) del clamp, esto daría un número negativo.
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters")
    r = ev.evaluar(c, None, FactoresIntradia(), _bi(), Metadata(ticker="TST"),
                   1.0, None, None, 5.0, CONFIG)
    assert r.score_ajustado >= 0.0
