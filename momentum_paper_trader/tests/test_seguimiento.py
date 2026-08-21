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
    def __init__(self, respuestas: dict[str, dict], falla_para: set[str] | None = None) -> None:
        self._respuestas = respuestas
        self._falla_para = falla_para or set()
        self.consultadas: list[str] = []

    def estado_orden(self, order_id: str) -> dict:
        self.consultadas.append(order_id)
        if order_id in self._falla_para:
            raise RuntimeError("orden no encontrada")
        return self._respuestas[order_id]


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
    assert "ENTRADA EJECUTADA" in mensaje
    assert "$78.40" in mensaje
    assert "[PAPER]" in mensaje


def test_take_profit_llenado_es_objetivo_con_ganancia():
    r = _revision_con_orden(resultado="abierta")
    datos = {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "filled", "filled_avg_price": "82.50"},
        {"type": "stop", "status": "canceled"}]}

    resultado, pnl, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "objetivo"
    assert pnl == round((82.50 - 78.40) * 65, 2)
    assert "OBJETIVO ALCANZADO" in mensaje
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
    assert "STOP EJECUTADO" in mensaje
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
    assert "ENTRADA EJECUTADA" not in mensaje


def test_orden_cancelada_sin_llenar_es_no_ejecutada():
    r = _revision_con_orden()
    datos = {"status": "expired", "filled_avg_price": None, "legs": []}

    resultado, pnl, mensaje = seguimiento._evaluar(r, datos)

    assert resultado == "no_ejecutada"
    assert pnl is None
    assert "NO EJECUTADA" in mensaje
    assert "Sin posición abierta" in mensaje


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
    assert "SIN SALIDAS ACTIVAS" in mensaje


# ------------------------- revisar: integración con persistencia -------------------------

def test_revisar_actualiza_persiste_y_avisa(monkeypatch, tmp_path):
    r = _revision_con_orden()
    path, enviados = _parchear(monkeypatch, tmp_path, [r])
    client = _FakeClient({"orden-RKLB": {
        "status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
            {"type": "limit", "status": "filled", "filled_avg_price": "82.50"},
            {"type": "stop", "status": "canceled"}]}})

    cambiadas = seguimiento.revisar(client)

    assert [c.resultado for c in cambiadas] == ["objetivo"]
    assert len(enviados) == 1 and "OBJETIVO" in enviados[0]
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
    assert len(enviados) == 1
