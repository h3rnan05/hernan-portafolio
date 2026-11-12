"""Remanente sin stop tras un take-profit parcial (FCEL, 2026-10-02).

Forma real del broker (GET /v2/orders?symbols=FCEL&nested=true):
padre buy limit 42 @ 17.30 filled 13:32:38 UTC; pata limit 17.90
`partially_filled` 37/42 a las 13:45:03; pata stop 17.05 `canceled` en
el mismo instante. Quedaron 5 acciones sin stop durante 67 minutos.
Alpaca y Telegram mockeados; el registro y el dedupe van al tmp del
fixture (`MOMENTUM_AVISOS_DIR`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from momentum_paper_trader import cierre, reproteccion, seguimiento
from momentum_paper_trader.alpaca_client import AlpacaPaperClient
from momentum_paper_trader.estado import RevisionIA

_AHORA = datetime(2026, 10, 2, 13, 46, 10, tzinfo=UTC)   # 09:46 ET
_PADRE = "2b0ab90e-6323-4c2e-91ad-20f9839ff91b"


def _revision(**kw) -> RevisionIA:
    base = dict(
        ticker="FCEL", creado_en="2026-10-02T13:10:07+00:00", entro=True, confianza=7,
        razonamiento="x", timestamp="2026-10-02T13:31:22+00:00",
        order_id=_PADRE, cantidad=42, precio_entrada=17.30, stop=17.05,
        objetivo=17.90, resultado="abierta",
    )
    base.update(kw)
    return RevisionIA(**base)


def _padre(tp_status="partially_filled", tp_filled="37", sl_status="canceled", tp_price="17.9"):
    return {
        "id": _PADRE, "symbol": "FCEL", "side": "buy", "type": "limit",
        "qty": "42", "filled_qty": "42", "filled_avg_price": "17.3", "status": "filled",
        "legs": [
            {"id": "tp-a897", "symbol": "FCEL", "side": "sell", "type": "limit",
             "limit_price": "17.9", "qty": "42", "filled_qty": tp_filled,
             "filled_avg_price": tp_price if tp_filled != "0" else None, "status": tp_status},
            {"id": "sl-8ad7", "symbol": "FCEL", "side": "sell", "type": "stop",
             "stop_price": "17.05", "qty": "42", "filled_qty": "0", "status": sl_status},
        ],
    }


class _Client:
    """Doble del broker con el estado de FCEL a las 13:45:11 UTC."""

    def __init__(self, *, precio="17.60", stop_falla=False, mercado_falla=False,
                 tp_tras_cancelar="canceled", ordenes_extra=None, ordenes_fallan=False,
                 posicion_tras_cancelar="5"):
        self.padre = _padre()
        self.precio = precio
        self.stop_falla = stop_falla
        self.mercado_falla = mercado_falla
        self.tp_tras_cancelar = tp_tras_cancelar
        self.ordenes_extra = list(ordenes_extra or [])
        self.ordenes_fallan = ordenes_fallan
        self.posicion_tras_cancelar = posicion_tras_cancelar
        self.cancelado = False
        self.stops: list[tuple] = []
        self.ventas: list[tuple] = []
        self.cancelaciones: list[str] = []

    # lecturas
    def estado_orden(self, oid):
        if oid == _PADRE:
            return self.padre
        if oid == "tp-a897":
            return {"id": oid, "status": self.tp_tras_cancelar if self.cancelado else "partially_filled"}
        raise AssertionError(oid)

    def ordenes_de_simbolos(self, simbolos):
        if self.ordenes_fallan:
            raise RuntimeError("red")
        return [self.padre, *self.ordenes_extra]

    def posicion(self, ticker):
        if self.cancelado:
            if self.posicion_tras_cancelar is None:
                return None
            qty = self.posicion_tras_cancelar
            return {"symbol": ticker, "side": "long", "qty": qty, "qty_available": qty,
                    "current_price": self.precio}
        return {"symbol": ticker, "side": "long", "qty": "5", "qty_available": "0",
                "current_price": self.precio}

    # escrituras
    def cancelar_ordenes_de(self, ticker, ordenes):
        self.cancelaciones.append(ticker)
        self.cancelado = True
        return 1

    def colocar_stop_remanente(self, ticker, cantidad, stop, client_order_id):
        if self.stop_falla:
            raise RuntimeError("422")
        self.stops.append((ticker, cantidad, stop, client_order_id))
        return {"id": "rps-orden-1", "status": "accepted", "type": "stop"}

    def vender_a_mercado(self, ticker, cantidad, client_order_id=None):
        if self.mercado_falla:
            raise RuntimeError("403")
        self.ventas.append((ticker, cantidad, client_order_id))
        return {"id": "rpm-orden-1", "status": "accepted", "type": "market"}


@pytest.fixture
def entorno(monkeypatch):
    enviados: list[str] = []
    revisiones = [_revision()]
    monkeypatch.setattr(reproteccion.notify, "enviar", lambda t: enviados.append(t))
    monkeypatch.setattr(reproteccion.estado, "cargar", lambda: list(revisiones))
    monkeypatch.setattr(reproteccion, "_en_ventana_de_cierre", lambda ahora, cfg: False)
    monkeypatch.setattr(cierre, "_ESPERA_PATA_SEG", 0)
    return enviados, revisiones


# -- detección pura ----------------------------------------------------------

def test_detecta_la_forma_real_de_fcel():
    assert reproteccion.remanente_sin_stop(_padre()) is True


def test_tp_parcial_ya_cancelado_sigue_siendo_remanente():
    # Tras cancelar el resto: la pata limit canceled con 37/42.
    assert reproteccion.remanente_sin_stop(_padre(tp_status="canceled")) is True


@pytest.mark.parametrize("padre", [
    _padre(sl_status="held"),                                 # stop vivo: protegido
    _padre(tp_status="new", tp_filled="0", sl_status="held"),  # bracket normal
    _padre(tp_status="filled", tp_filled="42"),               # objetivo completo
    _padre(sl_status="filled", tp_status="canceled", tp_filled="0"),   # salió por stop
    _padre(sl_status=None),                                   # status ausente: no se afirma
    _padre(sl_status="pending_cancel"),                       # ni vivo ni terminal: duda
    {**_padre(), "status": "partially_filled"},               # entrada aún no llena
    {**_padre(), "legs": None},                                # sin patas legibles
])
def test_no_detecta_sin_evidencia_de_remanente(padre):
    assert reproteccion.remanente_sin_stop(padre) is False


# -- acción ------------------------------------------------------------------

def test_fcel_cancela_el_resto_del_tp_y_repone_el_stop_original(entorno):
    enviados, _ = entorno
    client = _Client()
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.STOP_REPUESTO}
    assert client.cancelaciones == ["FCEL"]
    assert len(client.stops) == 1
    ticker, qty, stop, coid = client.stops[0]
    assert (ticker, qty, stop) == ("FCEL", "5", 17.05)
    assert coid.startswith("rps-FCEL-") and len(coid) <= 48
    assert client.ventas == []
    assert len(enviados) == 1 and "REPROTEGIDA" in enviados[0] and "17.05" in enviados[0]
    assert "[PAPER]" in enviados[0]
    fila = reproteccion.orden_de(_PADRE)
    assert fila["order_id"] == "rps-orden-1" and fila["tipo"] == "stop"


def test_el_id_del_stop_es_el_mismo_en_cada_reintento(entorno):
    c1, c2 = _Client(), _Client()
    reproteccion.reproteger(c1, ahora=_AHORA)
    reproteccion.reproteger(c2, ahora=_AHORA)
    assert c1.stops[0][3] == c2.stops[0][3]


def test_con_stop_ya_repuesto_no_hace_nada(entorno):
    enviados, _ = entorno
    vivo = {"id": "rps-orden-1", "symbol": "FCEL", "side": "sell", "type": "stop",
            "status": "new", "qty": "5"}
    client = _Client(ordenes_extra=[vivo])
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.YA_PROTEGIDA}
    assert client.cancelaciones == [] and client.stops == [] and client.ventas == []
    assert enviados == []


def test_con_liquidacion_a_mercado_viva_no_se_cruza(entorno):
    venta = {"id": "eod", "symbol": "FCEL", "side": "sell", "type": "market", "status": "accepted"}
    client = _Client(ordenes_extra=[venta])
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.YA_PROTEGIDA}
    assert client.stops == [] and client.ventas == []


def test_stop_rechazado_vende_el_remanente_a_mercado(entorno):
    enviados, _ = entorno
    client = _Client(stop_falla=True)
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.VENDIDO_MERCADO}
    assert client.ventas[0][:2] == ("FCEL", "5")
    assert client.ventas[0][2].startswith("rpm-FCEL-")
    assert len(enviados) == 1 and "vendido a mercado" in enviados[0]
    assert reproteccion.orden_de(_PADRE)["tipo"] == "market"


def test_precio_ya_bajo_el_stop_vende_sin_intentar_el_stop(entorno):
    client = _Client(precio="17.00")
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.VENDIDO_MERCADO}
    assert client.stops == []
    assert client.ventas[0][1] == "5"


def test_stop_y_venta_rechazados_avisan_urgente_una_vez_por_sesion(entorno):
    enviados, _ = entorno
    client = _Client(stop_falla=True, mercado_falla=True)
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.FALLO}
    assert len(enviados) == 1
    assert "ERROR" in enviados[0] and "URGENTE" in enviados[0]
    assert reproteccion.orden_de(_PADRE) is None
    # El tick siguiente reintenta, pero no repite el Telegram.
    assert reproteccion.reproteger(_Client(stop_falla=True, mercado_falla=True), ahora=_AHORA) == {
        "FCEL": reproteccion.FALLO}
    assert len(enviados) == 1


def test_tp_que_no_suelta_la_cantidad_no_coloca_nada_y_avisa(entorno):
    enviados, _ = entorno
    client = _Client(tp_tras_cancelar="pending_cancel")
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.FALLO}
    assert client.stops == [] and client.ventas == []
    assert "URGENTE" in enviados[0]


def test_resto_del_tp_llenado_al_cancelar_no_deja_nada_que_proteger(entorno):
    enviados, _ = entorno
    client = _Client(tp_tras_cancelar="filled", posicion_tras_cancelar=None)
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.SIN_POSICION}
    assert client.stops == [] and client.ventas == [] and enviados == []


def test_ordenes_ilegibles_avisan_y_no_colocan(entorno):
    enviados, _ = entorno
    client = _Client(ordenes_fallan=True)
    assert reproteccion.reproteger(client, ahora=_AHORA) == {"FCEL": reproteccion.FALLO}
    assert client.cancelaciones == [] and client.stops == []
    assert "URGENTE" in enviados[0]


def test_en_la_ventana_de_cierre_manda_el_cierre(entorno, monkeypatch):
    monkeypatch.setattr(reproteccion, "_en_ventana_de_cierre", lambda ahora, cfg: True)
    client = _Client()
    assert reproteccion.reproteger(client, ahora=_AHORA) == {}
    assert client.cancelaciones == [] and client.stops == []


def test_bracket_normal_no_se_toca(entorno):
    client = _Client()
    client.padre = _padre(tp_status="new", tp_filled="0", sl_status="held")
    assert reproteccion.reproteger(client, ahora=_AHORA) == {}
    assert client.cancelaciones == []


def test_revision_terminal_no_se_consulta(entorno):
    _, revisiones = entorno
    revisiones[0] = _revision(resultado="objetivo")

    class _NoLlamar:
        def __getattr__(self, n):
            raise AssertionError(n)

    assert reproteccion.reproteger(_NoLlamar(), ahora=_AHORA) == {}


def test_nunca_lanza(entorno):
    class _Roto:
        def estado_orden(self, oid):
            return _padre()

        def ordenes_de_simbolos(self, s):
            return []

        def posicion(self, t):
            raise RuntimeError("x")

    # Lectura fallida: se avisa y se devuelve FALLO, sin excepción.
    assert reproteccion.reproteger(_Roto(), ahora=_AHORA) == {"FCEL": reproteccion.FALLO}


# -- seguimiento: la historia del trade se cierra con el P&L de las dos partes

def _padre_tp_cancelado():
    return _padre(tp_status="canceled")


def test_seguimiento_sin_error_falso_mientras_el_stop_repuesto_vive():
    r = _revision()
    rem = {"id": "rps-orden-1", "type": "stop", "status": "new"}
    assert seguimiento._evaluar(r, _padre_tp_cancelado(), None, rem) is None


def test_seguimiento_cierra_con_pnl_combinado_cuando_el_stop_repuesto_se_llena():
    r = _revision()
    rem = {"id": "rps-orden-1", "type": "stop", "status": "filled", "filled_avg_price": "17.05"}
    t = seguimiento._evaluar(r, _padre_tp_cancelado(), None, rem)
    assert t.resultado == "cerrada"
    # 37 × (17.90 − 17.30) + 5 × (17.05 − 17.30) = 22.20 − 1.25
    assert t.pnl == pytest.approx(20.95)
    assert t.precio_salida == 17.05
    assert "CERRADA" in t.mensaje


def test_seguimiento_avisa_si_el_stop_repuesto_muere_sin_llenarse():
    r = _revision()
    rem = {"id": "rps-orden-1", "type": "stop", "status": "canceled"}
    t = seguimiento._evaluar(r, _padre_tp_cancelado(), None, rem)
    assert t.resultado == "cerrada" and "ERROR" in t.mensaje


def test_seguimiento_cierre_eod_con_tp_parcial_cuenta_las_dos_partes():
    r = _revision(cierre_order_id="eod-1")
    cierre_datos = {"status": "filled", "filled_avg_price": "17.50"}
    t = seguimiento._evaluar(r, _padre_tp_cancelado(), cierre_datos)
    # 37 × 0.60 + 5 × 0.20
    assert t.pnl == pytest.approx(23.20)


def test_seguimiento_cierre_eod_sin_parcial_igual_que_antes():
    r = _revision(cierre_order_id="eod-1")
    padre = _padre(tp_status="canceled", tp_filled="0")
    t = seguimiento._evaluar(r, padre, {"status": "filled", "filled_avg_price": "17.50"})
    assert t.pnl == pytest.approx(round(0.20 * 42, 2))


def test_seguimiento_revisar_lee_la_orden_del_remanente(monkeypatch):
    revisiones = [_revision()]
    guardadas = []
    enviados = []
    monkeypatch.setattr(seguimiento.estado, "cargar", lambda: revisiones)
    monkeypatch.setattr(seguimiento.estado, "guardar", lambda rs: guardadas.append(True))
    monkeypatch.setattr(seguimiento, "enviar_telegram", lambda t: enviados.append(t))
    reproteccion._anotar(_PADRE, {"ticker": "FCEL", "order_id": "rps-orden-1", "tipo": "stop"})

    class _C:
        def estado_orden(self, oid):
            if oid == _PADRE:
                return _padre_tp_cancelado()
            assert oid == "rps-orden-1"
            return {"id": oid, "type": "stop", "status": "filled", "filled_avg_price": "17.05"}

        def posiciones(self):
            return []

    cambiadas = seguimiento.revisar(_C(), ahora=_AHORA)
    assert [r.resultado for r in cambiadas] == ["cerrada"]
    assert revisiones[0].pnl == pytest.approx(20.95)
    assert len(enviados) == 1 and "CERRADA" in enviados[0]


# -- cliente -----------------------------------------------------------------

def test_cliente_stop_remanente_payload_paper_gtc_con_id(monkeypatch):
    from momentum_paper_trader import alpaca_client
    payloads = []

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "stop-1", "status": "accepted"}

    def _post(url, json, headers, timeout):
        payloads.append((url, json))
        return _R()

    monkeypatch.setattr(alpaca_client.requests, "post", _post)
    resp = AlpacaPaperClient("c", "s").colocar_stop_remanente("FCEL", "5", 17.05, "rps-FCEL-2b0ab90e6323")
    assert resp["id"] == "stop-1"
    url, p = payloads[0]
    assert url == "https://paper-api.alpaca.markets/v2/orders"
    assert p == {"symbol": "FCEL", "qty": "5", "side": "sell", "type": "stop",
                 "stop_price": "17.05", "time_in_force": "gtc",
                 "client_order_id": "rps-FCEL-2b0ab90e6323"}


def test_cliente_stop_remanente_duplicado_devuelve_el_existente(monkeypatch):
    from momentum_paper_trader import alpaca_client

    class _R:
        status_code = 422

        def raise_for_status(self):
            raise RuntimeError("422")

        def json(self):
            return {"message": "client_order_id must be unique"}

    monkeypatch.setattr(alpaca_client.requests, "post", lambda *a, **kw: _R())
    client = AlpacaPaperClient("c", "s")
    existente = {"id": "stop-1", "symbol": "FCEL", "client_order_id": "rps-FCEL-x", "status": "new"}
    monkeypatch.setattr(client, "ordenes_de_simbolos", lambda s: [existente])
    assert client.colocar_stop_remanente("FCEL", "5", 17.05, "rps-FCEL-x")["id"] == "stop-1"


@pytest.mark.parametrize("qty", ["0", "-1", "", "x"])
def test_cliente_stop_remanente_no_inventa_cantidad(monkeypatch, qty):
    from momentum_paper_trader import alpaca_client
    monkeypatch.setattr(alpaca_client.requests, "post",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no")))
    with pytest.raises(ValueError):
        AlpacaPaperClient("c", "s").colocar_stop_remanente("FCEL", qty, 17.05, "id")


# -- run.py: orden del tick ----------------------------------------------------

def test_run_repone_antes_de_reconciliar(monkeypatch):
    from momentum_paper_trader import run
    llamadas = []
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "s")
    monkeypatch.setattr("sys.argv", ["run"])
    monkeypatch.setattr(run.seguimiento, "revisar", lambda *a, **k: llamadas.append("seguimiento") or [])
    monkeypatch.setattr(run.cierre, "cerrar_si_toca", lambda *a, **k: llamadas.append("cierre") or [])
    monkeypatch.setattr(run.reproteccion, "reproteger", lambda *a, **k: llamadas.append("reproteccion") or {})
    monkeypatch.setattr(run.reconciliacion, "revisar", lambda *a, **k: llamadas.append("reconciliacion") or [])
    monkeypatch.setattr(run.halts, "revisar_posiciones_abiertas", lambda *a, **k: None)
    monkeypatch.setattr(run, "ejecutar", lambda *a, **k: [])
    monkeypatch.setattr(run.archivo, "archivar_revisadas", lambda *a, **k: [])
    monkeypatch.setattr(run.telemetria, "registrar_corrida", lambda *a, **k: None)
    monkeypatch.setattr(run.gates_sombra, "refrescar_regimen", lambda *a, **k: None)
    monkeypatch.setattr(run, "_clima_de_la_watchlist", lambda: None)
    run.main()
    assert llamadas == ["seguimiento", "cierre", "reproteccion", "reconciliacion"]


def test_registro_fuera_de_git_en_el_dir_de_avisos(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_AVISOS_DIR", str(tmp_path / "x"))
    reproteccion._anotar("p1", {"ticker": "A", "order_id": "o1"})
    data = json.loads((tmp_path / "x" / "reprotecciones.json").read_text())
    assert data["p1"]["order_id"] == "o1"
    assert reproteccion.orden_de("p1")["order_id"] == "o1"
    assert reproteccion.orden_de("otro") is None
