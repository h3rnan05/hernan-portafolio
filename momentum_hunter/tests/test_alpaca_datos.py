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
    incorporar_subastas,
    parsear_barra,
    parsear_snapshot,
)
from momentum_hunter.factors.intradia import es_sesion_regular, vwap_real
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
        if str(url).endswith("/auctions"):
            return _Resp({"auctions": {}, "next_page_token": None})
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


# ------------------------- subasta de apertura y de cierre -------------------------

def _serie_gapper():
    """Cinco minutos de continuo alrededor de 80, sin el cruce. La de
    las 13:30 es la vela donde Yahoo habría metido la apertura."""
    marcas = [f"2026-09-28T13:{30 + i:02d}:00+00:00" for i in range(5)]
    o = [80.0, 81.0, 81.0, 82.0, 82.0]
    h = [82.0, 82.0, 83.0, 83.0, 84.0]
    lo = [79.0, 80.0, 80.0, 81.0, 81.0]
    c = [81.0, 81.0, 82.0, 82.0, 83.0]
    vol = [1_000_000.0, 100_000.0, 100_000.0, 100_000.0, 100_000.0]
    return marcas, o, h, lo, c, vol


def _bi_de(listas, ticker="KOD"):
    marcas, o, h, lo, c, vol = listas
    return BarraIntradia(ticker, marcas, o, c, h, lo, vol)


def _print(cond, precio, cuando, exch, size=None):
    crudo = {"c": cond, "p": precio, "t": cuando, "x": exch}
    if size is not None:
        crudo["s"] = size
    return crudo


def _dia(o=None, c=None, fecha="2026-09-28"):
    return {"d": fecha, "o": o or [], "c": c or []}


def test_el_cruce_de_apertura_baja_el_vwap_y_no_se_cuenta_dos_veces():
    # KOD, 2026-09-28: Yahoo tenía 5.47M a 61.87 dentro de las 9:30 y
    # Alpaca no. 'O' y 'Q' del mismo sitio son ese único cruce.
    listas = _serie_gapper()
    antes = vwap_real(_bi_de(listas))
    dia = _dia(o=[
        _print("Q", 61.87, "2026-09-28T13:30:00.188390144Z", "Q"),
        _print("O", 61.87, "2026-09-28T13:30:00.200000000Z", "Q", size=5_470_000),
        _print("Q", 61.87, "2026-09-28T13:30:00.210000000Z", "Q", size=5_470_000),
    ])
    nuevas, aportes = incorporar_subastas(listas, [dia, dia])
    bi = _bi_de(nuevas)
    assert bi.open[0] == pytest.approx(61.87)
    assert bi.low[0] == pytest.approx(61.87)
    assert bi.high[0] == pytest.approx(82.0)
    assert bi.close[0] == pytest.approx(81.0)
    assert bi.volume[0] == pytest.approx(1_000_000 + 5_470_000)
    assert bi.volume[1] == pytest.approx(100_000)
    despues = vwap_real(bi)
    assert despues is not None and antes is not None
    assert despues < antes
    assert aportes[0]["apertura"]["volumen"] == pytest.approx(5_470_000)
    assert aportes[0]["apertura"]["precio"] == pytest.approx(61.87)
    assert aportes[0]["cierre"] is None


def test_dos_exchanges_se_suman_y_el_open_es_el_cruce_grande():
    listas = _serie_gapper()
    dia = _dia(o=[
        _print("O", 61.87, "2026-09-28T13:30:00.200Z", "Q", size=5_470_000),
        _print("O", 90.0, "2026-09-28T13:30:00.300Z", "P", size=1_000),
    ])
    nuevas, aportes = incorporar_subastas(listas, [dia])
    assert nuevas[5][0] == pytest.approx(1_000_000 + 5_470_000 + 1_000)
    assert nuevas[1][0] == pytest.approx(61.87)  # el open es el cruce grande
    assert nuevas[3][0] == pytest.approx(61.87)
    assert nuevas[2][0] == pytest.approx(90.0)  # el cruce chico igual abre el high
    assert aportes[0]["apertura"]["volumen"] == pytest.approx(5_471_000)


