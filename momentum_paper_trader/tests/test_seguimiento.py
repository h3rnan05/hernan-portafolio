"""Pruebas del seguimiento del ciclo de vida -- Alpaca y Telegram
mockeados por completo, `revisiones.json` en un archivo temporal."""

from __future__ import annotations

from momentum_paper_trader import estado, seguimiento
from momentum_paper_trader.estado import RevisionIA


def _revision_con_orden(ticker="RKLB", resultado=None) -> RevisionIA:
    return RevisionIA(
        ticker=ticker, creado_en="2026-08-21T14:00:00+00:00", entro=True, confianza=8,
        razonamiento="catalizador sólido", timestamp="2026-08-21T14:05:00+00:00",
        order_id=f"orden-{ticker}", cantidad=65, precio_entrada=78.42, stop=76.90,
        objetivo=82.50, resultado=resultado,
    )


class _FakeClient:
    def __init__(self, respuestas: dict[str, dict], falla_para: set[str] | None = None,
                 abiertas: list[dict] | None = None, abiertas_rotas: bool = False) -> None:
        self._respuestas = respuestas
        self._falla_para = falla_para or set()
        self.consultadas: list[str] = []
        self._abiertas = abiertas or []
        self._abiertas_rotas = abiertas_rotas
        self.canceladas: list[str] = []

    def estado_orden(self, order_id: str) -> dict:
        self.consultadas.append(order_id)
        if order_id in self._falla_para:
            raise RuntimeError("orden no encontrada")
        return self._respuestas[order_id]

    def ordenes_abiertas(self) -> list[dict]:
        if self._abiertas_rotas:
            raise RuntimeError("Alpaca caído")
        return self._abiertas

    def cancelar_ordenes_de(self, ticker: str, ordenes: list[dict]) -> int:
        ids = [o["id"] for o in ordenes if o.get("symbol") == ticker]
        self.canceladas += ids
        return len(ids)


def _parchear(monkeypatch, tmp_path, revisiones):
    real_cargar, real_guardar = estado.cargar, estado.guardar
    path = tmp_path / "revisiones.json"
    real_guardar(revisiones, path)
    monkeypatch.setattr(estado, "cargar", lambda p=path: real_cargar(p))
    monkeypatch.setattr(estado, "guardar", lambda rs, p=path: real_guardar(rs, p))
    enviados: list[str] = []
    monkeypatch.setattr(seguimiento, "enviar_telegram", lambda t: enviados.append(t))
    return path, enviados


# ------------------------- _evaluar: la lógica pura de transición -------------------------

def test_entrada_llenada_pasa_a_abierta_con_precio_real():
    r = _revision_con_orden()
    datos = {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "new"}, {"type": "stop", "status": "held"}]}

    resultado, pnl, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "abierta"
    assert pnl is None
    assert "LLENADA" in mensaje
    assert "$78.40" in mensaje
    assert "[PAPER]" in mensaje
    assert "RKLB" in mensaje
    assert "2026-08-21T14:00:00+00:00" in mensaje  # signal_id


def test_take_profit_llenado_es_objetivo_con_ganancia():
    r = _revision_con_orden(resultado="abierta")
    datos = {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "filled", "filled_avg_price": "82.50"},
        {"type": "stop", "status": "canceled"}]}

    resultado, pnl, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "objetivo"
    assert pnl == round((82.50 - 78.40) * 65, 2)
    assert "CERRADA" in mensaje
    assert "objetivo" in mensaje
    assert f"+${pnl:,.2f}" in mensaje


def test_stop_llenado_es_stop_con_perdida():
    r = _revision_con_orden(resultado="abierta")
    datos = {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "canceled"},
        {"type": "stop", "status": "filled", "filled_avg_price": "76.85"}]}

    resultado, pnl, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "stop"
    assert pnl == round((76.85 - 78.40) * 65, 2)
    assert pnl < 0
    assert "CERRADA" in mensaje
    assert "stop" in mensaje
    assert f"-${abs(pnl):,.2f}" in mensaje


def test_salida_en_la_misma_pasada_que_la_entrada_va_directo_al_cierre():
    # Entre corrida y corrida el trade entero pudo abrir Y cerrar -- no
    # debe mandar "abierta" tardío, sino directamente el desenlace.
    r = _revision_con_orden(resultado=None)
    datos = {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "filled", "filled_avg_price": "82.50"},
        {"type": "stop", "status": "canceled"}]}

    resultado, _, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "objetivo"
    assert "LLENADA" not in mensaje


