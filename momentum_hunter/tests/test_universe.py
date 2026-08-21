"""Pruebas de los parsers del universo -- sin red, sobre texto fijo con
el mismo formato que devuelven los archivos reales de NASDAQ Trader."""

from __future__ import annotations

from momentum_hunter.universe import (
    _parsear_nasdaqlisted,
    _parsear_otherlisted,
    _parsear_sec_tickers,
)


def test_parsear_nasdaqlisted_excluye_test_issues_y_marca_etf():
    texto = (
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
        "AAAA|Alpha Corp Common Stock|Q|N|N|100|N|N\n"
        "BBBB|Beta ETF Trust|Q|N|N|100|Y|N\n"
        "ZZZZ|Zulu Test Issue|Q|Y|N|100|N|N\n"
        "File Creation Time: 0801202600:00\n"
    )
    simbolos = _parsear_nasdaqlisted(texto)
    tickers = {s.ticker: s for s in simbolos}
    assert set(tickers) == {"AAAA", "BBBB"}
    assert tickers["AAAA"].es_etf is False
    assert tickers["BBBB"].es_etf is True
    assert all(s.bolsa == "NASDAQ" for s in simbolos)


def test_parsear_otherlisted_mapea_bolsa_y_excluye_arca():
    texto = (
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "CCCC|Charlie Corp|N|CCCC|N|100|N|CCCC\n"
        "DDDD|Delta Inc|A|DDDD|N|100|N|DDDD\n"
        "EEEE|Echo Arca ETF|P|EEEE|Y|100|N|EEEE\n"
        "File Creation Time: 0801202600:00\n"
    )
    simbolos = _parsear_otherlisted(texto)
    tickers = {s.ticker: s for s in simbolos}
    assert set(tickers) == {"CCCC", "DDDD"}   # Arca (P) queda fuera
    assert tickers["CCCC"].bolsa == "NYSE"
    assert tickers["DDDD"].bolsa == "AMEX"


def test_parsear_sec_tickers_mapea_bolsa_y_descarta_otc_y_cboe():
    payload = {"data": [
        [1, "Alpha Corp", "AAAA", "Nasdaq"],
        [2, "Charlie Corp", " cccc ", "NYSE"],
        [3, "Pink Sheet Co", "PINK", "OTC"],
        [4, "Bzx Co", "BZX", "CBOE"],
        [5, "Sin bolsa", "NOEX", None],
        [6, "Sin symbol", "", "NYSE"],
    ]}
    simbolos = _parsear_sec_tickers(payload)
    tickers = {s.ticker: s for s in simbolos}
    assert set(tickers) == {"AAAA", "CCCC"}
    assert tickers["AAAA"].bolsa == "NASDAQ"
    assert tickers["AAAA"].nombre == "Alpha Corp"
    assert tickers["CCCC"].bolsa == "NYSE"
    assert all(s.es_etf is False for s in simbolos)


def test_parsear_sec_tickers_payload_vacio_no_lanza():
    assert _parsear_sec_tickers({}) == []
    assert _parsear_sec_tickers({"data": []}) == []


# ------------------------- ventana rotativa (fix 2026-08-21) -------------------------
# `ticks[:N]` tomaba SIEMPRE el mismo extremo. Con el respaldo de la SEC
# (ordenado por capitalización descendente) eso significó que el bot solo
# veía las 1.000 empresas más grandes: 3.155 de 3.161 candidatas
# auditadas resultaron large-cap, y la banda small-cap -- la tesis entera
# del bot -- recibió 6 registros en 12 días, todos del mismo ticker.

from datetime import UTC, datetime

from momentum_hunter.universe import MINUTOS_POR_CORRIDA, ventana_rotativa

_T0 = datetime(2026, 8, 24, 13, 30, tzinfo=UTC)


def _en_corrida(n: int) -> datetime:
    from datetime import timedelta
    return _T0 + timedelta(minutes=MINUTOS_POR_CORRIDA * n)


def test_ventana_rotativa_avanza_entre_corridas():
    simbolos = [f"T{i}" for i in range(100)]
    a = ventana_rotativa(simbolos, 10, _en_corrida(0))
    b = ventana_rotativa(simbolos, 10, _en_corrida(1))
    assert a != b
    assert len(a) == len(b) == 10


def test_ventana_rotativa_cubre_el_universo_completo():
    # Lo que de verdad importa: ningún símbolo queda fuera para siempre.
    simbolos = [f"T{i}" for i in range(100)]
    vistos = set()
    for n in range(10):   # 100/10 = 10 corridas para un ciclo completo
        vistos |= set(ventana_rotativa(simbolos, 10, _en_corrida(n)))
    assert vistos == set(simbolos)


def test_ventana_rotativa_es_determinista_en_la_misma_corrida():
    # Dos ejecuciones del mismo tramo de 30 min miran lo mismo -- un
    # reintento del workflow no debe cambiar el universo evaluado.
    simbolos = [f"T{i}" for i in range(100)]
    t = _en_corrida(3)
    assert ventana_rotativa(simbolos, 10, t) == ventana_rotativa(simbolos, 10, t)


def test_ventana_rotativa_da_siempre_el_mismo_tamano():
    # Presupuesto de tiempo estable en CI: la última ventana se completa
    # dando la vuelta al principio en vez de quedar corta.
    simbolos = [f"T{i}" for i in range(95)]   # 95 no es múltiplo de 10
    for n in range(12):
        assert len(ventana_rotativa(simbolos, 10, _en_corrida(n))) == 10


def test_ventana_rotativa_devuelve_todo_si_el_limite_cubre_el_universo():
    simbolos = ["A", "B", "C"]
    assert ventana_rotativa(simbolos, 10, _T0) == simbolos
    assert ventana_rotativa(simbolos, 0, _T0) == simbolos


def test_ventana_rotativa_no_pierde_ni_duplica_dentro_de_una_ventana():
    simbolos = [f"T{i}" for i in range(100)]
    v = ventana_rotativa(simbolos, 25, _en_corrida(2))
    assert len(set(v)) == len(v) == 25