def test_sin_vela_de_las_9_30_el_cruce_es_la_vela_entera():
    # Si el minuto no existe, no hay close del continuo. El close no
    # puede quedarse en el precio del exchange que se insertó primero.
    marcas, o, h, lo, c, vol = _serie_gapper()
    listas = (marcas[1:], o[1:], h[1:], lo[1:], c[1:], vol[1:])
    dia = _dia(o=[
        _print("O", 90.0, "2026-09-28T13:30:00.100Z", "P", size=1_000),
        _print("O", 61.87, "2026-09-28T13:30:00.200Z", "Q", size=5_470_000),
    ])
    nuevas, _ = incorporar_subastas(listas, [dia])
    i = nuevas[0].index("2026-09-28T13:30:00+00:00")
    assert nuevas[1][i] == pytest.approx(61.87)
    assert nuevas[4][i] == pytest.approx(61.87)
    assert nuevas[2][i] == pytest.approx(90.0)
    assert nuevas[3][i] == pytest.approx(61.87)
    assert nuevas[5][i] == pytest.approx(5_471_000)


def test_un_size_cero_no_tapa_el_size_de_la_impresion_oficial():
    listas = _serie_gapper()
    dia = _dia(o=[
        _print("O", 61.87, "2026-09-28T13:30:00.100Z", "Q", size=0),
        _print("Q", 61.87, "2026-09-28T13:30:00.200Z", "Q", size=5_470_000),
    ])
    nuevas, aportes = incorporar_subastas(listas, [dia])
    assert nuevas[5][0] == pytest.approx(1_000_000 + 5_470_000)
    assert aportes[0]["apertura"]["volumen"] == pytest.approx(5_470_000)


def test_un_print_sin_size_no_inventa_volumen_ni_mueve_el_rango():
    listas = _serie_gapper()
    dia = _dia(o=[_print("Q", 61.87, "2026-09-28T13:30:00.188Z", "Q")])
    nuevas, aportes = incorporar_subastas(listas, [dia])
    assert nuevas[5] == list(listas[5])
    assert nuevas[1] == list(listas[1])
    assert nuevas[3] == list(listas[3])
    assert aportes[0]["apertura"]["volumen"] is None
    assert aportes[0]["apertura"]["sin_size"] is True
    assert aportes[0]["apertura"]["precio"] == pytest.approx(61.87)


def test_una_condicion_que_no_es_del_cruce_no_se_pliega():
    listas = _serie_gapper()
    dia = _dia(o=[_print("Z", 61.87, "2026-09-28T13:30:00.200Z", "Q", size=9_000_000)])
    nuevas, aportes = incorporar_subastas(listas, [dia])
    assert nuevas[5] == list(listas[5])
    assert aportes == []


def test_el_cierre_de_las_16_et_entra_en_la_vela_de_las_15_59():
    marcas, o, h, lo, c, vol = _serie_gapper()
    marcas.append("2026-09-28T19:59:00+00:00")
    o.append(10.0)
    h.append(10.2)
    lo.append(9.8)
    c.append(10.0)
    vol.append(100.0)
    dia = _dia(c=[
        _print("6", 10.5, "2026-09-28T20:00:00.120649216Z", "P", size=5_000),
        _print("M", 10.5, "2026-09-28T20:00:00.125925888Z", "P", size=5_000),
    ])
    nuevas, aportes = incorporar_subastas((marcas, o, h, lo, c, vol), [dia])
    assert "2026-09-28T20:00:00+00:00" not in nuevas[0]
    i = nuevas[0].index("2026-09-28T19:59:00+00:00")
    assert es_sesion_regular(nuevas[0][i])
    assert nuevas[5][i] == pytest.approx(5_100)
    assert nuevas[1][i] == pytest.approx(10.0)  # el open del continuo se conserva
    assert nuevas[4][i] == pytest.approx(10.5)  # close = cruce oficial
    assert nuevas[2][i] == pytest.approx(10.5)  # el high se abre hasta el cruce
    assert aportes[0]["cierre"]["volumen"] == pytest.approx(5_000)
    # '6' y 'M' no se suman.
    assert not es_sesion_regular("2026-09-28T20:00:00+00:00")


def test_el_cierre_de_invierno_no_se_mete_en_la_vela_de_las_15_59():
    # 21:00 UTC es el cierre de invierno. El minuto anterior (20:59)
    # tampoco entra en la sesión de verano: no se elige otro balde.
    listas = _serie_gapper()
    dia = _dia(c=[_print("6", 50.0, "2026-09-28T21:00:00.100Z", "P", size=8_000_000)])
    nuevas, aportes = incorporar_subastas(listas, [dia])
    assert nuevas[5] == list(listas[5])
    assert "2026-09-28T20:59:00+00:00" not in nuevas[0]
    assert aportes[0]["cierre"]["volumen"] is None
    assert aportes[0]["cierre"]["sin_size"] is False


