"""Fail-closed de la fuente de precios en el hunter (2026-10-01).

Con el default (alpaca/sip) y el feed caído o sin llaves, el escaneo y
el rechequeo terminan sin entradas nuevas, sin pedirle nada a Yahoo y
dejándolo registrado. HTTP simulado: ninguna prueba sale a la red.
"""
from __future__ import annotations

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter import watchlist
from momentum_hunter.data import alpaca_datos as ad
from momentum_hunter.tests.test_run import _barras, _jsonl_de_escaneo, _preparar_main_escaneo
from momentum_hunter.tests.test_run_watchlist import AHORA, CFG, _candidato_diario, _preparar_watchlist


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.headers = {}

    def json(self):
        return {}


def _feed_por_defecto_sin_llaves(monkeypatch):
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)

    def _boom(*a, **k):
        raise AssertionError("sin llaves no debía haber HTTP")

    monkeypatch.setattr(ad.requests, "get", _boom)


def _yahoo_prohibido(monkeypatch):
    class _NoYahoo:
        def barras(self, *a, **k):
            raise AssertionError("no debía pedirle precios a Yahoo")

        barras_intradia = barras

        def metadata(self, tickers):
            return {}

    monkeypatch.setattr(run_mod, "YahooProvider", _NoYahoo)


def _sin_escribir_watchlist(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no debía escribirse la watchlist")

    monkeypatch.setattr(run_mod.watchlist, "guardar", _boom)


def test_escaneo_sin_llaves_termina_sin_entradas_y_queda_en_la_telemetria(monkeypatch, tmp_path):
    _preparar_main_escaneo(monkeypatch, tmp_path, {"AAA": _barras("AAA", 5.0, 500_000.0)})
    _feed_por_defecto_sin_llaves(monkeypatch)
    _yahoo_prohibido(monkeypatch)
    _sin_escribir_watchlist(monkeypatch)
    alertas = []
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda texto, **k: alertas.append(texto))

    with pytest.raises(SystemExit) as exc:
        run_mod.main()
    assert exc.value.code == 2
    assert alertas == []
    _, lineas = _jsonl_de_escaneo(tmp_path)
    assert lineas[-1]["errores"] == {"datos:FuentePreciosCaida": 1}


def test_escaneo_con_el_feed_caido_tras_reintentos_tampoco_cae_a_yahoo(monkeypatch, tmp_path):
    _preparar_main_escaneo(monkeypatch, tmp_path, {"AAA": _barras("AAA", 5.0, 500_000.0)})
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "s")
    llamadas = []

    def _get(url, params=None, headers=None, timeout=None):
        llamadas.append((url, params.get("feed")))
        return _Resp(503)

    monkeypatch.setattr(ad.requests, "get", _get)
    monkeypatch.setattr(ad.time, "sleep", lambda _s: None)
    _yahoo_prohibido(monkeypatch)
    _sin_escribir_watchlist(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        run_mod.main()
    assert exc.value.code == 2
    # Reintentó (3 intentos por defecto) contra el host de datos con feed=sip.
    assert len(llamadas) == 3
    assert all(u.startswith("https://data.alpaca.markets/") and f == "sip" for u, f in llamadas)


def test_rechequeo_sin_llaves_no_toca_la_watchlist_ni_avisa(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    antes = path.read_bytes()
    _feed_por_defecto_sin_llaves(monkeypatch)
    _yahoo_prohibido(monkeypatch)
    alertas = []
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda texto, **k: alertas.append(texto))
    monkeypatch.setattr(run_mod, "_registrar_fuente_watchlist", lambda provider, dry_run: None)

    with pytest.raises(SystemExit) as exc:
        run_mod.revisar_watchlist(CFG, None, dry_run=False, ahora=AHORA)
    assert exc.value.code == 2
    assert path.read_bytes() == antes
    assert alertas == []
