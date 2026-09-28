"""La cuenta manda: una posición del broker sin revisión viva, o sin
stop, se avisa. Alpaca y Telegram mockeados. El dedupe va al tmp del
fixture (`MOMENTUM_AVISOS_DIR`), nunca a /var/lib."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_paper_trader import reconciliacion
from momentum_paper_trader.estado import RevisionIA

_VIERNES = datetime(2026, 9, 25, 20, 14, tzinfo=UTC)   # 16:14 ET
_LUNES = datetime(2026, 9, 28, 13, 30, tzinfo=UTC)     # 09:30 ET


def _revision(resultado="abierta") -> RevisionIA:
    return RevisionIA(
        ticker="CTAS", creado_en="2026-09-25T15:40:42+00:00", entro=True, confianza=7,
        razonamiento="x", timestamp="2026-09-25T16:30:22+00:00",
        order_id="orden-1", cantidad=3, precio_entrada=199.73, stop=198.56,
        objetivo=202.07, resultado=resultado,
    )


def _parchear(monkeypatch, revisiones, posiciones, ordenes):
    enviados: list[str] = []
    monkeypatch.setattr(reconciliacion.notify, "enviar", lambda t: enviados.append(t))
    monkeypatch.setattr(reconciliacion.estado, "cargar", lambda: list(revisiones))

    class _Client:
        def posiciones(self):
            if isinstance(posiciones, Exception):
                raise posiciones
            return posiciones

        def ordenes_abiertas(self):
            if isinstance(ordenes, Exception):
                raise ordenes
            return ordenes

    return _Client(), enviados


def test_posicion_archivada_en_el_broker_avisa_huerfana_y_no_repite(monkeypatch):
    # El 25/9: revisiones decía cerrada y Alpaca la seguía teniendo, sin stop.
    client, enviados = _parchear(
        monkeypatch, [_revision("cerrada")],
        [{"symbol": "CTAS", "qty": "3"}], [],
    )
    assert reconciliacion.revisar(client, _VIERNES) == ["CTAS"]
    assert len(enviados) == 1
    assert "ERROR" in enviados[0] and "[PAPER]" in enviados[0]
    assert "CTAS" in enviados[0]
    assert "no hay una revisión viva" in enviados[0]
    assert "no tiene stop" in enviados[0]
    assert reconciliacion.revisar(client, _VIERNES) == []
    assert len(enviados) == 1
    # Día de sesión nuevo: si sigue así, se vuelve a avisar.
    assert reconciliacion.revisar(client, _LUNES) == ["CTAS"]
    assert len(enviados) == 2


def test_posicion_seguida_con_stop_no_avisa(monkeypatch):
    client, enviados = _parchear(
        monkeypatch, [_revision("abierta")],
        [{"symbol": "CTAS", "qty": "3"}],
        [{"symbol": "CTAS", "side": "sell", "type": "stop", "id": "sl"}],
    )
    assert reconciliacion.revisar(client, _VIERNES) == []
    assert enviados == []


def test_posicion_seguida_sin_stop_avisa(monkeypatch):
    # Solo el take-profit: no es protección contra un hueco.
    client, enviados = _parchear(
        monkeypatch, [_revision("abierta")],
        [{"symbol": "CTAS", "qty": "3"}],
        [{"symbol": "CTAS", "side": "sell", "type": "limit", "id": "tp"}],
    )
    assert reconciliacion.revisar(client, _VIERNES) == ["CTAS"]
    assert "no tiene stop" in enviados[0]
    assert "no hay una revisión viva" not in enviados[0]


def test_venta_a_mercado_en_curso_no_se_trata_como_desprotegida(monkeypatch):
    client, enviados = _parchear(
        monkeypatch, [_revision("abierta")],
        [{"symbol": "CTAS", "qty": "3"}],
        [{"symbol": "CTAS", "side": "sell", "type": "market", "id": "mkt"}],
    )
    assert reconciliacion.revisar(client, _VIERNES) == []
    assert enviados == []


def test_sin_ordenes_legibles_no_inventa_que_falta_el_stop(monkeypatch):
    client, enviados = _parchear(
        monkeypatch, [],
        [{"symbol": "CTAS", "qty": "3"}],
        RuntimeError("Alpaca caído"),
    )
    assert reconciliacion.revisar(client, _VIERNES) == ["CTAS"]
    assert "no hay una revisión viva" in enviados[0]
    assert "no tiene stop" not in enviados[0]


def test_posiciones_ilegibles_no_alertan_un_vacio_falso(monkeypatch):
    client, enviados = _parchear(monkeypatch, [], RuntimeError("caído"), [])
    assert reconciliacion.revisar(client, _VIERNES) == []
    assert enviados == []


# Lo que devolvió Alpaca el 2026-09-28 para MNST, en la forma de
# `GET /v2/orders?status=open&nested=true`: el take-profit es la fila
# (status new) y el stop va en `legs` con status held. Sin anidar, esa
# pata no está en la lista y el chequeo veía solo un limit de venta.
_BRACKET_MNST = {
    "id": "b7c1e0aa-11d4-4f2a-9c33-0a1b2c3d4e5f",
    "client_order_id": "eb9e2aaa-f71a-4f51-b5b4-52a6a7d2c1c1",
    "created_at": "2026-09-28T14:30:22.224183Z",
    "updated_at": "2026-09-28T15:17:01.102000Z",
    "submitted_at": "2026-09-28T14:30:22.222565Z",
    "filled_at": None,
    "expired_at": None,
    "canceled_at": None,
    "failed_at": None,
    "asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
    "symbol": "MNST",
    "asset_class": "us_equity",
    "qty": "8",
    "filled_qty": "0",
    "filled_avg_price": None,
    "order_class": "oco",
    "type": "limit",
    "side": "sell",
    "time_in_force": "day",
    "limit_price": "44.10",
    "stop_price": None,
    "status": "new",
    "extended_hours": False,
    "legs": [
        {
            "id": "f2d920f1-7a3e-4d11-9c2b-1e8a0b5c6d70",
            "client_order_id": "fb472043-1f4e-4445-b5f2-174d1535a118",
            "created_at": "2026-09-28T14:30:22.224232Z",
            "symbol": "MNST",
            "qty": "8",
            "filled_qty": "0",
            "filled_avg_price": None,
            "order_class": "bracket",
            "type": "stop",
            "side": "sell",
            "time_in_force": "day",
            "limit_price": None,
            "stop_price": "41.62",
            "status": "held",
            "extended_hours": False,
            "legs": None,
        }
    ],
}


def _revision_mnst() -> RevisionIA:
    r = _revision("abierta")
    r.ticker = "MNST"
    return r


def test_stop_held_anidado_no_dispara_falso_sin_stop(monkeypatch):
    """MNST a las 10:17 MTY: el stop held a 41.62 estaba en `legs` y el
    ERROR decía que no había stop. Con la posición seguida, silencio."""
    client, enviados = _parchear(
        monkeypatch, [_revision_mnst()],
        [{"symbol": "MNST", "qty": "8"}],
        [_BRACKET_MNST],
    )
    assert reconciliacion.revisar(client, _VIERNES) == []
    assert enviados == []
    assert reconciliacion.detectar(
        [{"symbol": "MNST", "qty": "8"}], [_BRACKET_MNST], [_revision_mnst()],
    ) == []
    # Un OCO a veces manda la pata como stop_limit, también en held.
    pata = dict(_BRACKET_MNST["legs"][0])
    pata["type"] = "stop_limit"
    oco = dict(_BRACKET_MNST)
    oco["legs"] = [pata]
    assert reconciliacion.detectar(
        [{"symbol": "MNST", "qty": "8"}], [oco], [_revision_mnst()],
    ) == []


def test_pata_stop_ya_cancelada_no_cuenta_como_proteccion(monkeypatch):
    muerta = {
        "id": "tp-muerta",
        "symbol": "MNST",
        "side": "sell",
        "type": "limit",
        "status": "new",
        "order_class": "oco",
        "legs": [{
            "id": "f2d920f1-7a3e-4d11-9c2b-1e8a0b5c6d70",
            "symbol": "MNST",
            "side": "sell",
            "type": "stop",
            "order_class": "bracket",
            "stop_price": "41.62",
            "status": "canceled",
            "legs": None,
        }],
    }
    client, enviados = _parchear(
        monkeypatch, [_revision_mnst()],
        [{"symbol": "MNST", "qty": "8"}],
        [muerta],
    )
    assert reconciliacion.revisar(client, _VIERNES) == ["MNST"]
    assert "no tiene stop" in enviados[0]
