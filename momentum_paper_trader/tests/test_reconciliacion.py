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

        def ordenes_de_simbolos(self, simbolos):
            self.simbolos = list(simbolos)
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
        [{"symbol": "CTAS", "side": "sell", "type": "stop", "status": "held", "id": "sl"}],
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
        [{"symbol": "CTAS", "side": "sell", "type": "market", "status": "accepted", "id": "mkt"}],
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


# Forma viva del 2026-09-28, MNST. `status=open&nested=true` devolvía
# solo el take-profit bb5baab2 (limit 42.36, new) con legs null. El stop
# f2d920f1 (stop 41.62, held) iba anidado en la compra ya filled
# 265e093f, o como fila propia de status=all.
_TAKE_PROFIT_ABIERTO_MNST = {
    "id": "bb5baab2",
    "symbol": "MNST",
    "side": "sell",
    "type": "limit",
    "order_class": "bracket",
    "time_in_force": "day",
    "limit_price": "42.36",
    "stop_price": None,
    "status": "new",
    "legs": None,
}

_PADRE_FILLED_MNST = {
    "id": "265e093f",
    "symbol": "MNST",
    "side": "buy",
    "type": "limit",
    "order_class": "bracket",
    "time_in_force": "day",
    "qty": "8",
    "status": "filled",
    "legs": [
        {
            "id": "bb5baab2",
            "symbol": "MNST",
            "side": "sell",
            "type": "limit",
            "order_class": "bracket",
            "time_in_force": "day",
            "limit_price": "42.36",
            "stop_price": None,
            "status": "new",
            "legs": None,
        },
        {
            "id": "f2d920f1",
            "symbol": "MNST",
            "side": "sell",
            "type": "stop",
            "order_class": "bracket",
            "time_in_force": "day",
            "limit_price": None,
            "stop_price": "41.62",
            "status": "held",
            "legs": None,
        },
    ],
}


def _revision_mnst() -> RevisionIA:
    r = _revision("abierta")
    r.ticker = "MNST"
    return r


def test_el_take_profit_abierto_sin_patas_no_es_un_stop():
    """Lo que devolvió status=open&nested=true: solo bb5baab2, legs null.
    Eso es justo el falso 'sin stop' si no se mira el padre filled."""
    problemas = reconciliacion.detectar(
        [{"symbol": "MNST", "qty": "8"}],
        [_TAKE_PROFIT_ABIERTO_MNST],
        [_revision_mnst()],
    )
    assert problemas == [reconciliacion.Problema("MNST", False, True)]


def test_stop_held_bajo_el_padre_filled_no_dispara_falso_sin_stop(monkeypatch):
    """MNST a las 10:17 MTY: el stop held a 41.62 estaba en las patas de
    la compra ya filled, no en la lista open. Con la posición seguida,
    silencio. La búsqueda es status=all de los símbolos en posición."""
    client, enviados = _parchear(
        monkeypatch, [_revision_mnst()],
        [{"symbol": "MNST", "qty": "8"}],
        [_PADRE_FILLED_MNST],
    )
    assert reconciliacion.revisar(client, _VIERNES) == []
    assert enviados == []
    assert client.simbolos == ["MNST"]
    assert reconciliacion.detectar(
        [{"symbol": "MNST", "qty": "8"}], [_PADRE_FILLED_MNST], [_revision_mnst()],
    ) == []
    # La misma pata, como fila propia de status=all (sin anidar).
    fila = {
        "id": "f2d920f1",
        "symbol": "MNST",
        "side": "sell",
        "type": "stop",
        "stop_price": "41.62",
        "status": "held",
    }
    assert reconciliacion.detectar(
        [{"symbol": "MNST", "qty": "8"}], [fila], [_revision_mnst()],
    ) == []
    # Un OCO a veces manda la pata como stop_limit, también en held.
    pata = dict(_PADRE_FILLED_MNST["legs"][1])
    pata["type"] = "stop_limit"
    oco = dict(_PADRE_FILLED_MNST)
    oco["legs"] = [_PADRE_FILLED_MNST["legs"][0], pata]
    assert reconciliacion.detectar(
        [{"symbol": "MNST", "qty": "8"}], [oco], [_revision_mnst()],
    ) == []
    for status in ("new", "accepted", "pending_new"):
        viva = dict(pata)
        viva["status"] = status
        padre = dict(_PADRE_FILLED_MNST)
        padre["legs"] = [_PADRE_FILLED_MNST["legs"][0], viva]
        assert reconciliacion.detectar(
            [{"symbol": "MNST", "qty": "8"}], [padre], [_revision_mnst()],
        ) == []


def test_pata_stop_ya_cancelada_no_cuenta_como_proteccion(monkeypatch):
    muerta = dict(_PADRE_FILLED_MNST)
    muerta["legs"] = [
        _PADRE_FILLED_MNST["legs"][0],
        {
            "id": "f2d920f1",
            "symbol": "MNST",
            "side": "sell",
            "type": "stop",
            "order_class": "bracket",
            "stop_price": "41.62",
            "status": "canceled",
            "legs": None,
        },
    ]
    client, enviados = _parchear(
        monkeypatch, [_revision_mnst()],
        [{"symbol": "MNST", "qty": "8"}],
        [muerta],
    )
    assert reconciliacion.revisar(client, _VIERNES) == ["MNST"]
    assert "no tiene stop" in enviados[0]


def test_stop_sin_status_no_se_inventa_como_proteccion():
    problemas = reconciliacion.detectar(
        [{"symbol": "CTAS", "qty": "3"}],
        [{"symbol": "CTAS", "side": "sell", "type": "stop", "id": "sl"}],
        [_revision("abierta")],
    )
    assert problemas == [reconciliacion.Problema("CTAS", False, True)]
