"""Pruebas de las heurísticas puras de `data/provider.py` -- sin red."""

from __future__ import annotations

from momentum_hunter.data.provider import _num, _parece_cef, _parece_spac


def test_parece_spac_detecta_nombre_tipico():
    assert _parece_spac("Acme Acquisition Corp") is True
    assert _parece_spac("Acme Blank Check Company") is True


def test_parece_spac_no_falso_positivo_en_empresa_normal():
    assert _parece_spac("Apple Inc.") is False


def test_parece_spac_none_es_false():
    assert _parece_spac(None) is False


def test_parece_cef_por_quote_type():
    assert _parece_cef("Cualquiera", "CLOSEDEND") is True


def test_parece_cef_por_nombre():
    assert _parece_cef("Acme Municipal Income Fund", None) is True


def test_parece_cef_no_falso_positivo():
    assert _parece_cef("Apple Inc.", "EQUITY") is False


def test_num_convierte_valores_validos():
    assert _num("3.5") == 3.5
    assert _num(10) == 10.0


def test_num_none_con_nan_o_invalido():
    assert _num(float("nan")) is None
    assert _num(None) is None
    assert _num("no-es-numero") is None