def test_la_subasta_viaja_en_el_request_sip_y_mueve_la_vela(monkeypatch):
    velas = [
        _vela("2026-09-28T13:30:00Z", 1_000_000, precio=80.0),
        _vela("2026-09-28T13:31:00Z", 100_000, precio=81.0),
        _vela("2026-09-28T13:32:00Z", 100_000, precio=81.0),
        _vela("2026-09-28T13:33:00Z", 100_000, precio=82.0),
        _vela("2026-09-28T13:34:00Z", 100_000, precio=82.0),
    ]
    vistos = []

    def _get(url, params=None, headers=None, timeout=None):
        vistos.append((url, dict(params)))
        if str(url).endswith("/auctions"):
            return _Resp({"auctions": {"ACME": [_dia(o=[
                _print("O", 61.87, "2026-09-28T13:30:00.200Z", "Q", size=5_470_000),
            ])]}, "next_page_token": None})
        return _Resp({"bars": {"ACME": velas}, "next_page_token": None})

    monkeypatch.setattr(ad.requests, "get", _get)
    p = _provider()
    bi = p.barras_intradia(["acme"], "1m", "1d")["acme"]
    urls = [u for u, _ in vistos]
    assert any(u.endswith("/v2/stocks/auctions") for u in urls)
    sub = next(params for u, params in vistos if u.endswith("/auctions"))
    assert sub["feed"] == "sip"
    assert sub["symbols"] == "ACME"
    assert bi.volume[0] == pytest.approx(1_000_000 + 5_470_000)
    assert bi.open[0] == pytest.approx(61.87)
    assert bi.low[0] == pytest.approx(61.87)
    assert p.fallidos == []
    assert p.aportes_subasta["acme"][0]["apertura"]["volumen"] == pytest.approx(5_470_000)
    # La misma serie sin el cruce, para ver que el VWAP de verdad baja.
    sin = _bi_de((
        bi.timestamps, [80.0, 81.0, 81.0, 82.0, 82.0],
        [80.2, 81.2, 81.2, 82.2, 82.2], [79.8, 80.8, 80.8, 81.8, 81.8],
        [80.0, 81.0, 81.0, 82.0, 82.0], [1_000_000.0, 100_000.0, 100_000.0, 100_000.0, 100_000.0],
    ), ticker="acme")
    assert vwap_real(bi) < vwap_real(sin)


def test_subasta_caida_no_tira_el_ciclo_ni_marca_fallidos(monkeypatch):
    velas = [_vela(f"2026-09-28T14:{i:02d}:00Z", 100 + i) for i in range(6)]

    def _get(url, params=None, headers=None, timeout=None):
        if str(url).endswith("/auctions"):
            return _Resp({}, status=500)
        return _Resp({"bars": {"ACME": velas}})

    monkeypatch.setattr(ad.requests, "get", _get)
    p = _provider(reintentos=1)
    out = p.barras_intradia(["ACME"], "1m", "1d")
    assert out["ACME"].volume[0] == pytest.approx(100)
    assert p.fallidos == []
    assert p.aportes_subasta == {}


def test_iex_y_las_barras_que_no_son_de_minuto_no_piden_subasta(monkeypatch):
    urls = []

    def _get(url, params=None, headers=None, timeout=None):
        urls.append(url)
        if params and params.get("timeframe") == "1Day":
            return _Resp({"bars": {"ACME": _diarias(20)}})
        return _Resp({"bars": {"ACME": [_vela(f"2026-09-28T14:{i:02d}:00Z", 10) for i in range(6)]}})

    monkeypatch.setattr(ad.requests, "get", _get)
    _provider(feed="iex").barras_intradia(["ACME"], "1m", "1d")
    _provider().barras_intradia(["ACME"], "5m", "1d")
    _provider().barras(["ACME"])
    assert not any(str(u).endswith("/auctions") for u in urls)


def test_formatear_muestra_la_subasta_y_no_un_cero_inventado():
    intra = {"ACME": _intradia()}
    aportes = {"ACME": [{
        "dia": "2026-09-28",
        "apertura": {"precio": 61.87, "volumen": 5_470_000, "minuto": "2026-09-28T13:30:00+00:00", "sin_size": False},
        "cierre": {"precio": 70.0, "volumen": None, "minuto": None, "sin_size": True},
    }]}
    texto = formatear(
        ["ACME", "NADA"], intra, {}, {}, {},
        ahora=datetime(2026, 9, 28, 15, tzinfo=UTC),
        aportes_subasta=aportes,
    )
    assert "5470000" in texto
    assert "61.8700" in texto
    assert "sin size, no se plegó" in texto
    assert "x 0" not in texto
    assert "NADA: -" in texto
    # Sin el argumento, la tabla de siempre no gana una sección de subasta.
    sin = formatear(["ACME"], intra, {}, {}, {})
    assert "subasta plegada" not in sin
