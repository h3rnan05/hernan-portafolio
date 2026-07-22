"""Pruebas de /journal open|close|list|stats: red (Yahoo/GitHub) mockeada
por completo -- verifica que registre lo que /options de verdad calculó
(nunca invente un resultado al cerrar), que el costo se calcule bien para
estrategias de débito vs. crédito, y que las estadísticas se deriven solo
de operaciones cerradas."""

from __future__ import annotations

import journal_command as jc
from journal.store import abrir_entrada
from screener.data.provider import Barras, Fundamentales
from screener.options_ideas import CadenaOpciones, ContratoOpcion
from screener.options_math import precio_black_scholes

SPOT, DIAS, IV = 100.0, 35, 0.30


def _barras(ticker="BNY", n=260, precio_final=SPOT):
    precios = [precio_final - (n - i) * 0.1 for i in range(n)]
    return Barras(ticker, [str(i) for i in range(n)], precios,
                  [p * 1.01 for p in precios], [p * 0.99 for p in precios],
                  [1e7] * n)


class _FakeProvider:
    def __init__(self, barras_por_ticker):
        self._b = barras_por_ticker

    def barras(self, tickers, dias=400):
        return {t: self._b[t] for t in tickers if t in self._b}

    def fundamentales(self, tickers):
        return {t: Fundamentales(t) for t in tickers}


