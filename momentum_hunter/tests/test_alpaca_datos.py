"""Proveedor de barras del feed y el respaldo a Yahoo. HTTP simulado:
estas pruebas no salen a la red ni usan claves de verdad."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import requests

from momentum_hunter.data import alpaca_datos as ad
from momentum_hunter.data import fuente
from momentum_hunter.data.alpaca_datos import (
    AlpacaProvider,
    ErrorDatosAlpaca,
    parsear_barra,
    parsear_snapshot,
)
from momentum_hunter.data.comparar_fuentes import formatear, nota_ratio, resumen_diario, resumen_intradia
from momentum_hunter.data.fuente import ProveedorConRespaldo, proveedor_configurado
from momentum_hunter.models import Barras, BarraIntradia


class _Resp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._payload


def _vela(iso: str, volumen, precio=10.0):
    return {"t": iso, "o": precio, "h": precio + 0.2, "l": precio - 0.2, "c": precio, "v": volumen}


def _diarias(n: int, volumen=1000.0, ultima_volumen=1000.0):
    """n velas diarias a las 14:30 UTC, volumen real. La última puede
    apartarse para ver que no se aplasta."""
    out = []
    for i in range(n):
        dia = 28 - (n - 1 - i)
        vol = ultima_volumen if i == n - 1 else volumen
        out.append(_vela(f"2026-09-{dia:02d}T14:30:00Z", vol, precio=10.0 + i * 0.01))
    return out


def _provider(**kw):
    base = dict(api_key="k", api_secret="s", feed="sip", pausa=0, reintentos=3, dormir=lambda _s: None)
    base.update(kw)
    return AlpacaProvider(**base)


# ------------------------- parseo: no inventar volumen -------------------------

def test_parsear_barra_descarta_volumen_ausente():
    assert parsear_barra({"t": "2026-09-28T14:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": None}) is None
    assert parsear_barra({"t": "2026-09-28T14:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1}) is None


def test_parsear_barra_conserva_un_cero_explicito():
    vela = parsear_barra({"t": "2026-09-28T14:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0})
    assert vela is not None
    assert vela[-1] == 0.0


def test_parsear_snapshot_no_inventa_precio():
    assert parsear_snapshot({})["precio"] is None
    assert parsear_snapshot({"latestTrade": {}})["precio"] is None
    assert parsear_snapshot({"latestTrade": {"p": 12.5}, "minuteBar": {"t": "2026-09-28T14:31:00Z", "v": 80}}) == {
        "precio": 12.5,
        "minuto": "2026-09-28T14:31:00+00:00",
        "volumen_minuto": 80.0,
    }


def test_fecha_diaria_es_epoch_utc_como_yahoo(monkeypatch):
    # `run._cierre_anterior` hace int(fechas[-1]) y lo lee en UTC. Un ISO
    # acá rompería el gap. 14:30Z sigue siendo el mismo día de calendario.
    payload = {"bars": {"ACME": _diarias(20)}, "next_page_token": None}
    monkeypatch.setattr(ad.requests, "get", lambda *a, **k: _Resp(payload))
    ahora = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
    out = _provider(ahora=lambda: ahora).barras(["ACME"])
    b = out["ACME"]
    ultimo = datetime.fromtimestamp(int(b.fechas[-1]), tz=UTC)
    assert ultimo.date().isoformat() == "2026-09-28"
    assert all(f.isdigit() for f in b.fechas)


def test_intradia_recorta_el_cero_final_y_no_un_cero_del_medio(monkeypatch):
    velas = [
        _vela("2026-09-28T14:00:00Z", 100),
        _vela("2026-09-28T14:01:00Z", 0),
        _vela("2026-09-28T14:02:00Z", 300),
        _vela("2026-09-28T14:03:00Z", 400),
        _vela("2026-09-28T14:04:00Z", 500),
        _vela("2026-09-28T14:05:00Z", 0),
        _vela("2026-09-28T14:06:00Z", None),
    ]
    monkeypatch.setattr(ad.requests, "get", lambda *a, **k: _Resp({"bars": {"ACME": velas}}))
    bi = _provider().barras_intradia(["ACME"])["ACME"]
    assert bi.volume == [100.0, 0.0, 300.0, 400.0, 500.0]
    assert bi.timestamps[-1] == "2026-09-28T14:04:00+00:00"
    assert 0.0 in bi.volume  # el cero del medio es un minuto sin trades, no se borra


def test_el_query_pide_sip_y_ajuste_por_split(monkeypatch):
    vistos = []

    def _get(url, params=None, headers=None, timeout=None):
        vistos.append((url, dict(params), set(headers)))
        return _Resp({"bars": {"ACME": _diarias(20)}})

    monkeypatch.setattr(ad.requests, "get", _get)
    _provider(feed="sip").barras(["acme"])
    url, params, headers = vistos[0]
    assert url.endswith("/v2/stocks/bars")
    assert params["feed"] == "sip"
    assert params["adjustment"] == "split"
    assert params["timeframe"] == "1Day"
    assert params["symbols"] == "ACME"
    assert "APCA-API-KEY-ID" in headers
    assert "APCA-API-SECRET-KEY" in headers
    # La clave del dict es la que pidió el caller, no la del feed.
    # (se comprueba en el retorno, abajo no: este test solo mira el query)


def test_la_clave_del_resultado_respeta_el_ticker_pedido(monkeypatch):
    monkeypatch.setattr(
        ad.requests, "get", lambda *a, **k: _Resp({"bars": {"ACME": _diarias(20)}}))
    out = _provider().barras(["acme"])
    assert list(out) == ["acme"]


def test_paginacion_une_y_no_duplica(monkeypatch):
    paginas = [
        {"bars": {"ACME": [_vela("2026-09-28T14:30:00Z", 10)]}, "next_page_token": "sig"},
        {"bars": {"ACME": [
            _vela("2026-09-28T14:30:00Z", 10),
            _vela("2026-09-28T14:31:00Z", 11),
        ]}, "next_page_token": None},
    ]
    # Hacen falta >= 5 intradía. Relleno en la segunda página.
    paginas[1]["bars"]["ACME"].extend(
        _vela(f"2026-09-28T14:{32 + i:02d}:00Z", 12 + i) for i in range(4)
    )
    llamadas = []

    def _get(url, params=None, headers=None, timeout=None):
        llamadas.append(params.get("page_token"))
        return _Resp(paginas[len(llamadas) - 1])

    monkeypatch.setattr(ad.requests, "get", _get)
    bi = _provider().barras_intradia(["ACME"], "1m", "1d")
    assert llamadas == [None, "sig"]
    assert bi["ACME"].timestamps[0] == "2026-09-28T14:30:00+00:00"
    assert len(bi["ACME"].timestamps) == len(set(bi["ACME"].timestamps))


def test_429_reintenta_y_401_no(monkeypatch):
    llamadas = {"n": 0}

    def _get(url, params=None, headers=None, timeout=None):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return _Resp({}, status=429, headers={"Retry-After": "0"})
        return _Resp({"bars": {"ACME": _diarias(20)}})

    monkeypatch.setattr(ad.requests, "get", _get)
    assert "ACME" in _provider().barras(["ACME"])
    assert llamadas["n"] == 2

    llamadas["n"] = 0

    def _auth(url, params=None, headers=None, timeout=None):
        llamadas["n"] += 1
        return _Resp({}, status=401)

    monkeypatch.setattr(ad.requests, "get", _auth)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider().barras(["ACME"])
    assert exc.value.codigo == "auth"
    assert llamadas["n"] == 1


def test_sin_claves_no_pega_a_la_red_y_no_loguea_el_secreto(monkeypatch, caplog):
    def _boom(*a, **k):
        raise AssertionError("no debía haber HTTP")

    monkeypatch.setattr(ad.requests, "get", _boom)
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        AlpacaProvider(feed="sip", pausa=0, dormir=lambda _s: None).barras(["ACME"])
    assert exc.value.codigo == "sin_credenciales"

    secreto = "CLAVESECRETA123"
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", secreto)
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "OTRA456")

    def _red(*a, **k):
        raise requests.ConnectionError(f"https://data.example/?key={secreto}")

    monkeypatch.setattr(ad.requests, "get", _red)
    caplog.set_level("WARNING")
    with pytest.raises(ErrorDatosAlpaca) as exc:
        AlpacaProvider(feed="sip", pausa=0, reintentos=2, dormir=lambda _s: None).barras(["ACME"])
    assert exc.value.codigo == "red"
    assert secreto not in caplog.text


def test_un_simbolo_ausente_en_200_no_es_fallo(monkeypatch):
    monkeypatch.setattr(
        ad.requests, "get", lambda *a, **k: _Resp({"bars": {"AAA": _diarias(20)}}))
    p = _provider()
    out = p.barras(["AAA", "BBB"])
    assert list(out) == ["AAA"]
    assert p.fallidos == []


def test_un_lote_caido_marca_solo_esos_simbolos(monkeypatch):
    def _get(url, params=None, headers=None, timeout=None):
        if "BBB" in params["symbols"]:
            return _Resp({}, status=500)
        return _Resp({"bars": {"AAA": _diarias(20)}})

    monkeypatch.setattr(ad.requests, "get", _get)
    monkeypatch.setattr(ad, "LOTE_DIARIO", 1)
    p = _provider(reintentos=1)
    out = p.barras(["AAA", "BBB"])
    assert "AAA" in out and "BBB" not in out
    assert p.fallidos == ["BBB"]


def test_simbolos_con_guion_se_piden_con_punto_y_vuelven_con_la_clave_pedida(monkeypatch):
    # La traducción es solo del query. La clave que busca el pipeline
    # sigue siendo la del universo (con guion).
    vistos = []

    def _get(url, params=None, headers=None, timeout=None):
        vistos.append(params["symbols"])
        syms = params["symbols"].split(",")
        return _Resp({"bars": {s: _diarias(20) for s in syms}})

    monkeypatch.setattr(ad.requests, "get", _get)
    out = _provider().barras(["UMH", "BH-A", "CMS-PB"])
    assert vistos == ["UMH,BH.A,CMS.PRB"]
    assert set(out) == {"UMH", "BH-A", "CMS-PB"}
    assert "BH.A" not in out and "CMS.PRB" not in out


def test_sufijo_no_verificado_no_se_envia_y_no_se_confunde_con_la_clase(monkeypatch):
    # `-PB` es preferida, no clase P. `-WT`/`-UN` no se leen como una
    # letra. `-RI`, `-R` y `-WTA` no se mandan: no hay traducción
    # comprobada y un símbolo adivinado no es un dato.
    vistos = []

    def _get(url, params=None, headers=None, timeout=None):
        vistos.append(params["symbols"])
        syms = params["symbols"].split(",")
        return _Resp({"bars": {s: _diarias(20) for s in syms}})

    monkeypatch.setattr(ad.requests, "get", _get)
    p = _provider()
    out = p.barras(["LZM-WT", "KCAC-UN", "BRK-B", "CMS-PB", "FOO-RI", "BAR-R", "NE-WTA"])
    assert vistos == ["LZM.WS,KCAC.U,BRK.B,CMS.PRB"]
    assert p.fallidos == ["FOO-RI", "BAR-R", "NE-WTA"]
    assert set(out) == {"LZM-WT", "KCAC-UN", "BRK-B", "CMS-PB"}
    assert all(t not in out for t in p.fallidos)


def test_preferida_sin_serie_no_se_pide_como_clase_p(monkeypatch):
    # ETI-P es preferida sin letra de serie. `.P` no está verificado y
    # una clase P de verdad es rara: va al respaldo, no al feed.
    vistos = []

    def _get(url, params=None, headers=None, timeout=None):
        vistos.append(params["symbols"])
        syms = params["symbols"].split(",")
        return _Resp({"bars": {s: _diarias(20) for s in syms}})

    monkeypatch.setattr(ad.requests, "get", _get)
    p = _provider()
    out = p.barras(["ETI-P", "AAA", "CMS-PB"])
    assert vistos == ["AAA,CMS.PRB"]
    assert "ETI.P" not in vistos[0]
    assert p.fallidos == ["ETI-P"]
    assert "ETI-P" not in out
    assert set(out) == {"AAA", "CMS-PB"}


def test_dos_tickers_al_mismo_simbolo_se_piden_una_vez_y_vuelven_los_dos(monkeypatch):
    # BH-A y BH.A son el mismo símbolo del feed. Pedirlo dos veces, o
    # quedarse con una sola clave, deja a la otra sin serie y sin
    # fallback.
    vistos = []

    def _get(url, params=None, headers=None, timeout=None):
        vistos.append(params["symbols"])
        syms = params["symbols"].split(",")
        assert len(syms) == len(set(syms))
        return _Resp({"bars": {s: _diarias(20) for s in syms}})

    monkeypatch.setattr(ad.requests, "get", _get)
    out = _provider().barras(["BH-A", "BH.A", "LZM-WT", "LZM.WS"])
    assert vistos == ["BH.A,LZM.WS"]
    assert set(out) == {"BH-A", "BH.A", "LZM-WT", "LZM.WS"}
    assert out["BH-A"].volume == out["BH.A"].volume
    assert out["LZM-WT"].fechas == out["LZM.WS"].fechas

    def _todo_400(url, params=None, headers=None, timeout=None):
        vistos.append(params["symbols"])
        return _Resp({}, status=400)

    monkeypatch.setattr(ad.requests, "get", _todo_400)
    p = _provider(reintentos=1)
    with pytest.raises(ErrorDatosAlpaca):
        p.barras(["X-WT", "X.WS"])
    assert p.fallidos == ["X-WT", "X.WS"]
    assert vistos[-1] == "X.WS"


def test_http_400_en_un_lote_solo_manda_al_respaldo_el_simbolo_invalido(monkeypatch):
    # Un 400 rechaza el lote entero. Se parte hasta que el inválido
    # queda solo en fallidos; los otros 99 salen de Alpaca.
    malo = "ZZZZ"
    buenos = [f"S{i:02d}" for i in range(99)]
    tickers = buenos + [malo]
    llamadas = []

    def _get(url, params=None, headers=None, timeout=None):
        syms = params["symbols"].split(",")
        llamadas.append(syms)
        if malo in syms:
            return _Resp({}, status=400)
        return _Resp({"bars": {s: _diarias(20) for s in syms}})

    monkeypatch.setattr(ad.requests, "get", _get)
    p = _provider(reintentos=1)
    out = p.barras(tickers)
    assert len(llamadas[0]) == 100 and malo in llamadas[0]
    assert llamadas[-1] == [malo]
    assert p.fallidos == [malo]
    assert malo not in out
    assert set(out) == set(buenos)
    assert out["S00"].volume[-1] == 1000.0


def test_http_500_no_se_parte(monkeypatch):
    # 5xx sigue tumbando el lote entero. Partirlo reintentaría un fallo
    # que no es de símbolo.
    def _get(url, params=None, headers=None, timeout=None):
        if "BBB" in params["symbols"]:
            return _Resp({}, status=500)
        syms = params["symbols"].split(",")
        return _Resp({"bars": {s: _diarias(20) for s in syms}})

    monkeypatch.setattr(ad.requests, "get", _get)
    monkeypatch.setattr(ad, "LOTE_DIARIO", 2)
    p = _provider(reintentos=1)
    out = p.barras(["AAA", "BBB", "CCC"])
    assert list(out) == ["CCC"]
    assert p.fallidos == ["AAA", "BBB"]


def test_ningun_lote_valido_no_se_convierte_en_cero(monkeypatch):
    # Todo 400: se aísla cada símbolo y, como ninguno respondió, se
    # relanza el mismo código de hoy. El cuerpo trae volumen 0 a
    # propósito: no puede colarse como serie.
    ceros = _diarias(20, volumen=0.0, ultima_volumen=0.0)

    def _get(url, params=None, headers=None, timeout=None):
        syms = params["symbols"].split(",")
        return _Resp({"bars": {s: ceros for s in syms}}, status=400)

    monkeypatch.setattr(ad.requests, "get", _get)
    p = _provider(reintentos=1)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        p.barras(["AAA", "BBB"])
    assert exc.value.codigo == "http_400"
    assert p.fallidos == ["AAA", "BBB"]


def test_http_400_de_un_lote_grande_no_baja_hasta_el_simbolo(monkeypatch):
    # Si las dos mitades también son 400, no es un símbolo suelto.
    # Partir hasta el singleton serían 2n-1 pedidos; se corta en tres
    # (el lote y sus dos mitades) y se pausa entre esos sub-pedidos.
    llamadas = []
    dormidos = []

    def _get(url, params=None, headers=None, timeout=None):
        llamadas.append(params["symbols"])
        return _Resp({}, status=400)

    monkeypatch.setattr(ad.requests, "get", _get)
    n = ad.UMBRAL_CORTE_400
    tickers = [f"T{i}" for i in range(n)]
    p = _provider(reintentos=1, pausa=0.2, dormir=lambda s: dormidos.append(s))
    with pytest.raises(ErrorDatosAlpaca) as exc:
        p.barras(tickers)
    assert exc.value.codigo == "http_400"
    assert len(llamadas) == 3
    assert len(llamadas) < 2 * n - 1
    assert p.fallidos == tickers
    assert dormidos == [0.2, 0.2]


def test_snapshots_olvida_el_codigo_de_la_llamada_anterior(monkeypatch):
    monkeypatch.setattr(
        ad.requests, "get",
        lambda *a, **k: _Resp({"AAA": {"latestTrade": {"p": 3.5}}}),
    )
    p = _provider()
    p.ultimo_codigo = "http_400"
    p.fallidos = ["VIEJO"]
    out = p.snapshots(["AAA"])
    assert out["AAA"]["precio"] == 3.5
    assert p.ultimo_codigo is None
    assert p.fallidos == []


# ------------------------- respaldo -------------------------

class _Primario:
    def __init__(self, barras=None, fallidos=None, error=None):
        self._barras = barras or {}
        self._error = error
        self.fallidos = list(fallidos or [])
        self.feed = "sip"

    def barras(self, tickers, dias=280):
        if self._error:
            raise self._error
        return dict(self._barras)

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        if self._error:
            raise self._error
        return {}

    def metadata(self, tickers):
        raise AssertionError("el feed no debería resolver metadata")


class _Yahoo:
    def __init__(self):
        self.pedidos = []

    def barras(self, tickers, dias=280):
        self.pedidos.append(list(tickers))
        return {t: Barras(t, ["1"], [1.0], [1.0], [1.0], [1.0], [100.0]) for t in tickers}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        self.pedidos.append(list(tickers))
        return {}

    def metadata(self, tickers):
        self.pedidos.append(("meta", list(tickers)))
        return {t: type("M", (), {"ticker": t})() for t in tickers}


def _barra(ticker="AAA"):
    return Barras(ticker, ["1"], [1.0], [1.0], [1.0], [1.0], [50.0])


def test_el_ciclo_caido_cae_entero_a_yahoo():
    yahoo = _Yahoo()
    p = ProveedorConRespaldo(
        _Primario(error=ErrorDatosAlpaca("sin_credenciales")), yahoo, feed="sip")
    out = p.barras(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}
    assert yahoo.pedidos == [["AAA", "BBB"]]
    info = p.informe_datos()
    assert info.configurada == "alpaca"
    assert info.fuente == "yahoo"
    assert info.feed is None
    assert info.fallbacks == 2
    assert info.latencia_ms is not None


def test_solo_los_lotes_fallidos_van_al_respaldo():
    yahoo = _Yahoo()
    p = ProveedorConRespaldo(
        _Primario(barras={"AAA": _barra("AAA")}, fallidos=["BBB"]), yahoo, feed="sip")
    out = p.barras(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}
    assert out["AAA"].volume == [50.0]  # la del feed, no la de Yahoo
    assert yahoo.pedidos == [["BBB"]]
    info = p.informe_datos()
    assert info.fuente == "mixto"
    assert info.feed == "sip"
    assert info.fallbacks == 1


def test_ventana_con_guion_en_cada_lote_no_cae_al_respaldo(monkeypatch):
    # Antes, un guion en el lote devolvía 400 y las 100 iban a Yahoo.
    # Traducidas, las tres tandas salen de Alpaca: fallbacks=0.
    monkeypatch.setattr(ad, "LOTE_DIARIO", 2)
    tickers = ["UMH", "BH-A", "AAA", "CMS-PB", "LZM-WT", "BBB"]

    def _get(url, params=None, headers=None, timeout=None):
        syms = params["symbols"].split(",")
        if any("-" in s for s in syms):
            return _Resp({}, status=400)
        return _Resp({"bars": {s: _diarias(20) for s in syms}})

    monkeypatch.setattr(ad.requests, "get", _get)
    yahoo = _Yahoo()
    p = ProveedorConRespaldo(_provider(), yahoo, feed="sip")
    out = p.barras(tickers)
    assert set(out) == set(tickers)
    assert out["BH-A"].volume[-1] == 1000.0
    assert out["CMS-PB"].volume[-1] == 1000.0
    assert yahoo.pedidos == []
    info = p.informe_datos()
    assert info.fallbacks == 0
    assert info.fuente == "alpaca"
    assert info.feed == "sip"


def test_el_aviso_de_respaldo_nombra_el_codigo_y_el_simbolo(monkeypatch, caplog):
    # El dict de telemetría no cambia de forma. El código y el símbolo
    # van al aviso, que es lo que se puede leer después de una corrida.
    def _get(url, params=None, headers=None, timeout=None):
        if "ZZZZ" in params["symbols"]:
            return _Resp({}, status=400)
        return _Resp({"bars": {"AAA": _diarias(20)}})

    monkeypatch.setattr(ad.requests, "get", _get)
    yahoo = _Yahoo()
    caplog.set_level("WARNING")
    p = ProveedorConRespaldo(_provider(reintentos=1), yahoo, feed="sip")
    out = p.barras(["AAA", "ZZZZ"])
    assert out["AAA"].volume[-1] == 1000.0  # feed
    assert out["ZZZZ"].volume == [100.0]  # solo este fue al respaldo
    assert yahoo.pedidos == [["ZZZZ"]]
    assert p.informe_datos().fuente == "mixto"
    assert p.informe_datos().fallbacks == 1
    assert "http_400" in caplog.text and "ZZZZ" in caplog.text


def test_un_simbolo_sin_velas_no_dispara_el_respaldo():
    # 200 y el símbolo no está: el feed dijo que no hay datos. Pedirle
    # la otra cinta mezclaría dos fuentes en el mismo cálculo.
    yahoo = _Yahoo()
    p = ProveedorConRespaldo(_Primario(barras={"AAA": _barra()}), yahoo, feed="sip")
    out = p.barras(["AAA", "BBB"])
    assert list(out) == ["AAA"]
    assert yahoo.pedidos == []
    assert p.informe_datos().fuente == "alpaca"
    assert p.informe_datos().fallbacks == 0


def test_metadata_sigue_en_yahoo_y_no_cuenta_como_respaldo():
    yahoo = _Yahoo()
    p = ProveedorConRespaldo(_Primario(barras={"AAA": _barra()}), yahoo, feed="sip")
    assert p.metadata(["AAA"])["AAA"].ticker == "AAA"
    assert yahoo.pedidos == [("meta", ["AAA"])]
    # No fue un pedido de precio: la fuente sigue sin afirmarse.
    assert p.informe_datos().fuente is None
    assert p.informe_datos().fallbacks == 0
    assert p.informe_datos().latencia_ms is None


def test_default_es_yahoo_y_no_construye_el_feed(monkeypatch):
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)

    def _explotar(*a, **k):
        raise AssertionError("no debía construirse el feed")

    monkeypatch.setattr(fuente, "AlpacaProvider", _explotar)
    creado = {}

    def _yahoo():
        creado["si"] = True
        return _Yahoo()

    p = proveedor_configurado(construir_yahoo=_yahoo)
    assert creado["si"] is True
    p.barras(["AAA"])
    info = p.informe_datos()
    assert info.configurada == "yahoo"
    assert info.fuente == "yahoo"
    assert info.feed is None
    assert info.fallbacks == 0
    assert info.latencia_ms is not None


def test_feed_invalido_se_queda_en_yahoo(monkeypatch):
    monkeypatch.setenv("MOMENTUM_DATA_PROVIDER", "alpaca")
    monkeypatch.setenv("ALPACA_DATA_FEED", "boats")
    p = proveedor_configurado(construir_yahoo=_Yahoo)
    assert p.informe_datos().configurada == "yahoo"


def test_alpaca_en_el_entorno_arma_el_respaldo(monkeypatch):
    monkeypatch.setenv("MOMENTUM_DATA_PROVIDER", "alpaca")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    vistos = {}

    class _Falso:
        def __init__(self, feed="sip", **kw):
            vistos["feed"] = feed
            self.fallidos = []

        def barras(self, tickers, dias=280):
            self.fallidos = []
            return {}

        def barras_intradia(self, *a, **k):
            self.fallidos = []
            return {}

    monkeypatch.setattr(fuente, "AlpacaProvider", _Falso)
    p = proveedor_configurado(construir_yahoo=_Yahoo)
    p.barras(["AAA"])
    assert vistos["feed"] == "iex"
    assert p.informe_datos().configurada == "alpaca"
    assert p.informe_datos().fuente == "alpaca"
    assert p.informe_datos().feed == "iex"


# ------------------------- comparación (pura) -------------------------

def _intradia():
    ts = [f"2026-09-28T14:{m:02d}:00+00:00" for m in range(6)]
    vol = [100.0, 100.0, 100.0, 100.0, 100.0, 500.0]
    px = [10.0, 10.0, 10.0, 10.0, 10.0, 12.0]
    return BarraIntradia("ACME", ts, px, px, [v - 1 for v in px], px, vol)


def test_resumen_intradia_usa_el_vwap_de_sesion_regular():
    ahora = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
    r = resumen_intradia(_intradia(), ahora)
    assert r["ultima_vela"] == "2026-09-28T14:05:00+00:00"
    assert r["vol_ultima"] == 500.0
    assert r["vol_sesion"] == 1000.0
    assert r["edad_s"] == 55 * 60
    # Precio típico = (h+l+c)/3. h=c, l=c-1 => (c + c - 1 + c) / 3 = c - 1/3.
    # Cinco velas a 10 con vol 100 y una a 12 con vol 500.
    tipico = lambda c: c - 1.0 / 3.0
    esperado = (5 * tipico(10.0) * 100 + tipico(12.0) * 500) / 1000
    assert r["vwap"] == pytest.approx(esperado)


def test_resumen_vacio_no_inventa_ceros():
    assert resumen_intradia(None)["vol_ultima"] is None
    assert resumen_diario(Barras("A", [], [], [], [], [], []))["vol_promedio_20"] is None
    assert resumen_diario(Barras("A", ["1"], [1], [1], [1], [1], [5]))["vol_promedio_20"] is None


def test_nota_ratio_solo_cuando_hay_ambos_lados():
    assert nota_ratio(None, 100) is None
    assert nota_ratio(100, 0) is None
    assert nota_ratio(100, 100) is None
    assert nota_ratio(50, 100) == "0.50x"


def test_formatear_marca_la_diferencia_y_no_un_ausente():
    intra = {"ACME": _intradia()}
    texto = formatear(["ACME", "NADA"], intra, {}, {}, {}, ahora=datetime(2026, 9, 28, 15, tzinfo=UTC))
    assert "NADA" in texto
    assert "ACME" in texto
    # NADA no tiene números: no puede figurar un 0 inventado en su fila de yahoo.
    fila_nada = next(l for l in texto.splitlines() if l.startswith("NADA") and "yahoo" in l)
    assert " 0 " not in f" {fila_nada} "


def test_la_telemetria_guarda_la_fuente_y_el_reporte_ignora_el_rechequeo(tmp_path):
    from momentum_hunter import reporte_semanal, telemetria

    m = telemetria.Metricas(modo="escaneo")
    m.fuente_datos_configurada = "alpaca"
    m.fuente_datos = "mixto"
    m.feed_datos = "sip"
    m.fallbacks_datos = 3
    m.latencia_datos_ms = 420.5
    d = m.como_dict()["datos"]
    assert d == {
        "configurada": "alpaca", "fuente": "mixto", "feed": "sip",
        "fallbacks": 3, "latencia_ms": 420.5,
    }
    # Una corrida vieja no se inventa un conteo.
    assert telemetria.Metricas().como_dict()["datos"]["fallbacks"] is None

    ahora = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
    telemetria.registrar_corrida(m, tmp_path, ahora, fuente="vps")
    rechequeo = telemetria.Metricas(modo="watchlist", fuente_datos="alpaca", fallbacks_datos=0)
    telemetria.registrar_corrida(rechequeo, tmp_path, ahora, fuente="vps")
    # El reporte semanal solo mira el escaneo: el tick de watchlist trae
    # 500 errores falsos que no pueden disparar la alarma.
    (tmp_path / "2026-09-28" / "vps" / "events.jsonl").write_text(
        json.dumps({
            "modo": "escaneo",
            "embudo": {"operables": {"large": 1}, "evaluadas": {"large": 1},
                       "con_alguna_noticia": {}, "con_catalizador": {}, "accionables": {}},
            "condiciones": {}, "errores": {}, "score_maximo": 90,
        }) + "\n" + json.dumps({
            "modo": "watchlist",
            "embudo": {}, "condiciones": {}, "errores": {"datos:ErrorDatosAlpaca": 500},
            "score_maximo": 0,
            "datos": {"configurada": "alpaca", "fuente": "yahoo", "feed": None,
                      "fallbacks": 4, "latencia_ms": 10},
        }) + "\n",
        encoding="utf-8",
    )
    texto = reporte_semanal.construir("2026-09-28", "2026-09-28", tmp_path)
    assert "Corridas registradas: 1" in texto
    assert "500" not in texto
