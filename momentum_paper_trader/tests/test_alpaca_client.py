"""Pruebas del cliente de Alpaca -- red mockeada por completo (nunca un
request real). El foco central: el endpoint SIEMPRE es paper, sin
importar nada -- ver docstring del módulo."""

from __future__ import annotations

import requests

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
    assert llamadas[1][1]["nested"] == "true"


def test_ordenes_de_simbolos_pide_el_padre_filled_con_status_all(monkeypatch):
    """El stop held no está en status=open. Hay que pedirlo con
    status=all, nested y el símbolo de la posición."""
    llamadas = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": "265e093f", "status": "filled", "symbol": "MNST"}]

    def _fake_get(url, headers, timeout, params=None):
        llamadas.append((url, params))
        return _FakeResponse()

    monkeypatch.setattr(alpaca_client.requests, "get", _fake_get)
    client = AlpacaPaperClient("clave", "secreto")

    assert client.ordenes_de_simbolos([]) == []
    assert client.ordenes_de_simbolos(["", "  "]) == []
    assert llamadas == []

    assert client.ordenes_de_simbolos(["MNST", "MNST", " CTAS "])[0]["id"] == "265e093f"
    url, params = llamadas[0]
    assert url == "https://paper-api.alpaca.markets/v2/orders"
    assert params["status"] == "all"
    assert params["nested"] == "true"
    assert params["symbols"] == "MNST,CTAS"
    assert params["limit"] == 500
    assert params["direction"] == "desc"


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


def test_el_bracket_manda_client_order_id_y_extended_hours_false(monkeypatch):
    payloads = []

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"id": "orden-1", "status": "accepted"}

    monkeypatch.setattr(alpaca_client.requests, "post",
                        lambda url, json, headers, timeout: (payloads.append(json), _R())[1])

    AlpacaPaperClient("c", "s").colocar_orden_bracket(
        "RKLB", 65, 78.42, 76.90, 82.50, client_order_id="momentum-RKLB-2026-08-11")

    assert payloads[0]["client_order_id"] == "momentum-RKLB-2026-08-11"
    assert payloads[0]["extended_hours"] is False


def test_sin_client_order_id_la_clave_no_viaja(monkeypatch):
    # Alpaca genera el suyo; mandar la clave vacía sería peor que omitirla.
    payloads = []

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"id": "orden-1", "status": "accepted"}

    monkeypatch.setattr(alpaca_client.requests, "post",
                        lambda url, json, headers, timeout: (payloads.append(json), _R())[1])

    AlpacaPaperClient("c", "s").colocar_orden_bracket("RKLB", 65, 78.42, 76.90, 82.50)

    assert "client_order_id" not in payloads[0]


def test_cerrar_una_posicion_puede_pedir_que_alpaca_cancele_antes(monkeypatch):
    # Sin cancel_orders el DELETE ve la cantidad retenida por el bracket
    # y responde 403. El cierre de una sola posición tiene que poder pedirlo.
    llamadas = []

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "ord-cierre", "status": "accepted"}

    def _delete(url, params=None, headers=None, timeout=None):
        llamadas.append((url, params))
        return _R()

    monkeypatch.setattr(alpaca_client.requests, "delete", _delete)
    resp = AlpacaPaperClient("clave", "secreto").cerrar_posicion("CTAS", cancel_orders=True)

    assert resp["id"] == "ord-cierre"
    assert llamadas[0][0] == "https://paper-api.alpaca.markets/v2/positions/CTAS"
    assert llamadas[0][1] == {"cancel_orders": "true"}


def test_vender_a_mercado_vende_la_qty_recibida_y_solo_en_paper(monkeypatch):
    payloads = []

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "mkt-1", "status": "accepted"}

    def _post(url, json, headers, timeout):
        payloads.append((url, json))
        return _R()

    monkeypatch.setattr(alpaca_client.requests, "post", _post)
    resp = AlpacaPaperClient("clave", "secreto").vender_a_mercado("CTAS", "3")

    assert resp["id"] == "mkt-1"
    url, payload = payloads[0]
    assert url == "https://paper-api.alpaca.markets/v2/orders"
    assert payload["symbol"] == "CTAS"
    assert payload["qty"] == "3"
    assert payload["side"] == "sell"
    assert payload["type"] == "market"
    assert payload["time_in_force"] == "day"
    assert payload["extended_hours"] is False


def test_un_client_order_id_repetido_se_trata_como_ya_enviado(monkeypatch):
    import json as _json

    posts = []

    def _post(url, json, headers, timeout):
        posts.append(json)
        cuerpo = _json.dumps(
            {"code": 42210000, "message": "client_order_id must be unique"}
        ).encode()
        r = requests.Response()
        r.status_code = 422
        r.url = url
        r._content = cuerpo
        return r

    def _get(url, params=None, headers=None, timeout=None):
        class _R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return [{
                    "id": "ya-1",
                    "symbol": "CTAS",
                    "side": "sell",
                    "type": "market",
                    "status": "accepted",
                    "client_order_id": "eod-CTAS-20260928",
                }]

        return _R()

    monkeypatch.setattr(alpaca_client.requests, "post", _post)
    monkeypatch.setattr(alpaca_client.requests, "get", _get)
    resp = AlpacaPaperClient("clave", "secreto").vender_a_mercado(
        "CTAS", "3", client_order_id="eod-CTAS-20260928",
    )

    assert resp["id"] == "ya-1"
    assert posts[0]["client_order_id"] == "eod-CTAS-20260928"
    assert len(posts) == 1