def _cadena_sintetica(ticker="BNY", spot=SPOT, dias=DIAS, iv=IV) -> CadenaOpciones:
    calls, puts = [], []
    for k in range(60, 145, 5):
        precio_call = precio_black_scholes(spot, k, dias, iv, "call")
        precio_put = precio_black_scholes(spot, k, dias, iv, "put")
        oi = max(50, int(600 - abs(k - spot) * 10))
        calls.append(ContratoOpcion(
            strike=float(k), prima=round(precio_call, 2),
            bid=round(precio_call * 0.975, 2), ask=round(precio_call * 1.025, 2),
            iv=iv, open_interest=oi, volumen=oi // 2))
        puts.append(ContratoOpcion(
            strike=float(k), prima=round(precio_put, 2),
            bid=round(precio_put * 0.975, 2), ask=round(precio_put * 1.025, 2),
            iv=iv, open_interest=oi, volumen=oi // 2))
    return CadenaOpciones(ticker=ticker, vencimiento="2099-01-01", dias_a_vencimiento=dias,
                          calls=calls, puts=puts)


def _sin_journal_previo():
    return [], None


def test_parsear_estrategia_reconoce_nombre_largo_antes_que_corto():
    nombre, resto = jc._parsear_estrategia_y_motivo("Bull Call Spread me gustó el momentum")
    assert nombre == "Bull Call Spread"
    assert resto == "me gustó el momentum"


def test_parsear_estrategia_no_reconocida_da_none():
    nombre, resto = jc._parsear_estrategia_y_motivo("Estrategia Inventada algo")
    assert nombre is None
    assert resto == "Estrategia Inventada algo"


def test_abrir_operacion_estrategia_no_reconocida():
    texto = jc.abrir_operacion("BNY", "Estrategia Rara motivo")
    assert "No reconocí la estrategia" in texto


def test_abrir_operacion_sin_datos_de_precio(monkeypatch):
    monkeypatch.setattr(jc, "YahooProvider", lambda: _FakeProvider({}))
    texto = jc.abrir_operacion("ZZZZ", "Long Call")
    assert "no pude obtener datos de precio" in texto.lower()


def test_abrir_operacion_cadena_none(monkeypatch):
    monkeypatch.setattr(jc, "YahooProvider", lambda: _FakeProvider({"BNY": _barras()}))
    monkeypatch.setattr(jc, "obtener_cadena", lambda ticker: None)
    texto = jc.abrir_operacion("BNY", "Long Call")
    assert "no pude obtener la cadena de opciones" in texto.lower()


def test_abrir_operacion_estrategia_no_construible_lista_disponibles(monkeypatch):
    monkeypatch.setattr(jc, "YahooProvider", lambda: _FakeProvider({"BNY": _barras()}))
    vacia = CadenaOpciones(ticker="BNY", vencimiento="2099-01-01", dias_a_vencimiento=35, calls=[], puts=[])
    monkeypatch.setattr(jc, "obtener_cadena", lambda ticker: vacia)
    texto = jc.abrir_operacion("BNY", "Long Call")
    assert "No pude construir 'Long Call'" in texto


def test_abrir_operacion_exitosa_registra_y_persiste(monkeypatch):
    monkeypatch.setattr(jc, "YahooProvider", lambda: _FakeProvider({"BNY": _barras()}))
    monkeypatch.setattr(jc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(jc, "_leer_journal", _sin_journal_previo)

    escritos = {}

    def _fake_escribir(entradas, sha):
        escritos["entradas"] = entradas
        escritos["sha"] = sha

    monkeypatch.setattr(jc, "_escribir_journal", _fake_escribir)

    texto = jc.abrir_operacion("bny", "Long Call me gustó el momentum")
    assert "📝 Registrado en el journal" in texto
    assert "BNY -- Long Call" in texto
    assert "Motivo: me gustó el momentum" in texto
    assert len(escritos["entradas"]) == 1
    entrada = escritos["entradas"][0]
    assert entrada.ticker == "BNY"
    assert entrada.estrategia == "Long Call"
    assert entrada.motivo == "me gustó el momentum"
    assert entrada.costo == entrada.riesgo_maximo  # Long Call es débito


def test_abrir_operacion_sin_motivo_usa_razon_de_la_estrategia(monkeypatch):
    monkeypatch.setattr(jc, "YahooProvider", lambda: _FakeProvider({"BNY": _barras()}))
    monkeypatch.setattr(jc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(jc, "_leer_journal", _sin_journal_previo)
    escritos = {}
    monkeypatch.setattr(jc, "_escribir_journal", lambda entradas, sha: escritos.setdefault("entradas", entradas))

    jc.abrir_operacion("BNY", "Long Call")
    entrada = escritos["entradas"][0]
    assert entrada.motivo  # no vacío -- cae a la razón determinística de la estrategia
    assert "Compra call" in entrada.motivo


def test_abrir_operacion_estrategia_credito_costo_es_negativo(monkeypatch):
    monkeypatch.setattr(jc, "YahooProvider", lambda: _FakeProvider({"BNY": _barras()}))
    monkeypatch.setattr(jc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(jc, "_leer_journal", _sin_journal_previo)
    escritos = {}
    monkeypatch.setattr(jc, "_escribir_journal", lambda entradas, sha: escritos.setdefault("entradas", entradas))

    jc.abrir_operacion("BNY", "Bull Put Spread")
    entrada = escritos["entradas"][0]
    assert entrada.costo == -entrada.ganancia_maxima  # crédito recibido, no débito


def test_abrir_operacion_usa_score_de_shortlist_si_existe(monkeypatch, tmp_path):
    monkeypatch.setattr(jc, "YahooProvider", lambda: _FakeProvider({"BNY": _barras()}))
    monkeypatch.setattr(jc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(jc, "_leer_journal", _sin_journal_previo)
    escritos = {}
    monkeypatch.setattr(jc, "_escribir_journal", lambda entradas, sha: escritos.setdefault("entradas", entradas))

    import json as jsonlib
    shortlist = tmp_path / "shortlist_hoy.json"
    shortlist.write_text(jsonlib.dumps({
        "universo_escaneado": 480,
        "shortlist": [{"ticker": "BNY", "score": 83.0, "sector": "Financial Services", "sub_scores": {}}],
    }))
    monkeypatch.setattr(jc, "SHORTLIST_PATH", shortlist)

    texto = jc.abrir_operacion("BNY", "Long Call")
    assert "83/100" in texto
    entrada = escritos["entradas"][0]
    assert entrada.score_screener == 83.0
    assert entrada.posicion_shortlist == 1


def test_cerrar_operacion_sin_abierta(monkeypatch):
    monkeypatch.setattr(jc, "_leer_journal", _sin_journal_previo)
    texto = jc.cerrar_operacion("BNY", 100.0, None)
    assert "No encontré ninguna operación abierta" in texto


def test_cerrar_operacion_exitosa(monkeypatch):
    entrada = abrir_entrada(
        ticker="BNY", score_screener=83.0, posicion_shortlist=1, tesis="Alcista",
        estrategia="Long Call", patas=[{"tipo": "call", "accion": "comprar", "strike": 45.0, "prima": 2.0}],
        costo=200.0, riesgo_maximo=200.0, ganancia_maxima=None,
        probabilidad_exito=0.33, valor_esperado=30.0, motivo="test",
    )
    monkeypatch.setattr(jc, "_leer_journal", lambda: ([entrada], "sha123"))
    escritos = {}
    monkeypatch.setattr(jc, "_escribir_journal", lambda entradas, sha: escritos.setdefault("sha", sha))

    texto = jc.cerrar_operacion("bny", 150.0, "cerré antes del vencimiento")
    assert "✅ Cerrada: BNY -- Long Call" in texto
    assert "$+150.00" in texto
    assert "75.0%" in texto  # 150/200
    assert "cerré antes del vencimiento" in texto
    assert escritos["sha"] == "sha123"
    assert entrada.estado == "cerrada"


def test_listar_abiertas_vacio(monkeypatch):
    monkeypatch.setattr(jc, "_leer_journal", _sin_journal_previo)
    assert "No tienes operaciones abiertas" in jc.listar_abiertas()


def test_listar_abiertas_muestra_las_abiertas(monkeypatch):
    entrada = abrir_entrada(
        ticker="BNY", score_screener=83.0, posicion_shortlist=1, tesis="Alcista",
        estrategia="Long Call", patas=[], costo=200.0, riesgo_maximo=200.0,
        ganancia_maxima=None, probabilidad_exito=0.33, valor_esperado=30.0, motivo="me gustó",
    )
    monkeypatch.setattr(jc, "_leer_journal", lambda: ([entrada], "sha"))
    texto = jc.listar_abiertas()
    assert "BNY -- Long Call" in texto
    assert "me gustó" in texto


def test_mostrar_estadisticas_sin_cerradas(monkeypatch):
    monkeypatch.setattr(jc, "_leer_journal", _sin_journal_previo)
    texto = jc.mostrar_estadisticas()
    assert "no hay estadísticas que calcular" in texto.lower()


def test_mostrar_estadisticas_con_cerradas(monkeypatch):
    e1 = abrir_entrada(ticker="BNY", score_screener=83.0, posicion_shortlist=1, tesis="Alcista",
                       estrategia="Long Call", patas=[], costo=200.0, riesgo_maximo=200.0,
                       ganancia_maxima=None, probabilidad_exito=0.33, valor_esperado=30.0, motivo="x")
    e1.estado = "cerrada"
    e1.fecha_cierre = "2026-07-22T00:00:00+00:00"
    e1.resultado = 100.0
    e1.resultado_pct = 0.5
    monkeypatch.setattr(jc, "_leer_journal", lambda: ([e1], "sha"))
    texto = jc.mostrar_estadisticas()
    assert "📊 Estadísticas del journal" in texto
    assert "Win rate: 100.0%" in texto
    assert "Por estrategia:" in texto
    assert "Long Call:" in texto


def test_costo_apertura_debito_usa_riesgo_maximo():
    from screener.options_strategies import EstrategiaOpciones
    e = EstrategiaOpciones(nombre="Long Call", patas=[], razon="", riesgo_maximo=500.0, ganancia_maxima=None)
    assert jc.costo_apertura(e) == 500.0


def test_costo_apertura_credito_usa_ganancia_maxima_negativa():
    from screener.options_strategies import EstrategiaOpciones
    e = EstrategiaOpciones(nombre="Iron Condor", patas=[], razon="", riesgo_maximo=1000.0, ganancia_maxima=250.0)
    assert jc.costo_apertura(e) == -250.0
