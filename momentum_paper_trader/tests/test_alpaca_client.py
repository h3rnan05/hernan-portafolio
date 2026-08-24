"""Pruebas del cliente de Alpaca -- red mockeada por completo (nunca un
request real). El foco central: el endpoint SIEMPRE es paper, sin
importar nada -- ver docstring del módulo."""

from __future__ import annotations

from momentum_paper_trader import alpaca_client
from momentum_paper_trader.alpaca_client import AlpacaPaperClient


def test_base_url_es_siempre_paper_nunca_live():
    assert alpaca_client._BASE_URL == "https://paper-api.alpaca.markets/v2"
    assert "paper" in alpaca_client._BASE_URL
    assert alpaca_client._BASE_URL != "https://api.alpaca.markets/v2"


def test_info_cuenta_usa_get_de_solo_lectura_al_endpoint_paper(monkeypatch):
    llamadas = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"account_number": "PA123", "status": "ACTIVE", "buying_power": "100000"}

    def _fake_get(url, headers, timeout):
        llamadas.append((url, headers, timeout))
        return _FakeResponse()

    monkeypatch.setattr(alpaca_client.requests, "get", _fake_get)

    client = AlpacaPaperClient("clave", "secreto")
    cuenta = client.info_cuenta()

    assert cuenta["status"] == "ACTIVE"
    url, headers, _ = llamadas[0]
    assert url == "https://paper-api.alpaca.markets/v2/account"
    assert headers["APCA-API-KEY-ID"] == "clave"


def test_posiciones_y_ordenes_abiertas_son_gets_de_solo_lectura(monkeypatch):
    llamadas = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"symbol": "RKLB"}]

    def _fake_get(url, headers, timeout, params=None):
        llamadas.append((url, params))
        return _FakeResponse()

    monkeypatch.setattr(alpaca_client.requests, "get", _fake_get)
    client = AlpacaPaperClient("clave", "secreto")

    assert client.posiciones() == [{"symbol": "RKLB"}]
    assert client.ordenes_abiertas() == [{"symbol": "RKLB"}]

    assert llamadas[0][0] == "https://paper-api.alpaca.markets/v2/positions"
    assert llamadas[1][0] == "https://paper-api.alpaca.markets/v2/orders"
    assert llamadas[1][1]["status"] == "open"


def test_estado_orden_pide_nested_para_ver_las_patas_del_bracket(monkeypatch):
    llamadas = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "orden-123", "status": "filled", "legs": []}

    def _fake_get(url, headers, timeout, params=None):
        llamadas.append((url, params))
        return _FakeResponse()

    monkeypatch.setattr(alpaca_client.requests, "get", _fake_get)
    client = AlpacaPaperClient("clave", "secreto")

    datos = client.estado_orden("orden-123")

    assert datos["status"] == "filled"
    url, params = llamadas[0]
    assert url == "https://paper-api.alpaca.markets/v2/orders/orden-123"
    assert params["nested"] == "true"


def test_colocar_orden_bracket_arma_el_payload_correcto(monkeypatch):
    llamadas = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "orden-123", "status": "accepted"}

    def _fake_post(url, json, headers, timeout):
        llamadas.append((url, json, headers, timeout))
        return _FakeResponse()

    monkeypatch.setattr(alpaca_client.requests, "post", _fake_post)

    client = AlpacaPaperClient("clave", "secreto")
    orden = client.colocar_orden_bracket("RKLB", 65, 78.42, 76.90, 82.50)

    assert orden.order_id == "orden-123"
    assert orden.ticker == "RKLB"
    assert orden.cantidad == 65
    assert orden.estado == "accepted"

    url, payload, headers, _ = llamadas[0]
    assert url.startswith("https://paper-api.alpaca.markets")
    assert payload["symbol"] == "RKLB"
    assert payload["qty"] == "65"
    assert payload["side"] == "buy"
    assert payload["type"] == "limit"
    assert payload["limit_price"] == "78.42"
    assert payload["order_class"] == "bracket"
    assert payload["take_profit"]["limit_price"] == "82.50"
    assert payload["stop_loss"]["stop_price"] == "76.90"
    assert headers["APCA-API-KEY-ID"] == "clave"
    assert headers["APCA-API-SECRET-KEY"] == "secreto"


