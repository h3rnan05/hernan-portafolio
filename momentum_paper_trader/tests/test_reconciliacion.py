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