def test_posicion_404_es_ausente_y_un_5xx_no(monkeypatch):
    def _get(url, headers=None, timeout=None):
        r = requests.Response()
        r.status_code = 404 if url.endswith("/CTAS") else 500
        r.url = url
        r._content = b""
        return r

    monkeypatch.setattr(alpaca_client.requests, "get", _get)
    client = AlpacaPaperClient("c", "s")
    assert client.posicion("CTAS") is None
    try:
        client.posicion("MNST")
        assert False, "un 500 no es 'no hay posición'"
    except requests.HTTPError:
        pass


def test_vender_a_mercado_no_inventa_una_cantidad(monkeypatch):
    monkeypatch.setattr(
        alpaca_client.requests, "post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no debió llamarse")))
    client = AlpacaPaperClient("c", "s")
    for cantidad in ("0", "-1", "", "no-es-un-numero"):
        try:
            client.vender_a_mercado("CTAS", cantidad)
            assert False, cantidad
        except ValueError:
            pass


def test_cancelar_el_stop_held_del_padre_filled_y_no_el_padre(monkeypatch):
    """Forma viva de MNST: padre 265e093f filled, límite bb5baab2 new,
    stop f2d920f1 held. Se cancelan las dos patas vivas; el padre no."""
    borradas = []

    class _R:
        status_code = 204

        def raise_for_status(self):
            pass

    def _delete(url, headers=None, timeout=None):
        borradas.append(url)
        return _R()

    monkeypatch.setattr(alpaca_client.requests, "delete", _delete)
    ordenes = [{
        "id": "265e093f",
        "symbol": "MNST",
        "side": "buy",
        "type": "limit",
        "order_class": "bracket",
        "status": "filled",
        "legs": [
            {
                "id": "bb5baab2",
                "symbol": "MNST",
                "side": "sell",
                "type": "limit",
                "limit_price": "42.36",
                "status": "new",
                "legs": None,
            },
            {
                "id": "f2d920f1",
                "symbol": "MNST",
                "side": "sell",
                "type": "stop",
                "stop_price": "41.62",
                "status": "held",
                "legs": None,
            },
        ],
    }]
    n = AlpacaPaperClient("c", "s").cancelar_ordenes_de("MNST", ordenes)

    assert n == 2
    assert borradas == [
        "https://paper-api.alpaca.markets/v2/orders/bb5baab2",
        "https://paper-api.alpaca.markets/v2/orders/f2d920f1",
    ]


def test_cancelar_incluye_la_pata_stop_held_y_no_la_ya_muerta(monkeypatch):
    borradas = []

    class _R:
        status_code = 204

        def raise_for_status(self):
            pass

    def _delete(url, headers=None, timeout=None):
        borradas.append(url)
        return _R()

    monkeypatch.setattr(alpaca_client.requests, "delete", _delete)
    ordenes = [{
        "id": "b7c1e0aa-11d4-4f2a-9c33-0a1b2c3d4e5f",
        "symbol": "MNST",
        "side": "sell",
        "type": "limit",
        "order_class": "oco",
        "status": "new",
        "legs": [
            {
                "id": "f2d920f1-7a3e-4d11-9c2b-1e8a0b5c6d70",
                "symbol": "MNST",
                "side": "sell",
                "type": "stop",
                "order_class": "bracket",
                "stop_price": "41.62",
                "status": "held",
                "legs": None,
            },
            {
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "symbol": "MNST",
                "side": "sell",
                "type": "stop",
                "status": "canceled",
                "legs": None,
            },
        ],
    }]
    n = AlpacaPaperClient("c", "s").cancelar_ordenes_de("MNST", ordenes)

    assert n == 2
    assert borradas == [
        "https://paper-api.alpaca.markets/v2/orders/b7c1e0aa-11d4-4f2a-9c33-0a1b2c3d4e5f",
        "https://paper-api.alpaca.markets/v2/orders/f2d920f1-7a3e-4d11-9c2b-1e8a0b5c6d70",
    ]


def test_activo_consulta_el_endpoint_de_assets(monkeypatch):
    llamadas = []

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"symbol": "RKLB", "tradable": True, "status": "active"}

    monkeypatch.setattr(alpaca_client.requests, "get",
                        lambda url, headers, timeout: (llamadas.append(url), _R())[1])

    assert AlpacaPaperClient("c", "s").activo("RKLB")["tradable"] is True
    assert llamadas[0] == "https://paper-api.alpaca.markets/v2/assets/RKLB"
