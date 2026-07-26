"""Pruebas de las estadísticas del Learning Engine -- números fijos a
mano para poder verificar win rate/retorno/drawdown/expectancy/sharpe
exactamente."""

from __future__ import annotations

from momentum_hunter.stats import calcular_estadisticas, calcular_por_clasificacion
from momentum_hunter.tracker import AlertaRegistrada


def _alerta(id_, ticker, fecha, retorno_1d, clasificacion="🔥 BREAKOUT") -> AlertaRegistrada:
    return AlertaRegistrada(
        id=id_, ticker=ticker, fecha=fecha, precio_entrada=10.0, stop=9.0,
        objetivo1=11.0, objetivo2=12.0, clasificacion=clasificacion, estrategia="Long Call",
        score=90.0, resultados_pct={"1d": retorno_1d}, resuelta=True,
    )


def test_sin_alertas_devuelve_estadisticas_vacias():
    e = calcular_estadisticas([], 1)
    assert e.n == 0
    assert e.win_rate is None
    assert e.sharpe is None


def test_win_rate_y_retorno_promedio():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10),
        _alerta("2", "B", "2026-07-02", -0.05),
        _alerta("3", "C", "2026-07-03", 0.20),
    ]
    e = calcular_estadisticas(alertas, 1)
    assert e.n == 3
    assert e.win_rate == 2 / 3
    assert abs(e.retorno_promedio - (0.10 - 0.05 + 0.20) / 3) < 1e-9


def test_expectancy_combina_ganancia_y_perdida_promedio():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10),
        _alerta("2", "B", "2026-07-02", -0.10),
    ]
    e = calcular_estadisticas(alertas, 1)
    # win_rate=0.5, ganancia_prom=0.10, perdida_prom=-0.10 -> expectancy=0
    assert abs(e.expectancy - 0.0) < 1e-9


def test_drawdown_maximo_sobre_la_racha_perdedora():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10),
        _alerta("2", "B", "2026-07-02", -0.20),
        _alerta("3", "C", "2026-07-03", -0.10),
        _alerta("4", "D", "2026-07-04", 0.05),
    ]
    e = calcular_estadisticas(alertas, 1)
    # equity: 0.10 -> -0.10 -> -0.20 -> -0.15; pico=0.10; peor caída = 0.10-(-0.20)=0.30
    assert abs(e.drawdown_maximo - 0.30) < 1e-9


def test_sharpe_none_con_una_sola_alerta():
    e = calcular_estadisticas([_alerta("1", "A", "2026-07-01", 0.10)], 1)
    assert e.sharpe is None


def test_ignora_alertas_sin_resultado_en_ese_horizonte():
    con_resultado = _alerta("1", "A", "2026-07-01", 0.10)
    sin_resultado = AlertaRegistrada(
        id="2", ticker="B", fecha="2026-07-02", precio_entrada=10.0, stop=9.0,
        objetivo1=11.0, objetivo2=12.0, clasificacion="🔥 BREAKOUT", estrategia="Long Call",
        score=90.0, resultados_pct={}, resuelta=False,
    )
    e = calcular_estadisticas([con_resultado, sin_resultado], 1)
    assert e.n == 1


def test_calcular_por_clasificacion_agrupa_correctamente():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10, clasificacion="🔥 BREAKOUT"),
        _alerta("2", "B", "2026-07-02", -0.05, clasificacion="🚀 SHORT SQUEEZE"),
        _alerta("3", "C", "2026-07-03", 0.20, clasificacion="🔥 BREAKOUT"),
    ]
    tabla = calcular_por_clasificacion(alertas, 1)
    assert set(tabla) == {"🔥 BREAKOUT", "🚀 SHORT SQUEEZE"}
    assert tabla["🔥 BREAKOUT"].n == 2
    assert tabla["🚀 SHORT SQUEEZE"].n == 1