def test_error_http_se_propaga_para_que_el_executor_lo_capture(monkeypatch):
    class _FakeResponseError:
        def raise_for_status(self):
            raise RuntimeError("símbolo no encontrado")

    monkeypatch.setattr(
        alpaca_client.requests, "post",
        lambda *a, **kw: _FakeResponseError())

    client = AlpacaPaperClient("clave", "secreto")
    try:
        client.colocar_orden_bracket("NOEXISTE", 10, 5.0, 4.0, 6.0)
        assert False, "debía lanzar"
    except RuntimeError:
        pass


def test_cerrar_posiciones_cancela_ordenes_y_usa_endpoint_paper(monkeypatch):
    # cancel_orders=true importa: las patas del bracket siguen vivas
    # mientras haya posición, y cerrar sin cancelarlas puede rebotar.
    llamadas = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"symbol": "RKLB", "status": 200}]

    def _fake_delete(url, params, headers, timeout):
        llamadas.append((url, params))
        return _FakeResponse()

    monkeypatch.setattr(alpaca_client.requests, "delete", _fake_delete)
    client = AlpacaPaperClient("clave", "secreto")

    assert client.cerrar_todas_las_posiciones() == [{"symbol": "RKLB", "status": 200}]
    url, params = llamadas[0]
    assert url == "https://paper-api.alpaca.markets/v2/positions"
    assert params["cancel_orders"] == "true"


def test_cerrar_posiciones_tolera_respuesta_inesperada(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": "no positions"}   # dict, no lista

    monkeypatch.setattr(alpaca_client.requests, "delete", lambda *a, **kw: _FakeResponse())
    assert AlpacaPaperClient("c", "s").cerrar_todas_las_posiciones() == []


def test_precio_sub_dolar_conserva_cuatro_decimales():
    # El bot opera desde $0,75: con .2f un stop de $0,7512 se enviaba
    # como "0.75", un precio distinto del que decidió el pipeline.
    assert AlpacaPaperClient._precio(0.7512) == "0.7512"
    assert AlpacaPaperClient._precio(0.9999) == "0.9999"


def test_precio_normal_usa_dos_decimales():
    assert AlpacaPaperClient._precio(1245.050048828125) == "1245.05"
    assert AlpacaPaperClient._precio(1.0) == "1.00"


def test_bracket_con_niveles_invertidos_falla_localmente(monkeypatch):
    # Mejor un error local y explícito que un rechazo remoto opaco.
    monkeypatch.setattr(alpaca_client.requests, "post",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no debió llamarse")))
    client = AlpacaPaperClient("c", "s")
    for entrada, stop, objetivo in ((10.0, 12.0, 15.0), (10.0, 9.0, 8.0), (0.0, -1.0, 1.0)):
        try:
            client.colocar_orden_bracket("X", 10, entrada, stop, objetivo)
            assert False, "debía lanzar"
        except ValueError:
            pass


def test_bracket_con_cantidad_cero_falla_localmente(monkeypatch):
    monkeypatch.setattr(alpaca_client.requests, "post",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no debió llamarse")))
    try:
        AlpacaPaperClient("c", "s").colocar_orden_bracket("X", 0, 10.0, 9.0, 12.0)
        assert False, "debía lanzar"
    except ValueError:
        pass


def test_reloj_mercado_es_un_get_de_solo_lectura_al_endpoint_paper(monkeypatch):
    llamadas = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"is_open": True, "next_close": "2026-08-24T20:00:00-00:00"}

    def _fake_get(url, headers, timeout, params=None):
        llamadas.append(url)
        return _FakeResponse()

    monkeypatch.setattr(alpaca_client.requests, "get", _fake_get)

    assert AlpacaPaperClient("clave", "secreto").reloj_mercado()["is_open"] is True
    assert llamadas[0] == "https://paper-api.alpaca.markets/v2/clock"
