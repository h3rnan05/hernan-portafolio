"""Precio oficial de subasta y gap oficial -- sin red (transporte falso)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter.data import subastas
from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca
from momentum_hunter.models import BarraIntradia


def _print(cond, precio, cuando, exch, size=None):
    crudo = {"c": cond, "p": precio, "t": cuando, "x": exch}
    if size is not None:
        crudo["s"] = size
    return crudo


def _dia(fecha, o=None, c=None):
    return {"d": fecha, "o": o or [], "c": c or []}


AYER = _dia("2026-09-25", c=[
    _print("M", 50.00, "2026-09-25T20:00:00.100Z", "Q", size=900_000),
    _print("6", 50.00, "2026-09-25T20:00:00.101Z", "Q", size=900_000),
    _print("6", 50.05, "2026-09-25T20:00:00.300Z", "N", size=1_000),
])
HOY = _dia("2026-09-28", o=[
    _print("Q", 55.00, "2026-09-28T13:30:00.100Z", "Q"),
    _print("O", 55.00, "2026-09-28T13:30:00.101Z", "Q", size=400_000),
    _print("O", 54.00, "2026-09-28T13:30:00.500Z", "P", size=2_000),
])


def test_el_oficial_es_el_del_exchange_con_el_cruce_mas_grande():
    dias = subastas.oficiales_por_dia([AYER, HOY])
    assert dias["2026-09-25"].cierre == pytest.approx(50.00)
    assert dias["2026-09-28"].apertura == pytest.approx(55.00)
    assert dias["2026-09-28"].cierre is None      # todavía no hubo subasta de cierre


def test_el_volumen_es_de_un_solo_cruce_nunca_la_suma():
    # 'M' y '6' del mismo sitio traen las mismas 900.000 acciones, y otro
    # exchange 1.000 más. Sumar daría 1.801.000: el error de #188.
    dias = subastas.oficiales_por_dia([AYER])
    assert dias["2026-09-25"].vol_cierre == pytest.approx(900_000)


def test_un_dia_repetido_en_dos_paginas_sigue_siendo_un_cruce():
    dias = subastas.oficiales_por_dia([AYER, AYER])
    assert dias["2026-09-25"].vol_cierre == pytest.approx(900_000)


def test_gap_oficial_contra_el_cierre_oficial_previo():
    dias = subastas.oficiales_por_dia([AYER, HOY])
    assert subastas.gap_oficial(dias, "2026-09-28") == pytest.approx(0.10)


def test_el_precio_oficial_gana_sobre_el_print_del_cruce_del_mismo_sitio():
    dias = subastas.oficiales_por_dia([_dia("2026-09-28", o=[
        _print("O", 55.02, "2026-09-28T13:30:00.10Z", "Q", size=10),
        _print("Q", 55.00, "2026-09-28T13:30:00.11Z", "Q")])])
    assert dias["2026-09-28"].apertura == pytest.approx(55.00)


def test_sin_size_y_oficiales_distintos_no_se_elige_ninguno():
    dias = subastas.oficiales_por_dia([_dia("2026-09-28", o=[
        _print("Q", 55.00, "2026-09-28T13:30:00Z", "Q"),
        _print("Q", 54.00, "2026-09-28T13:30:00Z", "P")])])
    assert dias["2026-09-28"].apertura is None


def test_sin_size_pero_mismo_precio_si_hay_oficial_y_el_volumen_queda_none():
    dias = subastas.oficiales_por_dia([_dia("2026-09-28", o=[
        _print("Q", 55.00, "2026-09-28T13:30:00Z", "Q"),
        _print("Q", 55.00, "2026-09-28T13:30:00Z", "P")])])
    assert dias["2026-09-28"].apertura == pytest.approx(55.00)
    assert dias["2026-09-28"].vol_apertura is None   # sin size no es cero


def test_prints_ilegibles_se_descartan():
    dias = subastas.oficiales_por_dia([_dia("2026-09-28", o=[
        {"c": "O", "p": "x", "t": "2026-09-28T13:30:00Z", "s": 5},
        {"c": "O", "p": 55.0, "t": "sin hora", "s": 5},
        "basura"])])
    assert dias["2026-09-28"].apertura is None


def test_sin_cierre_en_la_sesion_previa_no_salta_a_una_mas_vieja():
    viernes_sin_cierre = _dia("2026-09-25", o=[_print("O", 49.0, "2026-09-25T13:30:00Z", "Q", size=10)])
    jueves = _dia("2026-09-24", c=[_print("6", 48.0, "2026-09-24T20:00:00Z", "Q", size=10)])
    dias = subastas.oficiales_por_dia([jueves, viernes_sin_cierre, HOY])
    assert subastas.gap_oficial(dias, "2026-09-28") is None


def test_sin_apertura_de_hoy_no_hay_gap():
    assert subastas.gap_oficial(subastas.oficiales_por_dia([AYER]), "2026-09-28") is None


class _Transporte:
    def __init__(self, auctions=None, error=None):
        self.auctions, self.error, self.pedidos = auctions or {}, error, []

    def _paginas(self, ruta, params):
        self.pedidos.append((ruta, dict(params)))
        if self.error:
            raise self.error
        return [{"auctions": self.auctions, "next_page_token": None}]


def test_gaps_oficiales_pide_sip_con_el_simbolo_del_feed_y_devuelve_calculables():
    t = _Transporte({"ACME": [AYER, HOY], "BRK.B": [AYER]})
    ahora = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
    out = subastas.gaps_oficiales(["ACME", "BRK-B"], "2026-09-28", ahora, transporte=t)
    assert out == {"ACME": pytest.approx(0.10)}
    ruta, params = t.pedidos[0]
    assert ruta == "/v2/stocks/auctions" and params["feed"] == "sip"
    assert params["symbols"] == "ACME,BRK.B"


@pytest.mark.parametrize("error", [ErrorDatosAlpaca("auth"), RuntimeError("https://k:s@x")])
def test_un_fallo_devuelve_vacio_no_tumba(error):
    assert subastas.gaps_oficiales(["ACME"], "2026-09-28", transporte=_Transporte(error=error)) == {}


# ------------------------------------------------------ integración con run


def _bi(ticker="ACME"):
    marcas = ["2026-09-25T19:59:00+00:00", "2026-09-28T13:30:00+00:00", "2026-09-28T13:31:00+00:00"]
    return BarraIntradia(ticker, marcas, [50.0, 56.0, 56.5], [50.0, 56.4, 56.8],
                         [50.1, 56.6, 57.0], [49.9, 55.8, 56.3], [1000.0, 5000.0, 4000.0])


def test_el_gap_oficial_gana_sobre_el_de_velas_en_enforce(monkeypatch):
    from momentum_hunter.config import MomentumConfig
    monkeypatch.setenv("MOMENTUM_GAP_OFICIAL", "enforce")
    c = run_mod._construir_candidato_intradia(
        "ACME", None, None, None, False, 1.0, 50.0, 50.0, _bi(), MomentumConfig(), gap_oficial=0.10)
    assert c.factores.gap_pct == pytest.approx(0.10)       # velas dirían +12 %


def test_observar_no_cambia_el_gap_de_velas(monkeypatch):
    """Default del encargo: medir sin alterar la entrada de la v1."""
    from momentum_hunter.config import MomentumConfig
    monkeypatch.delenv("MOMENTUM_GAP_OFICIAL", raising=False)
    c = run_mod._construir_candidato_intradia(
        "ACME", None, None, None, False, 1.0, 50.0, 50.0, _bi(), MomentumConfig(), gap_oficial=0.10)
    assert c.factores.gap_pct == pytest.approx(0.12)


def test_sin_gap_oficial_queda_el_de_velas():
    from momentum_hunter.config import MomentumConfig
    c = run_mod._construir_candidato_intradia(
        "ACME", None, None, None, False, 1.0, 50.0, 50.0, _bi(), MomentumConfig())
    assert c.factores.gap_pct == pytest.approx(0.12)


def test_gaps_oficiales_de_run_agrupa_por_fecha_de_la_ultima_vela(monkeypatch):
    vistos = []
    monkeypatch.setattr(subastas, "gaps_oficiales",
                        lambda tickers, fecha: vistos.append((sorted(tickers), fecha)) or {"ACME": 0.1})
    out = run_mod._gaps_oficiales({"ACME": _bi(), "OTRO": _bi("OTRO"), "VACIO": None})
    assert vistos == [(["ACME", "OTRO"], "2026-09-28")]
    assert out == {"ACME": 0.1}
