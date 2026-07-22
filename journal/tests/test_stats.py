from __future__ import annotations

from journal.stats import calcular_estadisticas, calcular_estadisticas_por_estrategia
from journal.store import abrir_entrada, cerrar_entrada


def _abrir(ticker="BNY", estrategia="Bull Call Spread", riesgo=100.0):
    return abrir_entrada(
        ticker=ticker, score_screener=80.0, posicion_shortlist=1, tesis="Alcista",
        estrategia=estrategia, patas=[], costo=riesgo, riesgo_maximo=riesgo,
        ganancia_maxima=200.0, probabilidad_exito=0.5, valor_esperado=10.0, motivo="test",
    )


def _cerrada(resultado, ticker="BNY", estrategia="Bull Call Spread", riesgo=100.0):
    entradas = [_abrir(ticker=ticker, estrategia=estrategia, riesgo=riesgo)]
    cerrar_entrada(entradas, entradas[0].id, resultado=resultado)
    return entradas[0]


def test_calcular_estadisticas_vacio():
    s = calcular_estadisticas([])
    assert s.n_operaciones == 0
    assert s.win_rate is None
    assert s.pnl_total == 0.0
    assert s.drawdown_maximo == 0.0


def test_calcular_estadisticas_ignora_operaciones_abiertas():
    abiertas = [_abrir()]
    s = calcular_estadisticas(abiertas)
    assert s.n_operaciones == 0


def test_calcular_estadisticas_win_rate_y_promedios():
    entradas = [_cerrada(100.0), _cerrada(-50.0), _cerrada(50.0), _cerrada(-30.0)]
    s = calcular_estadisticas(entradas)
    assert s.n_operaciones == 4
    assert s.n_ganadoras == 2
    assert s.n_perdedoras == 2
    assert s.win_rate == 0.5
    assert s.ganancia_promedio == 75.0   # (100+50)/2
    assert s.perdida_promedio == -40.0   # (-50-30)/2
    assert s.pnl_total == 70.0


def test_calcular_estadisticas_expectancy():
    # win_rate=0.5, ganancia_promedio=75, perdida_promedio=-40
    # expectancy = 0.5*75 + 0.5*(-40) = 37.5 - 20 = 17.5
    entradas = [_cerrada(100.0), _cerrada(-50.0), _cerrada(50.0), _cerrada(-30.0)]
    s = calcular_estadisticas(entradas)
    assert abs(s.expectancy - 17.5) < 1e-9


def test_calcular_estadisticas_resultado_cero_cuenta_como_perdida():
    entradas = [_cerrada(0.0)]
    s = calcular_estadisticas(entradas)
    assert s.n_ganadoras == 0
    assert s.n_perdedoras == 1


def test_calcular_estadisticas_drawdown_detecta_la_peor_caida():
    # Equity acumulado en orden de cierre: +100 (pico 100) -50 (dd=50)
    # +20 (equity 70, sigue en dd desde el pico) -80 (equity -10, dd=110)
    e1, e2, e3, e4 = _cerrada(100.0), _cerrada(-50.0), _cerrada(20.0), _cerrada(-80.0)
    import datetime
    base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    for i, e in enumerate([e1, e2, e3, e4]):
        e.fecha_cierre = (base + datetime.timedelta(days=i)).isoformat()
    s = calcular_estadisticas([e1, e2, e3, e4])
    assert abs(s.drawdown_maximo - 110.0) < 1e-9


def test_calcular_estadisticas_por_estrategia_separa_por_nombre():
    entradas = [
        _cerrada(100.0, estrategia="Bull Call Spread"),
        _cerrada(-50.0, estrategia="Bull Call Spread"),
        _cerrada(30.0, estrategia="Iron Condor"),
    ]
    por_estrategia = calcular_estadisticas_por_estrategia(entradas)
    assert set(por_estrategia) == {"Bull Call Spread", "Iron Condor"}
    assert por_estrategia["Bull Call Spread"].n_operaciones == 2
    assert por_estrategia["Iron Condor"].n_operaciones == 1
    assert por_estrategia["Iron Condor"].pnl_total == 30.0


def test_calcular_estadisticas_por_estrategia_vacio_da_diccionario_vacio():
    assert calcular_estadisticas_por_estrategia([]) == {}
