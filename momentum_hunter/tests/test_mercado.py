"""Pruebas del clima de mercado -- sin red, con un provider falso."""

from __future__ import annotations

from momentum_hunter import mercado
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import BarraIntradia


def _bi(ticker="SPY", closes=None, n=12) -> BarraIntradia:
    """Velas de sesión regular (14:00 UTC en adelante) para que
    `barras_de_hoy`/`vwap_real` las cuenten."""
    closes = closes or [100.0] * n
    ts = [f"2026-08-21T14:{i:02d}:00+00:00" for i in range(len(closes))]
    vol = [1000.0] * len(closes)
    return BarraIntradia(ticker, ts, list(closes), list(closes), list(closes), list(closes), vol)


class _FakeProvider(DataProvider):
    def __init__(self, barras=None, excepcion=None):
        self._barras = barras if barras is not None else {}
        self._excepcion = excepcion

    def barras(self, tickers, dias=280):
        return {}

    def barras_intradia(self, tickers, periodo="5d", intervalo="1m"):
        if self._excepcion:
            raise self._excepcion
        return self._barras

    def metadata(self, tickers):
        return {}


def test_mercado_subiendo_es_favorable():
    # Serie ascendente: el último precio queda por encima del VWAP y de la EMA9.
    subiendo = [100.0 + i for i in range(14)]
    clima = mercado.evaluar(_FakeProvider({"SPY": _bi(closes=subiendo)}))
    assert clima.veredicto == mercado.FAVORABLE
    assert clima.favorable is True and clima.debil is False


def test_mercado_bajando_es_debil():
    bajando = [120.0 - i for i in range(14)]
    clima = mercado.evaluar(_FakeProvider({"SPY": _bi(closes=bajando)}))
    assert clima.veredicto == mercado.DEBIL
    assert clima.debil is True


def test_sin_datos_del_indice_es_desconocido_no_debil():
    # "No se sabe" nunca debe confundirse con "está mal": desconocido no
    # penaliza nada (mismo principio que el resto del repo).
    clima = mercado.evaluar(_FakeProvider({}))
    assert clima.veredicto == mercado.DESCONOCIDO
    assert clima.debil is False and clima.favorable is False


def test_fallo_de_red_es_desconocido_y_no_lanza():
    clima = mercado.evaluar(_FakeProvider(excepcion=RuntimeError("yahoo caído")))
    assert clima.veredicto == mercado.DESCONOCIDO


def test_muy_pocas_velas_es_desconocido():
    # Al abrir el mercado no hay velas suficientes para la EMA9 -- eso es
    # "todavía no se sabe", no un veredicto.
    clima = mercado.evaluar(_FakeProvider({"SPY": _bi(closes=[100.0, 101.0, 102.0])}))
    assert clima.veredicto == mercado.DESCONOCIDO


def test_frase_es_humana_y_sin_indicadores_crudos():
    # report.py prohíbe mostrar indicadores crudos al usuario.
    for veredicto in (mercado.FAVORABLE, mercado.DEBIL, mercado.DESCONOCIDO):
        frase = mercado.ClimaMercado(veredicto).frase()
        assert frase and frase[0].isupper()
        for prohibido in ("VWAP", "EMA", "RVOL", "MACD"):
            assert prohibido not in frase


def test_el_indice_por_defecto_es_spy():
    assert mercado.TICKER_INDICE == "SPY"
