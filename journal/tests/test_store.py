from __future__ import annotations

from journal.store import abrir_entrada, cerrar_entrada, encontrar_abierta_por_ticker


def _abrir(ticker="BNY", costo=130.0, riesgo=130.0):
    return abrir_entrada(
        ticker=ticker, score_screener=83.0, posicion_shortlist=1, tesis="Alcista",
        estrategia="Bull Call Spread",
        patas=[{"tipo": "call", "accion": "comprar", "strike": 45.0, "prima": 2.10}],
        costo=costo, riesgo_maximo=riesgo, ganancia_maxima=370.0,
        probabilidad_exito=0.42, valor_esperado=15.5, motivo="Momentum alto",
    )


def test_abrir_entrada_genera_id_y_normaliza_ticker():
    e = _abrir(ticker="bny")
    assert e.ticker == "BNY"
    assert e.id
    assert e.estado == "abierta"


def test_abrir_entrada_ids_son_unicos():
    a, b = _abrir(), _abrir()
    assert a.id != b.id


def test_cerrar_entrada_marca_estado_y_calcula_pct():
    entradas = [_abrir(riesgo=130.0)]
    cerrada = cerrar_entrada(entradas, entradas[0].id, resultado=65.0, notas="cerré temprano")
    assert cerrada is not None
    assert cerrada.estado == "cerrada"
    assert cerrada.resultado == 65.0
    assert cerrada.resultado_pct == 0.5  # 65/130
    assert cerrada.notas_cierre == "cerré temprano"
    assert cerrada.fecha_cierre is not None
    # Se muta in-place: la lista original también refleja el cierre.
    assert entradas[0].estado == "cerrada"


def test_cerrar_entrada_sin_riesgo_maximo_no_calcula_pct():
    entradas = [_abrir(riesgo=None)]
    cerrada = cerrar_entrada(entradas, entradas[0].id, resultado=65.0)
    assert cerrada.resultado_pct is None


def test_cerrar_entrada_id_inexistente_da_none():
    entradas = [_abrir()]
    assert cerrar_entrada(entradas, "no-existe", resultado=10.0) is None


def test_cerrar_entrada_ya_cerrada_no_se_recierra():
    entradas = [_abrir()]
    cerrar_entrada(entradas, entradas[0].id, resultado=10.0)
    assert cerrar_entrada(entradas, entradas[0].id, resultado=999.0) is None
    assert entradas[0].resultado == 10.0  # no se sobrescribió


def test_encontrar_abierta_por_ticker_devuelve_la_mas_reciente():
    vieja = _abrir()
    vieja.fecha_apertura = "2026-01-01T00:00:00+00:00"
    nueva = _abrir()
    nueva.fecha_apertura = "2026-06-01T00:00:00+00:00"
    entradas = [vieja, nueva]
    assert encontrar_abierta_por_ticker(entradas, "BNY").id == nueva.id


def test_encontrar_abierta_ignora_cerradas():
    entradas = [_abrir()]
    cerrar_entrada(entradas, entradas[0].id, resultado=10.0)
    assert encontrar_abierta_por_ticker(entradas, "BNY") is None


def test_encontrar_abierta_sin_coincidencias_da_none():
    assert encontrar_abierta_por_ticker([_abrir(ticker="AAPL")], "BNY") is None
