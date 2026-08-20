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
