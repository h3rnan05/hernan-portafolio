"""Pruebas de las estadísticas del Learning Engine -- números fijos a
mano para poder verificar win rate/retorno/drawdown/expectancy/sharpe
exactamente."""

from __future__ import annotations

from momentum_hunter.stats import (
    calcular_estadisticas,
    calcular_por_catalizador,
    calcular_por_clasificacion,
    calcular_por_float,
    calcular_por_gap,
    calcular_por_hora,
    calcular_por_rvol,
)
from momentum_hunter.tracker import AlertaRegistrada


def _alerta(
    id_, ticker, fecha, retorno_1d, clasificacion="🔥 BREAKOUT",
    hora_utc=None, catalizador_tipo=None, float_acciones=None, gap_pct=None, rvol=None,
) -> AlertaRegistrada:
    return AlertaRegistrada(
        id=id_, ticker=ticker, fecha=fecha, precio_entrada=10.0, stop=9.0,
        objetivo1=11.0, objetivo2=12.0, clasificacion=clasificacion, estrategia="Long Call",
        score=90.0, resultados_pct={"1d": retorno_1d}, resuelta=True,
        hora_utc=hora_utc, catalizador_tipo=catalizador_tipo,
        float_acciones=float_acciones, gap_pct=gap_pct, rvol=rvol,
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


def test_calcular_por_hora_agrupa_por_hora_exacta():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10, hora_utc=13),
        _alerta("2", "B", "2026-07-02", -0.05, hora_utc=15),
        _alerta("3", "C", "2026-07-03", 0.20, hora_utc=13),
    ]
    tabla = calcular_por_hora(alertas, 1)
    assert set(tabla) == {13, 15}
    assert tabla[13].n == 2


def test_calcular_por_catalizador_agrupa_por_tipo():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10, catalizador_tipo="fda"),
        _alerta("2", "B", "2026-07-02", -0.05, catalizador_tipo="earnings"),
    ]
    tabla = calcular_por_catalizador(alertas, 1)
    assert set(tabla) == {"fda", "earnings"}


def test_calcular_por_float_agrupa_en_bandas():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10, float_acciones=2_000_000),
        _alerta("2", "B", "2026-07-02", 0.05, float_acciones=100_000_000),
    ]
    tabla = calcular_por_float(alertas, 1)
    assert set(tabla) == {"<5M", ">=50M"}


def test_calcular_por_gap_usa_valor_absoluto():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10, gap_pct=-0.15),
        _alerta("2", "B", "2026-07-02", 0.05, gap_pct=0.02),
    ]
    tabla = calcular_por_gap(alertas, 1)
    assert set(tabla) == {"10-20%", "<5%"}


def test_calcular_por_rvol_agrupa_en_bandas():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10, rvol=3.0),
        _alerta("2", "B", "2026-07-02", 0.05, rvol=12.0),
    ]
    tabla = calcular_por_rvol(alertas, 1)
    assert set(tabla) == {"<5x", ">=10x"}


def test_agrupaciones_excluyen_alertas_sin_ese_dato():
    alertas = [
        _alerta("1", "A", "2026-07-01", 0.10, catalizador_tipo="fda"),
        _alerta("2", "B", "2026-07-02", 0.05, catalizador_tipo=None),
    ]
    tabla = calcular_por_catalizador(alertas, 1)
    assert set(tabla) == {"fda"}
