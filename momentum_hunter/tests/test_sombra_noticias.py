"""Sombra de noticias de Alpaca frente a Yahoo (2026-10-01). Cliente
simulado: ninguna prueba sale a la red ni usa llaves de verdad."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter.catalysts import sombra_noticias as sn
from momentum_hunter.catalysts.detector import Titular
from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca
from momentum_hunter.models import Barras, Metadata

CFG = MomentumConfig()
AHORA = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)


def _art(simbolos, titular, fuente="benzinga", fecha="2026-10-02T13:00:00Z"):
    return {"headline": titular, "source": fuente, "created_at": fecha, "symbols": simbolos,
            "url": "https://www.benzinga.com/x"}


class _Cliente:
    def __init__(self, paginas=None, error=None):
        self.paginas = list(paginas or [])
        self.error = error
        self.llamadas = []

    def _get(self, path, params):
        self.llamadas.append((path, dict(params)))
        if self.error:
            raise self.error
        return self.paginas.pop(0) if self.paginas else {"news": [], "next_page_token": None}


@pytest.fixture(autouse=True)
def _estado(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path / "estado"))
    monkeypatch.setenv("MOMENTUM_NOTICIAS_SOMBRA", "1")


def test_agrupa_por_simbolo_pagina_y_solo_usa_el_host_de_datos():
    cli = _Cliente([
        {"news": [_art(["AAA", "ZZZ"], "AAA wins FDA approval for drug")], "next_page_token": "p2"},
        {"news": [_art(["BBB"], "BBB reports quarterly results")], "next_page_token": None},
    ])
    f = sn.NoticiasAlpaca(cliente=cli)
    f.precargar(["AAA", "BBB", "CCC"], AHORA, AHORA)
    assert [t.texto for t in f.titulares("AAA")] == ["AAA wins FDA approval for drug"]
    assert len(f.titulares("BBB")) == 1
    assert f.tiene("CCC") and f.titulares("CCC") == []   # pedido sin noticias: lista vacía
    assert not f.tiene("ZZZ")                             # no pedido: ausente
    assert [c[0] for c in cli.llamadas] == ["/v1beta1/news", "/v1beta1/news"]
    assert cli.llamadas[1][1]["page_token"] == "p2"
    assert cli.llamadas[0][1]["symbols"] == "AAA,BBB,CCC"


def test_lote_que_falla_queda_ausente_y_se_cuenta_sin_lanzar():
    f = sn.NoticiasAlpaca(cliente=_Cliente(error=ErrorDatosAlpaca("sin_credenciales")))
    f.precargar(["AAA"], AHORA, AHORA)
    assert not f.tiene("AAA")
    assert f.errores == {"sin_credenciales": 1}


def test_compara_catalizadores_con_el_mismo_detector_y_ancla():
    cli = _Cliente([{"news": [
        _art(["AAA"], "AAA receives FDA approval for lead drug"),
        _art(["BBB"], "Sector roundup: biotech names move"),
    ], "next_page_token": None}])
    s = sn.Sombra(CFG, AHORA, fuente=sn.NoticiasAlpaca(cliente=cli))
    s.precargar(["AAA", "BBB", "CCC"])
    cat_yahoo = type("C", (), {"tipo": "fda"})()
    s.anotar("AAA", "AAA Corp", [Titular("x", "Yahoo")], None)
    s.anotar("BBB", "BBB Inc", [Titular("y", "Yahoo")], cat_yahoo)
    s.anotar("DDD", "DDD Inc", [], None)                  # no precargado
    filas = {f["ticker"]: f for f in s.filas}
    assert filas["AAA"]["cat_alpaca"] is not None and filas["AAA"]["cat_yahoo"] is None
    assert filas["BBB"]["cat_alpaca"] is None and filas["BBB"]["cat_yahoo"] == "fda"
    assert filas["DDD"]["sin_respuesta"] is True and filas["DDD"]["n_alpaca"] is None
    r = s.resumen()
    assert (r["comparadas"], r["solo_alpaca"], r["solo_yahoo"], r["ambos"]) == (2, 1, 1, 0)


def test_un_titular_de_otra_empresa_no_ancla():
    cli = _Cliente([{"news": [_art(["AAA"], "Rival XYZ receives FDA approval")], "next_page_token": None}])
    s = sn.Sombra(CFG, AHORA, fuente=sn.NoticiasAlpaca(cliente=cli))
    s.precargar(["AAA"])
    s.anotar("AAA", "Alfa Therapeutics", [], None)
    assert s.filas[0]["n_alpaca"] == 1 and s.filas[0]["cat_alpaca"] is None


def test_cerrar_escribe_jsonl_y_acumular_lo_lee(tmp_path):
    s = sn.Sombra(CFG, AHORA, fuente=sn.NoticiasAlpaca(cliente=_Cliente()))
    s.precargar(["AAA"])
    s.anotar("AAA", "AAA Corp", [], None)
    s.cerrar(persistir=True)
    ruta = sn.directorio() / "2026-10-02.jsonl"
    lineas = [json.loads(l) for l in ruta.read_text().splitlines()]
    assert lineas[0]["ticker"] == "AAA" and lineas[-1]["tipo"] == "resumen"
    total = sn.acumular(date(2026, 10, 1), date(2026, 10, 3))
    assert total["corridas"] == 1 and total["comparadas"] == 1


def test_dry_run_no_escribe():
    s = sn.Sombra(CFG, AHORA, fuente=sn.NoticiasAlpaca(cliente=_Cliente()))
    s.cerrar(persistir=False)
    assert not (sn.directorio() / "2026-10-02.jsonl").exists()


def test_apagada_con_la_variable(monkeypatch):
    monkeypatch.setenv("MOMENTUM_NOTICIAS_SOMBRA", "0")
    assert sn.nueva(CFG, AHORA) is None


def _barras():
    n = 25
    closes = [5.0] * n
    return Barras("AAA", [str(1_700_000_000 + i * 86_400) for i in range(n)], closes, closes, closes,
                  closes, [500_000.0] * n)


class _Prov:
    def metadata(self, tickers):
        return {t: Metadata(ticker=t, nombre="AAA Corp", market_cap=100_000_000.0) for t in tickers}


def test_la_sombra_no_cambia_ningun_candidato(monkeypatch):
    # El detector mide la frescura contra `date.today()` (no contra
    # AHORA): con una fecha fija, la prueba caducaba a los pocos días.
    titulares = [Titular("AAA receives FDA approval for drug", "Reuters", date.today().isoformat())]
    monkeypatch.setattr(run_mod, "YahooNewsProvider",
                        lambda metricas=None: type("N", (), {"titulares": lambda self, t: titulares,
                                                             "estado": {}})())
    sin = run_mod.construir_candidatos_diarios(["AAA"], {"AAA": _barras()}, _Prov(), CFG, True, ahora=AHORA)
    assert sin and sin[0].catalizador is not None          # hay algo que la sombra podría haber cambiado

    cli = _Cliente([{"news": [_art(["AAA"], "AAA misses estimates badly")], "next_page_token": None}])
    s = sn.Sombra(CFG, AHORA, fuente=sn.NoticiasAlpaca(cliente=cli))
    s.precargar(["AAA"])
    con = run_mod.construir_candidatos_diarios(["AAA"], {"AAA": _barras()}, _Prov(), CFG, True,
                                               ahora=AHORA, sombra=s)
    assert [(c.ticker, c.catalizador) for c in con] == [(c.ticker, c.catalizador) for c in sin]
    assert len(s.filas) == 1 and s.filas[0]["ticker"] == "AAA"
