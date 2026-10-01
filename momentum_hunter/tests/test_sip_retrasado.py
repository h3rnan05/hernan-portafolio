"""SIP retrasado 16 min (plan sin SIP reciente) y códigos HTTP reales.
Sin red: `requests.get` y los proveedores son dobles."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from momentum_hunter.data import alpaca_datos as ad
from momentum_hunter.data import fuente, subastas
from momentum_hunter.data.alpaca_datos import (
    RETRASO_SIP_MIN,
    AlpacaProvider,
    ErrorDatosAlpaca,
    es_rechazo,
    modo_sip_retrasado,
)
from momentum_hunter.data.fuente import ProveedorConRespaldo, comparar_diarias, proveedor_configurado
from momentum_hunter.models import Barras

AHORA = datetime(2026, 10, 1, 17, 0, tzinfo=UTC)   # 13:00 ET, sesión abierta


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._payload


def _provider(**kw):
    base = dict(api_key="k", api_secret="s", feed="sip", pausa=0, reintentos=3,
                dormir=lambda _s: None, ahora=lambda: AHORA)
    base.update(kw)
    return AlpacaProvider(**base)


def _epoch(fecha: str, hora="13:30") -> str:
    return str(int(datetime.fromisoformat(f"{fecha}T{hora}:00+00:00").timestamp()))


def _b(t, filas):
    """filas: [(fecha, close, volume)]"""
    return Barras(t, [_epoch(f) for f, _, _ in filas], [c for _, c, _ in filas],
                  [c for _, c, _ in filas], [c for _, c, _ in filas], [c for _, c, _ in filas],
                  [v for _, _, v in filas])


# ---------------------------- códigos reales ----------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_401_y_403_se_registran_con_su_codigo_y_no_se_reintentan(monkeypatch, status):
    n = {"n": 0}

    def _get(*a, **k):
        n["n"] += 1
        return _Resp({"message": "x"}, status=status)

    monkeypatch.setattr(ad.requests, "get", _get)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider().barras(["ACME"])
    assert exc.value.codigo == f"http_{status}"
    assert n["n"] == 1
    assert es_rechazo(f"http_{status}") and es_rechazo("auth") and not es_rechazo("http_500")


def test_el_aviso_del_lote_lleva_el_codigo_real(monkeypatch, caplog):
    monkeypatch.setattr(ad.requests, "get", lambda *a, **k: _Resp({}, status=403))
    caplog.set_level(logging.WARNING, logger="momentum_hunter.data.alpaca")
    with pytest.raises(ErrorDatosAlpaca):
        _provider().barras(["ACME"])
    assert "http_403: ACME" in caplog.text
    assert "(auth" not in caplog.text


# ------------------------------ el corte ------------------------------

def _captura(monkeypatch):
    vistos = []

    def _get(url, params=None, headers=None, timeout=None):
        vistos.append((url, dict(params)))
        return _Resp({"bars": {}, "next_page_token": None})

    monkeypatch.setattr(ad.requests, "get", _get)
    return vistos


def test_retraso_corre_el_end_de_diarias_y_minuto(monkeypatch):
    vistos = _captura(monkeypatch)
    p = _provider(retraso_min=RETRASO_SIP_MIN)
    p.barras(["ACME"])
    p.barras_intradia(["ACME"])
    corte = (AHORA - timedelta(minutes=16)).isoformat(timespec="seconds")
    assert [q["end"] for _, q in vistos] == [corte, corte]
    assert all(q["feed"] == "sip" for _, q in vistos)
    assert p.corte == AHORA - timedelta(minutes=16)
    assert RETRASO_SIP_MIN >= 16   # medido: 15 min es el borde exacto del 403


def test_sin_retraso_el_end_sigue_siendo_ahora(monkeypatch):
    vistos = _captura(monkeypatch)
    _provider().barras(["ACME"])
    assert vistos[0][1]["end"] == AHORA.isoformat(timespec="seconds")


def test_iex_ignora_el_retraso(monkeypatch):
    vistos = _captura(monkeypatch)
    p = _provider(feed="iex", retraso_min=16)
    p.barras_intradia(["ACME"])
    assert p.retraso_min is None
    assert vistos[0][1]["end"] == AHORA.isoformat(timespec="seconds")


@pytest.mark.parametrize("malo", [0, -5, True])
def test_retraso_invalido_no_se_acepta(malo):
    with pytest.raises(ErrorDatosAlpaca):
        _provider(retraso_min=malo)


# ------------------------------- el modo -------------------------------

@pytest.mark.parametrize("valor,esperado", [
    (None, "sombra"), ("", "sombra"), ("off", "off"), ("ON", "on"),
    (" sombra ", "sombra"), ("si", "sombra"), ("1", "sombra"),
])
def test_modo_default_y_valores_raros_quedan_en_sombra(monkeypatch, valor, esperado):
    if valor is None:
        monkeypatch.delenv("MOMENTUM_SIP_RETRASADO", raising=False)
    else:
        monkeypatch.setenv("MOMENTUM_SIP_RETRASADO", valor)
    assert modo_sip_retrasado() == esperado


# ---------------------- dobles para el proveedor ----------------------

class _Yahoo:
    def __init__(self):
        self.pedidos = []

    def barras(self, tickers, dias=280):
        self.pedidos.append(("d", list(tickers)))
        return {t: _b(t, [("2026-09-30", 10.0, 1000.0), ("2026-10-01", 11.0, 500.0)]) for t in tickers}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        self.pedidos.append(("m", list(tickers)))
        return {"YH": "minuto-yahoo"}

    def metadata(self, tickers):
        return {}


class _Alpaca:
    instancias: list = []

    def __init__(self, feed="sip", retraso_min=None, error=None, fallidos=(), **kw):
        self.feed = feed
        self.retraso_min = retraso_min
        self.kw = kw
        self.error = error
        self._fallidos = list(fallidos)
        self.fallidos = []
        self.ultimo_codigo = None
        self.corte = None
        self.pedidos = []
        _Alpaca.instancias.append(self)

    def barras(self, tickers, dias=280):
        self.pedidos.append(("d", list(tickers)))
        if self.error:
            raise self.error
        self.corte = AHORA - timedelta(minutes=16)
        self.fallidos = [t for t in self._fallidos if t in tickers]
        if self.fallidos:
            self.ultimo_codigo = "http_500"
        return {t: _b(t, [("2026-09-30", 10.0, 1000.0), ("2026-10-01", 10.9, 450.0)])
                for t in tickers if t not in self.fallidos}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        self.pedidos.append(("m", list(tickers)))
        raise AssertionError("en sombra/on el minuto no se pide a Alpaca")


@pytest.fixture
def entorno_sip(monkeypatch):
    monkeypatch.setenv("MOMENTUM_DATA_PROVIDER", "alpaca")
    monkeypatch.setenv("ALPACA_DATA_FEED", "sip")
    monkeypatch.delenv("MOMENTUM_METADATA_PROVIDER", raising=False)
    monkeypatch.setenv("MOMENTUM_SIP_STREAM", "sombra")
    _Alpaca.instancias = []
    monkeypatch.setattr(fuente, "AlpacaProvider", _Alpaca)
    return monkeypatch


def test_sombra_decide_con_yahoo_y_compara_sip_sin_mezclar(entorno_sip, caplog):
    entorno_sip.delenv("MOMENTUM_SIP_RETRASADO", raising=False)   # default
    yahoo = _Yahoo()
    caplog.set_level(logging.INFO, logger="momentum_hunter.data.fuente")
    p = proveedor_configurado(construir_yahoo=lambda: yahoo)
    out = p.barras(["AAA", "BBB"])
    # Lo que decide es exactamente lo de Yahoo, ni una vela del SIP.
    assert out["AAA"].volume == [1000.0, 500.0] and out["AAA"].close[-1] == 11.0
    primario, sombra = _Alpaca.instancias
    assert primario.pedidos == []          # el pedido que daría 403 ya no se hace
    assert sombra.retraso_min == RETRASO_SIP_MIN and sombra.kw.get("reintentos") == 1
    assert sombra.pedidos == [("d", ["AAA", "BBB"])]
    info = p.informe_datos()
    assert (info.fuente, info.feed, info.fallbacks) == ("yahoo", None, 0)
    assert "sombra sip retrasado:" in caplog.text
    assert p.ultima_comparacion["n_comunes"] == 2
    assert p.ultima_comparacion["vol_hoy_sip_sobre_yahoo"]["mediana"] == pytest.approx(0.9)
    # El minuto va directo a Yahoo: mismo dato que el viejo 403 -> respaldo.
    assert p.barras_intradia(["CCC"]) == {"YH": "minuto-yahoo"}
    assert yahoo.pedidos[-1] == ("m", ["CCC"])


def test_sombra_que_falla_no_toca_la_decision(entorno_sip, caplog):
    entorno_sip.setenv("MOMENTUM_SIP_RETRASADO", "sombra")

    class _Rota(_Alpaca):
        def barras(self, tickers, dias=280):
            raise ErrorDatosAlpaca("http_403")

    entorno_sip.setattr(fuente, "AlpacaProvider", _Rota)
    p = proveedor_configurado(construir_yahoo=_Yahoo)
    out = p.barras(["AAA"])
    assert out["AAA"].volume == [1000.0, 500.0]
    assert "sombra sip retrasado: no disponible (http_403)" in caplog.text
    assert p.informe_datos().fallbacks == 0


def test_on_diarias_del_sip_retrasado_y_respaldo_solo_para_fallidos(entorno_sip):
    entorno_sip.setenv("MOMENTUM_SIP_RETRASADO", "on")

    class _ConFallido(_Alpaca):
        def __init__(self, **kw):
            super().__init__(fallidos=["BBB"], **kw)

    entorno_sip.setattr(fuente, "AlpacaProvider", _ConFallido)
    yahoo = _Yahoo()
    p = proveedor_configurado(construir_yahoo=lambda: yahoo)
    out = p.barras(["AAA", "BBB"])
    (primario,) = _Alpaca.instancias
    assert primario.retraso_min == RETRASO_SIP_MIN
    assert out["AAA"].volume == [1000.0, 450.0]       # SIP
    assert out["BBB"].volume == [1000.0, 500.0]       # Yahoo, solo el fallido
    assert yahoo.pedidos == [("d", ["BBB"])]
    assert p.informe_datos().fallbacks == 1
    # Minuto: Yahoo por diseño (IEX no es el consolidado), no fallback.
    assert p.barras_intradia(["CCC"]) == {"YH": "minuto-yahoo"}
    info = p.informe_datos()
    assert info.fuente == "mixto" and info.fallbacks == 1


def test_off_es_el_camino_viejo(entorno_sip):
    entorno_sip.setenv("MOMENTUM_SIP_RETRASADO", "off")
    p = proveedor_configurado(construir_yahoo=_Yahoo)
    (primario,) = _Alpaca.instancias
    assert primario.retraso_min is None
    out = p.barras(["AAA"])
    assert out["AAA"].volume[-1] == 450.0 and primario.pedidos == [("d", ["AAA"])]


def test_iex_no_entra_en_el_modo(entorno_sip):
    entorno_sip.setenv("ALPACA_DATA_FEED", "iex")
    entorno_sip.setenv("MOMENTUM_SIP_RETRASADO", "on")
    p = proveedor_configurado(construir_yahoo=_Yahoo)
    (primario,) = _Alpaca.instancias
    assert primario.feed == "iex" and primario.retraso_min is None
    assert p._modo == "off"


def test_constructor_directo_sigue_siendo_off():
    p = ProveedorConRespaldo(_Alpaca(), _Yahoo(), feed="sip")
    assert p._modo == "off" and p._sombra is None


# --------------------------- la comparación ---------------------------

def test_comparar_no_cuenta_faltantes_como_cero():
    yahoo = {
        "AAA": _b("AAA", [("2026-09-30", 10.0, 1000.0), ("2026-10-01", 11.0, 500.0)]),
        "BBB": _b("BBB", [("2026-09-30", 5.0, 2000.0), ("2026-10-01", 5.5, 0.0)]),
        "CCC": _b("CCC", [("2026-09-30", 7.0, 100.0), ("2026-10-01", 7.0, 50.0)]),
        "SOLOY": _b("SOLOY", [("2026-10-01", 1.0, 1.0)]),
    }
    sip = {
        "AAA": _b("AAA", [("2026-09-30", 10.0, 1010.0), ("2026-10-01", 11.11, 400.0)]),
        "BBB": _b("BBB", [("2026-09-30", 5.0, 2000.0), ("2026-10-01", 5.5, 10.0)]),
        "CCC": _b("CCC", [("2026-09-30", 7.0, 100.0)]),     # sin fila de hoy
    }
    r = comparar_diarias(yahoo, sip, "2026-10-01")
    assert (r["n_yahoo"], r["n_sip"], r["n_comunes"], r["solo_yahoo"]) == (4, 3, 3, 1)
    assert r["hoy_sin_fila_sip"] == 1
    assert r["vol_ayer_sip_sobre_yahoo"]["n"] == 3
    # BBB con volumen Yahoo 0 no entra (no hay razón); CCC sin fila tampoco.
    assert r["vol_hoy_sip_sobre_yahoo"] == {"n": 1, "p10": 0.8, "mediana": 0.8, "p90": 0.8}
    assert r["cierre_hoy_dif_pct"]["n"] == 2
    vacia = comparar_diarias({}, {}, "2026-10-01")
    assert vacia["vol_hoy_sip_sobre_yahoo"] is None and vacia["n_comunes"] == 0


# ------------------------------ subastas ------------------------------

class _Transporte:
    def __init__(self):
        self.params = []

    def _paginas(self, ruta, params):
        self.params.append(params)
        return [{"auctions": {}}]


@pytest.mark.parametrize("modo,minutos", [("on", 16), ("sombra", 0), ("off", 0)])
def test_subastas_corren_el_end_solo_en_on(monkeypatch, modo, minutos):
    monkeypatch.setenv("MOMENTUM_SIP_RETRASADO", modo)
    t = _Transporte()
    subastas.descargar(["ACME"], ahora=AHORA, transporte=t)
    assert t.params[0]["end"] == (AHORA - timedelta(minutes=minutos)).isoformat(timespec="seconds")
    assert t.params[0]["feed"] == "sip"
