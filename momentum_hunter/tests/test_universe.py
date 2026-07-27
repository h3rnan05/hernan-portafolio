"""Pruebas de los parsers del universo -- sin red, sobre texto fijo con
el mismo formato que devuelven los archivos reales de NASDAQ Trader."""

from __future__ import annotations

from momentum_hunter.universe import _parsear_nasdaqlisted, _parsear_otherlisted


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
