"""Respaldo IEX cuando SIP responde 403 de suscripción (2026-10-06).

Aprobado por el dueño: si un pedido con `feed=sip` recibe HTTP 403 de
PLAN, se repite el MISMO pedido con `feed=iex`. Nunca ante 401 ni otros
errores; si IEX también falla, fail-closed como hoy. Pegajoso por
proceso. Sin Yahoo. HTTP simulado: no sale a la red ni usa claves."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from momentum_hunter import telemetria
from momentum_hunter.data import alpaca_datos as ad
from momentum_hunter.data.alpaca_datos import AlpacaProvider, ErrorDatosAlpaca
from momentum_hunter.data.fuente import (
    FuentePreciosCaida,
    ProveedorAlpaca,
    informe_de,
    proveedor_configurado,
)

MSG_SIP = {"message": "subscription does not permit querying recent SIP data"}


class _Resp:
    def __init__(self, payload, status=200, headers=None, text=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        if text is not None:
            self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _diarias(n=25, simbolo="ACME"):
    velas = [
        {"t": f"2026-09-{i + 1:02d}T14:30:00Z", "o": 10.0, "h": 10.2, "l": 9.8, "c": 10.0, "v": 1000.0}
        for i in range(n)
    ]
    return {"bars": {simbolo: velas}}


def _provider(**kw):
    base = dict(api_key="k", api_secret="s", feed="sip", pausa=0, reintentos=1,
                dormir=lambda _s: None,
                ahora=lambda: datetime(2026, 10, 6, 15, 0, tzinfo=UTC))
    base.update(kw)
    return AlpacaProvider(**base)


class _Http:
    """Contesta según el `feed` del pedido y anota cada llamada."""

    def __init__(self, sip, iex=None):
        self.sip = sip
        self.iex = iex if iex is not None else (lambda: _Resp(_diarias()))
        self.llamadas: list[dict] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.llamadas.append({"url": url, **dict(params or {})})
        feed = (params or {}).get("feed")
        return (self.sip if feed == "sip" else self.iex)()

    def feeds(self):
        return [c.get("feed") for c in self.llamadas]


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.delenv(ad.ENV_RESPALDO_IEX, raising=False)
    ad.reiniciar_respaldo_iex()
    yield
    ad.reiniciar_respaldo_iex()


# ------------------------------ 403 de plan → IEX ------------------------------

def test_403_de_plan_repite_el_mismo_pedido_con_iex(monkeypatch, caplog):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _provider()
    with caplog.at_level(logging.WARNING, logger="momentum_hunter.data.alpaca"):
        out = prov.barras(["ACME"])
    assert "ACME" in out and len(out["ACME"]) == 25
    assert http.feeds() == ["sip", "iex"]
    # El MISMO pedido: solo cambia el feed.
    sip, iex = http.llamadas
    assert {k: v for k, v in sip.items() if k != "feed"} == {k: v for k, v in iex.items() if k != "feed"}
    assert prov.feeds_usados == {"iex"}
    assert ad.fallback_iex_en_proceso() is True
    texto = caplog.text
    assert "feed_usado=iex" in texto and "fallback_iex=true" in texto
    # El cuerpo de la respuesta no se registra.
    assert "does not permit" not in texto


def test_403_de_plan_por_texto_tambien_cae_a_iex(monkeypatch):
    http = _Http(sip=lambda: _Resp(ValueError("no json"), status=403,
                                   text="subscription does not permit querying recent SIP data"))
    monkeypatch.setattr(ad.requests, "get", http)
    assert "ACME" in _provider().barras(["ACME"])
    assert http.feeds() == ["sip", "iex"]


def test_snapshots_tambien_caen_a_iex(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403),
                 iex=lambda: _Resp({"ACME": {"latestTrade": {"p": 3.5}}}))
    monkeypatch.setattr(ad.requests, "get", http)
    out = _provider().snapshots(["ACME"])
    assert out["ACME"]["precio"] == 3.5
    assert http.feeds() == ["sip", "iex"]


# ------------------------------ sin respaldo ------------------------------

def test_401_no_cae_a_iex(monkeypatch):
    http = _Http(sip=lambda: _Resp({"message": "unauthorized"}, status=401))
    monkeypatch.setattr(ad.requests, "get", http)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider().barras(["ACME"])
    assert exc.value.codigo == "http_401"
    assert http.feeds() == ["sip"]
    assert ad.fallback_iex_en_proceso() is False


def test_403_sin_texto_de_plan_no_cae_a_iex(monkeypatch):
    http = _Http(sip=lambda: _Resp({"message": "forbidden"}, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider().barras(["ACME"])
    assert exc.value.codigo == "http_403"
    assert http.feeds() == ["sip"]


@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
def test_otros_errores_no_caen_a_iex(monkeypatch, status):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=status))
    monkeypatch.setattr(ad.requests, "get", http)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider().barras(["ACME"])
    assert exc.value.codigo == f"http_{status}"
    assert "iex" not in http.feeds()


def test_flag_apagado_no_cae_a_iex(monkeypatch):
    monkeypatch.setenv(ad.ENV_RESPALDO_IEX, "0")
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider().barras(["ACME"])
    assert exc.value.codigo == "http_403"
    assert http.feeds() == ["sip"]
    assert ad.fallback_iex_en_proceso() is False


def test_flag_por_defecto_encendido(monkeypatch):
    assert ad.respaldo_iex_habilitado() is True
    for valor in ("1", "true", "si", ""):
        monkeypatch.setenv(ad.ENV_RESPALDO_IEX, valor)
        assert ad.respaldo_iex_habilitado() is True
    for valor in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv(ad.ENV_RESPALDO_IEX, valor)
        assert ad.respaldo_iex_habilitado() is False


def test_instancia_solo_sip_no_cae_a_iex(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider(respaldo_iex=False).barras(["ACME"])
    assert exc.value.codigo == "http_403"
    assert http.feeds() == ["sip"]


def test_iex_tambien_falla_es_fail_closed(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403),
                 iex=lambda: _Resp({"message": "forbidden"}, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    with pytest.raises(ErrorDatosAlpaca) as exc:
        _provider().barras(["ACME"])
    assert exc.value.codigo == "http_403"
    assert http.feeds() == ["sip", "iex"]


def test_iex_tambien_falla_corta_la_corrida_como_hoy(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403),
                 iex=lambda: _Resp({}, status=500))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = ProveedorAlpaca(_provider(), metadata_de=lambda: None)
    with pytest.raises(FuentePreciosCaida) as exc:
        prov.barras(["ACME"])
    assert exc.value.codigo == "http_500"
    datos = informe_de(prov)
    assert datos["fuente"] is None and datos["feed"] is None
    assert datos["fallback_iex"] is False


def test_un_simbolo_ausente_en_iex_no_es_cero(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403),
                 iex=lambda: _Resp({"bars": {}}))
    monkeypatch.setattr(ad.requests, "get", http)
    out = _provider().barras(["ACME"])
    assert out == {}


# ------------------------------ pegajoso ------------------------------

def test_pegajoso_el_resto_del_ciclo_va_directo_a_iex(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _provider()
    prov.barras(["ACME"])
    prov.barras(["ACME"])
    # Otra instancia del mismo proceso (p. ej. snapshots) también.
    _provider().barras(["ACME"])
    assert http.feeds() == ["sip", "iex", "iex", "iex"]


def test_proceso_nuevo_vuelve_a_probar_sip_y_se_queda_si_volvio(monkeypatch):
    estado = {"sip": 403}
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403) if estado["sip"] == 403 else _Resp(_diarias()))
    monkeypatch.setattr(ad.requests, "get", http)
    _provider().barras(["ACME"])
    assert http.feeds() == ["sip", "iex"]
    # Escaneo siguiente = proceso nuevo; SIP ya volvió.
    ad.reiniciar_respaldo_iex()
    estado["sip"] = 200
    prov = _provider()
    prov.barras(["ACME"])
    prov.barras(["ACME"])
    assert http.feeds()[2:] == ["sip", "sip"]
    assert prov.feeds_usados == {"sip"}
    assert ad.fallback_iex_en_proceso() is False


def test_pegajoso_vence_y_reprueba_sip(monkeypatch):
    reloj = {"t": 1000.0}
    monkeypatch.setattr(ad.time, "monotonic", lambda: reloj["t"])
    estado = {"sip": 403}
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403) if estado["sip"] == 403 else _Resp(_diarias()))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = _provider()
    prov.barras(["ACME"])
    reloj["t"] += ad.PEGAJOSO_IEX_S - 1
    prov.barras(["ACME"])
    assert http.feeds() == ["sip", "iex", "iex"]
    reloj["t"] += 2
    estado["sip"] = 200
    prov.barras(["ACME"])
    assert http.feeds()[-1] == "sip"


def test_pegajoso_no_aplica_a_instancias_solo_sip(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    _provider().barras(["ACME"])
    with pytest.raises(ErrorDatosAlpaca):
        _provider(respaldo_iex=False).barras(["ACME"])
    assert http.feeds() == ["sip", "iex", "sip"]


# ------------------------------ telemetría ------------------------------

def test_telemetria_del_proveedor_dice_iex(monkeypatch):
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = ProveedorAlpaca(_provider(), metadata_de=lambda: None)
    assert "ACME" in prov.barras(["ACME"])
    datos = informe_de(prov)
    assert datos["fuente"] == "alpaca"
    assert datos["feed"] == "iex"
    assert datos["feed_usado"] == "iex"
    assert datos["fallback_iex"] is True


def test_telemetria_sin_fallback_sigue_en_sip(monkeypatch):
    http = _Http(sip=lambda: _Resp(_diarias()))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = ProveedorAlpaca(_provider(), metadata_de=lambda: None)
    prov.barras(["ACME"])
    datos = informe_de(prov)
    assert datos["feed"] == "sip" and datos["feed_usado"] == "sip"
    assert datos["fallback_iex"] is False


def test_telemetria_mixta_no_se_rotula_sip(monkeypatch):
    reloj = {"t": 0.0}
    monkeypatch.setattr(ad.time, "monotonic", lambda: reloj["t"])
    estado = {"sip": 200}
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403) if estado["sip"] == 403 else _Resp(_diarias()))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = ProveedorAlpaca(_provider(), metadata_de=lambda: None)
    prov.barras(["ACME"])
    estado["sip"] = 403
    prov.barras(["ACME"])
    datos = informe_de(prov)
    assert datos["feed_usado"] == "sip+iex"
    assert datos["feed"] == "iex"
    assert datos["fallback_iex"] is True


def test_metricas_y_log_del_ciclo(monkeypatch, caplog):
    from momentum_hunter import run as run_mod

    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = ProveedorAlpaca(_provider(), metadata_de=lambda: None)
    prov.barras(["ACME"])
    m = telemetria.Metricas(modo="escaneo")
    with caplog.at_level(logging.INFO, logger="momentum_hunter.run"):
        run_mod._anotar_fuente_datos(m, prov)
    d = m.como_dict()["datos"]
    assert d["feed"] == "iex"
    assert d["feed_usado"] == "iex"
    assert d["fallback_iex"] is True
    assert "feed_usado=iex fallback_iex=true" in caplog.text


def test_proveedor_configurado_usa_el_respaldo_por_defecto(monkeypatch):
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("MOMENTUM_METADATA_PROVIDER", raising=False)
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "s")
    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    prov = proveedor_configurado(construir_yahoo=lambda: None)
    assert "ACME" in prov.barras(["ACME"])
    assert http.feeds()[0] == "sip" and "iex" in http.feeds()
    assert informe_de(prov)["feed_usado"] == "iex"


def test_halts_y_subastas_no_usan_iex(monkeypatch):
    """Halts lee condiciones/cinta del SIP y las subastas oficiales solo
    existen en SIP: un 403 ahí sigue siendo 'sin dato', no IEX."""
    from momentum_hunter.data import halts, subastas

    http = _Http(sip=lambda: _Resp(MSG_SIP, status=403))
    monkeypatch.setattr(ad.requests, "get", http)
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "s")
    ahora = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    lecturas = halts.consultar(["ACME"], ahora, directorio=None)
    assert lecturas["ACME"].situacion == "desconocido"
    assert subastas.descargar(["ACME"], ahora) == {}
    assert "iex" not in http.feeds()
