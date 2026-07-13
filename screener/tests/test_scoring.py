"""Pruebas del motor de scoring con un DataProvider falso (sin red).
Verifica normalización cross-sectional, manejo de datos faltantes y ranking."""

from __future__ import annotations

import math

from screener.config import ScreenerConfig
from screener.data.provider import Barras, DataProvider, Fundamentales
from screener.factors import technical as tech
from screener.scoring import percentiles, puntuar


def _serie(precios: list[float], vol: float = 1e7) -> Barras:
    n = len(precios)
    return Barras("X", [str(i) for i in range(n)], precios,
                  [p * 1.01 for p in precios], [p * 0.99 for p in precios],
                  [vol / precios[-1]] * n)


class FakeProvider(DataProvider):
    def __init__(self, barras, fund):
        self._b, self._f = barras, fund

    def barras(self, tickers, dias=400):
        return {t: self._b[t] for t in tickers if t in self._b}

    def fundamentales(self, tickers):
        return {t: self._f.get(t, Fundamentales(t)) for t in tickers}


def test_percentiles_basico():
    p = percentiles({"a": 1.0, "b": 2.0, "c": 3.0})
    assert p["c"] == 100.0 and p["a"] == 0.0 and p["b"] == 50.0


def test_percentiles_invertido():
    # menor valor -> mejor score
    p = percentiles({"a": 1.0, "b": 3.0}, invertir=True)
    assert p["a"] == 100.0 and p["b"] == 0.0


def test_percentiles_ignora_none():
    p = percentiles({"a": 5.0, "b": None})
    assert "b" not in p and p["a"] == 100.0


def test_momentum_tendencia_alcista():
    # serie estrictamente creciente: momentum positivo, tendencia = 3/3
    subiendo = _serie([100 + i for i in range(260)])
    assert tech.momentum_compuesto(subiendo) > 0
    assert tech.score_tendencia(subiendo) == 3.0
    assert tech.proximidad_maximo_52s(subiendo) == 1.0  # en máximos


def test_ranking_prefiere_momentum_y_tendencia():
    """Una acción en clara tendencia alcista debe rankear sobre una lateral."""
    ganadora = _serie([100 + i for i in range(260)])           # sube fuerte
    lateral = _serie([100 + math.sin(i / 5) for i in range(260)])  # plana
    cfg = ScreenerConfig()
    fund = {"WIN": Fundamentales("WIN", pe=15, roe=0.25),
            "FLAT": Fundamentales("FLAT", pe=15, roe=0.25)}
    ranking = puntuar({"WIN": ganadora, "FLAT": lateral}, fund, cfg)
    assert ranking[0].ticker == "WIN"
    assert ranking[0].score_total > ranking[1].score_total


def test_datos_faltantes_no_rompen():
    """Sin fundamentales, el score se re-normaliza sobre factores de precio."""
    serie = _serie([100 + i for i in range(260)])
    cfg = ScreenerConfig()
    ranking = puntuar({"A": serie}, {"A": Fundamentales("A")}, cfg)
    assert len(ranking) == 1
    assert 0 <= ranking[0].score_total <= 100
    # no debe incluir sub-scores fundamentales que no existen
    assert "valor" not in ranking[0].sub and "calidad" not in ranking[0].sub


def test_pesos_deben_sumar_uno():
    import pytest
    mala = ScreenerConfig(pesos={"momentum": 0.9})  # suma 0.9
    with pytest.raises(ValueError):
        mala.validar()
