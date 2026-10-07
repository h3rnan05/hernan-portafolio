"""Volumen SIP retrasado para el filtro de universo con respaldo IEX (2026-10-07).

Aprobado por el dueño: cuando la diaria del escaneo vino de IEX (#256,
SIP dio 403 de plan), el volumen del filtro sale de SIP con `end` <=
ahora-15 min. Con SIP normal no cambia nada. Si el SIP retrasado falla,
fail-closed: el ticker queda sin dato (nunca 0 ni volumen IEX).
HTTP simulado: no sale a la red ni usa claves."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter import telemetria
from momentum_hunter.config import CONFIG
from momentum_hunter.data import alpaca_datos as ad
from momentum_hunter.data import volumen_retrasado as vr
from momentum_hunter.data.alpaca_datos import AlpacaProvider, ErrorDatosAlpaca
from momentum_hunter.data.fuente import ConMetadataAparte, ProveedorAlpaca
from momentum_hunter.models import Barras, Metadata

MSG_SIP = {"message": "subscription does not permit querying recent SIP data"}
AHORA = datetime(2026, 10, 7, 16, 0, tzinfo=UTC)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._payload


def _velas(n, precio, vol):
    return [
        {"t": (datetime(2026, 9, 1, 4, tzinfo=UTC) + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
         "o": precio, "h": precio, "l": precio, "c": precio, "v": vol}
        for i in range(n)
    ]


def _cuerpo(params, precio, vol_por_simbolo, n=25):
    simbolos = params.get("symbols", "").split(",")
    return {"bars": {s: _velas(n, precio, vol_por_simbolo.get(s, 0.0)) for s in simbolos
                     if s in vol_por_simbolo}}


class _Http:
    """SIP en vivo = `sip_vivo` (status), SIP retrasado (end <= ahora-15) =
    `sip_retrasado`, IEX = volumen chico. Anota cada pedido."""

    def __init__(self, sip_vivo=403, sip_retrasado=200, vol_sip=None, vol_iex=None, precio=10.0,
                 reloj=lambda: AHORA):
        self.reloj = reloj
        self.sip_vivo = sip_vivo
        self.sip_retrasado = sip_retrasado
        self.vol_sip = vol_sip or {}
        self.vol_iex = vol_iex or {}
        self.precio = precio
        self.llamadas: list[dict] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        p = dict(params or {})
        self.llamadas.append(p)
        if p.get("feed") == "iex":
            return _Resp(_cuerpo(p, self.precio, self.vol_iex))
        fin = datetime.fromisoformat(p["end"])
        retrasado = fin <= self.reloj() - timedelta(minutes=15)
        status = self.sip_retrasado if retrasado else self.sip_vivo
        if status == 403:
            return _Resp(MSG_SIP, status=403)
        if status != 200:
            return _Resp({"message": "x"}, status=status)
        return _Resp(_cuerpo(p, self.precio, self.vol_sip))

    def sip_retrasados(self):
        return [c for c in self.llamadas if c.get("feed") == "sip"
                and datetime.fromisoformat(c["end"]) <= self.reloj() - timedelta(minutes=15)]


def _proveedor():
    prim = AlpacaProvider(api_key="k", api_secret="s", feed="sip", pausa=0, reintentos=1,
                          dormir=lambda _s: None, ahora=lambda: AHORA)
    return ProveedorAlpaca(prim, lambda: None, feed="sip")


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.delenv(vr.ENV_VOLUMEN_SIP_RETRASADO, raising=False)
    monkeypatch.delenv(ad.ENV_RESPALDO_IEX, raising=False)
    ad.reiniciar_respaldo_iex()
    yield
    ad.reiniciar_respaldo_iex()


# ------------------------- feed de la diaria por ticker -------------------------

def test_feed_diario_por_ticker_dice_sip_o_iex(monkeypatch):
    http = _Http(sip_vivo=200, vol_sip={"AAA": 2e6})
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    prov.barras(["AAA"])
    assert prov.feed_diario_por_ticker() == {"AAA": "sip"}

    ad.reiniciar_respaldo_iex()
    http2 = _Http(sip_vivo=403, vol_iex={"AAA": 5e4})
    monkeypatch.setattr(ad.requests, "get", http2)
    prov2 = _proveedor()
    prov2.barras(["AAA"])
    assert prov2.feed_diario_por_ticker() == {"AAA": "iex"}


# ------------------------- end siempre <= ahora-15 min -------------------------

def test_end_del_sip_retrasado_es_ahora_menos_16_y_nunca_menos_de_15(monkeypatch):
    http = _Http(vol_sip={"AAA": 2e6})
    monkeypatch.setattr(ad.requests, "get", http)
    solo = _proveedor()._primario.solo_sip()
    out = solo.barras_sip_retrasadas(["AAA"])
    assert "AAA" in out
    assert http.llamadas, "no pidió nada"
    for c in http.llamadas:
        assert c["feed"] == "sip"
        assert datetime.fromisoformat(c["end"]) <= AHORA - timedelta(minutes=15)
        assert datetime.fromisoformat(c["end"]) == AHORA - timedelta(minutes=ad.RETRASO_SIP_MIN)
    with pytest.raises(ValueError):
        solo.barras_sip_retrasadas(["AAA"], retraso_min=14)


def test_sip_retrasado_nunca_cae_a_iex_ni_en_instancia_con_respaldo(monkeypatch):
    http = _Http(sip_retrasado=403, vol_iex={"AAA": 5e4})
    monkeypatch.setattr(ad.requests, "get", http)
    solo = _proveedor()._primario.solo_sip()
    with pytest.raises(ErrorDatosAlpaca) as ex:
        solo.barras_sip_retrasadas(["AAA"])
    assert ex.value.codigo == "http_403"
    assert all(c["feed"] == "sip" for c in http.llamadas)
    assert ad.fallback_iex_en_proceso() is False
    # La instancia con respaldo IEX no puede pedir "SIP retrasado".
    with pytest.raises(ErrorDatosAlpaca):
        _proveedor()._primario.barras_sip_retrasadas(["AAA"])


def test_el_pedido_retrasado_no_ensucia_feed_usado_de_precios(monkeypatch):
    http = _Http(vol_sip={"AAA": 2e6}, vol_iex={"AAA": 5e4})
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    prov.barras(["AAA"])
    prov.volumen_sip_retrasado(["AAA"])
    assert prov.informe_datos().feed_usado == "iex"


# ------------------------- preparar: IEX + SIP retrasado OK -------------------------

def test_iex_mas_sip_retrasado_ok_el_filtro_usa_volumen_sip(monkeypatch):
    # IEX ve 50 mil/día (< 1M large), SIP consolidado 2M (>= 1M): con
    # volumen IEX quedaría fuera por vol_bajo_large.
    http = _Http(vol_sip={"BIG": 2e6}, vol_iex={"BIG": 5e4}, precio=50.0)
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    barras = prov.barras(["BIG"])
    assert barras["BIG"].volume[-1] == 5e4
    assert run_mod._clasificar_banda_de_universo(barras["BIG"], CONFIG) == (None, "vol_bajo_large")

    vf = vr.preparar(prov, barras)
    serie = vf.serie("BIG", barras["BIG"])
    assert serie is not None and serie.volume[-1] == 2e6
    assert run_mod._clasificar_banda_de_universo(barras["BIG"], CONFIG, volumen=serie) == ("large", None)
    assert vf.etiqueta == "sip_retrasado"
    assert vf.sin_dato == set()
    assert len(http.sip_retrasados()) == 1


def test_precio_sigue_saliendo_de_iex():
    iex = Barras("X", [str(i) for i in range(25)], [5.0] * 25, [5.0] * 25, [5.0] * 25, [5.0] * 25, [1e3] * 25)
    sip = Barras("X", [str(i) for i in range(25)], [99.0] * 25, [99.0] * 25, [99.0] * 25, [99.0] * 25, [5e5] * 25)
    # Precio 5 (small) del IEX, volumen 500k del SIP: banda small.
    assert run_mod._clasificar_banda_de_universo(iex, CONFIG, volumen=sip) == ("small", None)


# ------------------------- SIP normal: no cambia nada -------------------------

def test_sip_normal_no_pide_retrasado_y_no_cambia_nada(monkeypatch):
    http = _Http(sip_vivo=200, vol_sip={"AAA": 2e6}, precio=50.0)
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    barras = prov.barras(["AAA"])
    vf = vr.preparar(prov, barras)
    assert vf.serie("AAA", barras["AAA"]) is barras["AAA"]
    assert vf.reemplazos == {} and vf.sin_dato == set()
    assert vf.etiqueta == "sip"
    assert http.sip_retrasados() == []
    assert all(c["feed"] == "sip" for c in http.llamadas)


def test_proveedor_sin_dato_de_feed_no_cambia_nada():
    class _Doble:
        def barras(self, *a, **k):
            return {}
    b = Barras("A", ["1"], [1.0], [1.0], [1.0], [1.0], [1.0])
    vf = vr.preparar(_Doble(), {"A": b})
    assert vf.serie("A", b) is b and vf.etiqueta is None
    m = telemetria.Metricas(modo="escaneo")
    vf.anotar(m)
    assert m.volumen_fuente is None and m.volumen_sin_dato is None


# ------------------------- SIP retrasado falla: fail-closed -------------------------

@pytest.mark.parametrize("status", [403, 401, 429, 500])
def test_sip_retrasado_falla_queda_sin_dato_fail_closed(monkeypatch, status, caplog):
    http = _Http(sip_retrasado=status, vol_iex={"BIG": 5e6}, precio=50.0)
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    barras = prov.barras(["BIG"])
    with caplog.at_level(logging.WARNING, logger="momentum_hunter.data.volumen_retrasado"):
        vf = vr.preparar(prov, barras)
    # Aunque el volumen IEX (5M) pasaría el piso, no se usa como consolidado.
    assert vf.serie("BIG", barras["BIG"]) is None
    assert vf.sin_dato == {"BIG"}
    assert vf.codigo == f"http_{status}"
    assert vf.etiqueta == "sin_dato"
    assert run_mod._clasificar_banda_de_universo(
        barras["BIG"], CONFIG, volumen=None, sin_dato_volumen=True) == (None, vr.MOTIVO_SIN_DATO)
    assert "sin dato" in caplog.text


def test_ticker_que_no_vuelve_del_retrasado_queda_sin_dato_no_cero(monkeypatch):
    http = _Http(vol_sip={"AAA": 2e6}, vol_iex={"AAA": 5e4, "BBB": 5e4}, precio=50.0)
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    barras = prov.barras(["AAA", "BBB"])
    vf = vr.preparar(prov, barras)
    assert vf.serie("AAA", barras["AAA"]).volume[-1] == 2e6
    assert vf.serie("BBB", barras["BBB"]) is None
    assert vf.etiqueta == "sip_retrasado"
    m = telemetria.Metricas(modo="escaneo")
    vf.anotar(m)
    assert m.volumen_sin_dato == 1


def test_excepcion_inesperada_tambien_es_sin_dato():
    class _Roto:
        def feed_diario_por_ticker(self):
            return {"A": "iex"}

        def volumen_sip_retrasado(self, tickers):
            raise RuntimeError("boom")
    b = Barras("A", ["1"], [1.0], [1.0], [1.0], [1.0], [1.0])
    vf = vr.preparar(_Roto(), {"A": b})
    assert vf.serie("A", b) is None and vf.codigo == "RuntimeError"


def test_flag_apagado_usa_iex_rotulado(monkeypatch):
    monkeypatch.setenv(vr.ENV_VOLUMEN_SIP_RETRASADO, "0")
    http = _Http(vol_sip={"BIG": 2e6}, vol_iex={"BIG": 5e4}, precio=50.0)
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    barras = prov.barras(["BIG"])
    vf = vr.preparar(prov, barras)
    assert vf.serie("BIG", barras["BIG"]) is barras["BIG"]
    assert vf.etiqueta == "iex"
    assert http.sip_retrasados() == []


def test_con_metadata_aparte_pasa_los_metodos(monkeypatch):
    http = _Http(vol_sip={"BIG": 2e6}, vol_iex={"BIG": 5e4}, precio=50.0)
    monkeypatch.setattr(ad.requests, "get", http)
    prov = ConMetadataAparte(_proveedor(), metadata=None)
    barras = prov.barras(["BIG"])
    vf = vr.preparar(prov, barras)
    assert vf.etiqueta == "sip_retrasado"


# ------------------------- telemetría -------------------------

def test_telemetria_volumen_fuente_en_datos(monkeypatch):
    http = _Http(vol_sip={"BIG": 2e6}, vol_iex={"BIG": 5e4}, precio=50.0)
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _proveedor()
    vf = vr.preparar(prov, prov.barras(["BIG"]))
    m = telemetria.Metricas(modo="escaneo")
    vf.anotar(m)
    datos = m.como_dict()["datos"]
    assert datos["volumen_fuente"] == "sip_retrasado"
    assert datos["volumen_sin_dato"] == 0
    assert datos["volumen_retrasado_end"] == (AHORA - timedelta(minutes=16)).isoformat(timespec="seconds")
    assert datos["volumen_retrasado_codigo"] is None


# ------------------------- pipeline completo (main) -------------------------

def _preparar_main(monkeypatch, tmp_path, http):
    import sys
    # main usa el reloj real: el doble HTTP decide "retrasado" con ese reloj.
    http.reloj = lambda: datetime.now(UTC)
    monkeypatch.setenv("MOMENTUM_DATA_PROVIDER", "alpaca")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "s")
    monkeypatch.setattr(ad.requests, "get", http)
    monkeypatch.setattr(sys, "argv", ["momentum_hunter.run", "--no-catalizadores"])
    real = telemetria.registrar_corrida
    monkeypatch.setattr(
        run_mod.telemetria, "registrar_corrida",
        lambda m, dir_telemetria=tmp_path, ahora=None, fuente=None: real(
            m, dir_telemetria=tmp_path, ahora=ahora, fuente=fuente),
    )
    tickers = list(http.vol_iex)
    monkeypatch.setattr(run_mod, "_cargar_tickers", lambda args, **kw: tickers)
    monkeypatch.setattr(run_mod.universe, "tickers", lambda **kw: tickers)
    monkeypatch.setattr(run_mod, "_revisar_resumen_cierre", lambda *a, **k: None)
    monkeypatch.setattr(run_mod, "_verificar_historia_corporativa", lambda b, ahora: (b, None))

    class _Meta:
        def metadata(self, ts):
            return {t: Metadata(ticker=t, market_cap=100_000_000.0) for t in ts}
    monkeypatch.setattr(run_mod, "YahooProvider", lambda: _Meta())
    vistos = {}

    def _diarios(validos, barras, provider, cfg, con_cat, bandas=None, metricas=None, ahora=None,
                 registro_noticias=None, volumenes=None, **kw):
        vistos["validos"] = list(validos)
        vistos["volumenes"] = volumenes
        for t in validos:
            metricas.sumar(metricas.operables, bandas.get(t, "small"))
        return []
    monkeypatch.setattr(run_mod, "construir_candidatos_diarios", _diarios)
    return vistos


def _linea(tmp_path):
    import json
    archivos = list(tmp_path.rglob("*.jsonl"))
    assert archivos
    return json.loads(archivos[0].read_text().strip().splitlines()[-1])


def test_main_con_iex_usa_sip_retrasado_en_el_filtro(monkeypatch, tmp_path):
    http = _Http(vol_sip={"BIG": 2e6, "THIN": 2e5}, vol_iex={"BIG": 5e4, "THIN": 5e3}, precio=50.0)
    vistos = _preparar_main(monkeypatch, tmp_path, http)
    run_mod.main()
    assert vistos["validos"] == ["BIG"]
    assert vistos["volumenes"]["BIG"].volume[-1] == 2e6
    linea = _linea(tmp_path)
    assert linea["embudo"]["operables"] == {"large": 1}
    assert linea["embudo"]["rechazos_universo"] == {"vol_bajo_large": 1}
    assert linea["datos"]["volumen_fuente"] == "sip_retrasado"
    assert linea["datos"]["feed_usado"] == "iex"


def test_main_con_iex_y_retrasado_caido_no_evalua_nada(monkeypatch, tmp_path):
    http = _Http(sip_retrasado=500, vol_iex={"BIG": 5e6}, precio=50.0)
    vistos = _preparar_main(monkeypatch, tmp_path, http)
    run_mod.main()
    assert "validos" not in vistos
    linea = _linea(tmp_path)
    assert linea["embudo"]["rechazos_universo"] == {vr.MOTIVO_SIN_DATO: 1}
    assert linea["datos"]["volumen_fuente"] == "sin_dato"
    assert linea["datos"]["volumen_retrasado_codigo"] == "http_500"


def test_main_con_sip_normal_no_cambia(monkeypatch, tmp_path):
    http = _Http(sip_vivo=200, vol_sip={"BIG": 2e6, "THIN": 2e5}, vol_iex={"BIG": 0, "THIN": 0}, precio=50.0)
    vistos = _preparar_main(monkeypatch, tmp_path, http)
    run_mod.main()
    assert vistos["validos"] == ["BIG"]
    assert vistos["volumenes"] is None          # no se pasa nada nuevo
    assert http.sip_retrasados() == []
    linea = _linea(tmp_path)
    assert linea["datos"]["volumen_fuente"] == "sip"
    assert linea["datos"]["feed_usado"] == "sip"