def test_orden_cancelada_sin_llenar_es_no_ejecutada():
    r = _revision_con_orden()
    datos = {"status": "expired", "filled_avg_price": None, "legs": []}

    resultado, pnl, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "no_ejecutada"
    assert pnl is None
    assert mensaje == ""   # se persiste, no se avisa: no hubo trade


def test_entrada_todavia_esperando_no_genera_novedad():
    r = _revision_con_orden()
    datos = {"status": "new", "filled_avg_price": None, "legs": []}
    assert seguimiento._evaluar(r, datos) is None


def test_abierta_sin_cambios_no_repite_el_aviso():
    r = _revision_con_orden(resultado="abierta")
    datos = {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "new"}, {"type": "stop", "status": "held"}]}
    assert seguimiento._evaluar(r, datos) is None


def test_posicion_llena_con_patas_muertas_avisa_cerrada():
    r = _revision_con_orden(resultado="abierta")
    datos = {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "expired"}, {"type": "stop", "status": "expired"}]}

    resultado, _, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "cerrada"
    assert "ERROR" in mensaje
    assert "sin salidas" in mensaje


# ------------------------- revisar: integración con persistencia -------------------------

def test_revisar_avisa_llenada(monkeypatch, tmp_path):
    r = _revision_con_orden()
    _, enviados = _parchear(monkeypatch, tmp_path, [r])
    client = _FakeClient({"orden-RKLB": {
        "status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
            {"type": "limit", "status": "new"}, {"type": "stop", "status": "held"}]}})

    cambiadas = seguimiento.revisar(client)

    assert [c.resultado for c in cambiadas] == ["abierta"]
    assert len(enviados) == 1
    assert "LLENADA" in enviados[0] and "RKLB" in enviados[0]


def test_revisar_actualiza_persiste_y_avisa(monkeypatch, tmp_path):
    r = _revision_con_orden()
    path, enviados = _parchear(monkeypatch, tmp_path, [r])
    client = _FakeClient({"orden-RKLB": {
        "status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
            {"type": "limit", "status": "filled", "filled_avg_price": "82.50"},
            {"type": "stop", "status": "canceled"}]}})

    cambiadas = seguimiento.revisar(client)

    assert [c.resultado for c in cambiadas] == ["objetivo"]
    assert len(enviados) == 1 and "CERRADA" in enviados[0] and "objetivo" in enviados[0]
    persistidas = estado.cargar(path)
    assert persistidas[0].resultado == "objetivo"
    assert persistidas[0].pnl == round((82.50 - 78.40) * 65, 2)


def test_revisar_no_consulta_resultados_terminales(monkeypatch, tmp_path):
    r = _revision_con_orden(resultado="objetivo")
    _parchear(monkeypatch, tmp_path, [r])
    client = _FakeClient({})

    assert seguimiento.revisar(client) == []
    assert client.consultadas == []


def test_revisar_ignora_revisiones_sin_orden(monkeypatch, tmp_path):
    rechazada = RevisionIA(
        ticker="TTWO", creado_en="x", entro=False, confianza=3,
        razonamiento="no", timestamp="x")
    _parchear(monkeypatch, tmp_path, [rechazada])
    client = _FakeClient({})

    assert seguimiento.revisar(client) == []
    assert client.consultadas == []


def test_revisar_fallo_de_una_orden_no_tumba_las_demas(monkeypatch, tmp_path):
    r_rota = _revision_con_orden("ROTO")
    r_ok = _revision_con_orden("OK")
    _, enviados = _parchear(monkeypatch, tmp_path, [r_rota, r_ok])
    client = _FakeClient(
        {"orden-OK": {"status": "expired", "filled_avg_price": None, "legs": []}},
        falla_para={"orden-ROTO"})

    cambiadas = seguimiento.revisar(client)

    assert [c.ticker for c in cambiadas] == ["OK"]
    assert enviados == []   # OK expiró sin fill: persistido, sin Telegram


# ------------------------- entrada sin llenar: se cancela a los 15 min (2026-09-22) -------------------------

from datetime import UTC, datetime  # noqa: E402

from momentum_paper_trader.config import PaperTraderConfig  # noqa: E402

_COLOCADA = datetime(2026, 8, 21, 14, 5, 0, tzinfo=UTC)   # = timestamp de _revision_con_orden
_CFG = PaperTraderConfig()


def test_entrada_vencida_es_funcion_pura_y_conservadora():
    r = _revision_con_orden()
    esperando = {"status": "new", "filled_qty": "0"}
    # 14 min: todavía no. 16 min: sí, y devuelve los minutos.
    assert seguimiento.entrada_vencida(r, esperando, _CFG, _COLOCADA.replace(minute=19)) is None
    assert seguimiento.entrada_vencida(r, esperando, _CFG, _COLOCADA.replace(minute=21)) == 16.0
    # Llena, parcial o muerta: nunca se cancela desde acá.
    assert seguimiento.entrada_vencida(r, {"status": "filled"}, _CFG, _COLOCADA.replace(hour=15)) is None
    assert seguimiento.entrada_vencida(r, {"status": "partially_filled", "filled_qty": "10"}, _CFG, _COLOCADA.replace(hour=15)) is None
    assert seguimiento.entrada_vencida(r, {"status": "canceled"}, _CFG, _COLOCADA.replace(hour=15)) is None
    # Sin timestamp legible no se inventa una edad.
    r2 = _revision_con_orden()
    r2.timestamp = "ayer"
    assert seguimiento.entrada_vencida(r2, esperando, _CFG, _COLOCADA.replace(hour=15)) is None


def test_revisar_cancela_la_entrada_vencida_y_avisa_cancelada(monkeypatch, tmp_path):
    path, enviados = _parchear(monkeypatch, tmp_path, [_revision_con_orden()])
    cli = _FakeClient({"orden-RKLB": {"status": "new", "filled_qty": "0"}},
                      abiertas=[{"id": "orden-RKLB", "symbol": "RKLB"}, {"id": "otra", "symbol": "ZZZ"}])
    cambiadas = seguimiento.revisar(cli, _CFG, ahora=_COLOCADA.replace(minute=21))
    assert cli.canceladas == ["orden-RKLB"]                       # solo la de este ticker/orden
    assert [r.resultado for r in cambiadas] == ["no_ejecutada"]
    assert estado.cargar(path)[0].resultado == "no_ejecutada"
    assert len(enviados) == 1 and "CANCELADA" in enviados[0] and "RKLB" in enviados[0]
    assert "16 min" in enviados[0] and "$78.42" in enviados[0]


def test_revisar_no_cancela_una_entrada_joven(monkeypatch, tmp_path):
    path, enviados = _parchear(monkeypatch, tmp_path, [_revision_con_orden()])
    cli = _FakeClient({"orden-RKLB": {"status": "new", "filled_qty": "0"}},
                      abiertas=[{"id": "orden-RKLB", "symbol": "RKLB"}])
    assert seguimiento.revisar(cli, _CFG, ahora=_COLOCADA.replace(minute=10)) == []
    assert cli.canceladas == [] and enviados == []
    assert estado.cargar(path)[0].resultado is None


def test_si_no_se_pudo_cancelar_la_orden_sigue_viva_para_la_proxima_pasada(monkeypatch, tmp_path):
    """Nunca se marca no_ejecutada una orden que puede seguir viva."""
    path, enviados = _parchear(monkeypatch, tmp_path, [_revision_con_orden()])
    # Alpaca no devuelve las abiertas.
    cli = _FakeClient({"orden-RKLB": {"status": "new", "filled_qty": "0"}}, abiertas_rotas=True)
    assert seguimiento.revisar(cli, _CFG, ahora=_COLOCADA.replace(hour=15)) == []
    assert estado.cargar(path)[0].resultado is None and enviados == []
    # La orden ya no está entre las abiertas (se llenó hace un instante): tampoco se toca.
    cli2 = _FakeClient({"orden-RKLB": {"status": "new", "filled_qty": "0"}}, abiertas=[])
    assert seguimiento.revisar(cli2, _CFG, ahora=_COLOCADA.replace(hour=15)) == []
    assert cli2.canceladas == []


def test_una_cancelacion_ajena_sin_fill_sigue_siendo_silenciosa(monkeypatch, tmp_path):
    """Si la orden murió por otra vía (expiró al cierre), no hubo trade y
    no hay Telegram: solo la cancelación PROPIA avisa."""
    path, enviados = _parchear(monkeypatch, tmp_path, [_revision_con_orden()])
    cli = _FakeClient({"orden-RKLB": {"status": "expired", "filled_qty": "0"}})
    cambiadas = seguimiento.revisar(cli, _CFG, ahora=_COLOCADA.replace(hour=20))
    assert [r.resultado for r in cambiadas] == ["no_ejecutada"] and enviados == []
