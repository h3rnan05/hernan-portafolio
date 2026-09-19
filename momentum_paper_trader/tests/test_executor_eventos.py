"""Eventos del panel (`dashboard.events.log_event`) dentro del ejecutor.

Son solo observabilidad. Estas pruebas fijan dos cosas:
  1. Si `log_event` falla, el ejecutor hace EXACTAMENTE lo mismo
     (mismas órdenes, mismas revisiones, mismo return).
  2. Cuando funciona, escribe lo que el panel necesita, y en dry-run no
     escribe nada.

Reutiliza los dobles de `test_executor.py` sin modificarlo."""

from __future__ import annotations

import json

import pytest

from momentum_paper_trader import estado, executor
from momentum_paper_trader.tests.test_executor import (
    AHORA,
    CFG,
    _DECISION_ENTRA,
    _DECISION_NO_ENTRA,
    _FakeAlpacaClient,
    _entrada_triggered,
    _parchear,
)

ESCENARIOS = {
    "orden_colocada": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0)),
    "ia_rechaza": dict(decision=_DECISION_NO_ENTRA, cliente=dict(cash=10_000.0)),
    "alpaca_rechaza": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0, falla_para={"RKLB"})),
    "ticker_comprometido": dict(decision=_DECISION_ENTRA,
                                cliente=dict(cash=40_000.0, posiciones=[{"symbol": "RKLB"}])),
    "mercado_cerrado": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0, mercado_abierto=False)),
    "cuenta_rota": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0, cuenta_rota=True)),
}


def _correr(monkeypatch, tmp_path, escenario, dry_run=False):
    esc = ESCENARIOS[escenario]
    tmp_path.mkdir(parents=True, exist_ok=True)
    _, rev_path, enviados, _ = _parchear(monkeypatch, tmp_path, [_entrada_triggered()],
                                         decision=esc["decision"])
    client = _FakeAlpacaClient(**esc["cliente"])
    nuevas = executor.ejecutar(client, CFG, dry_run=dry_run, ahora=AHORA)
    revisiones = [(r.ticker, r.entro, r.order_id, r.cantidad, r.motivo_no_operada)
                  for r in estado.cargar(rev_path)]
    return (
        [(r.ticker, r.order_id, r.cantidad) for r in nuevas],
        list(client.ordenes_colocadas),
        revisiones,
        list(enviados),
    )


def _log_event_que_explota(*args, **kwargs):
    raise RuntimeError("disco lleno, permisos, lo que sea")


@pytest.mark.parametrize("escenario", sorted(ESCENARIOS))
def test_el_ejecutor_hace_lo_mismo_si_log_event_falla(monkeypatch, tmp_path, escenario):
    monkeypatch.setenv("DASH_EVENTOS", str(tmp_path / "normal" / "events.jsonl"))
    normal = _correr(monkeypatch, tmp_path / "a", escenario)

    monkeypatch.setattr(executor, "log_event", _log_event_que_explota)
    con_falla = _correr(monkeypatch, tmp_path / "b", escenario)

    assert con_falla == normal


def _eventos(ruta):
    if not ruta.exists():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_orden_colocada_deja_la_cinta_completa(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    _correr(monkeypatch, tmp_path, "orden_colocada")

    ev = _eventos(ruta)
    assert [e["tipo"] for e in ev] == ["rechequeo", "deteccion", "decision", "orden"]
    orden = ev[-1]
    assert orden["ticker"] == "RKLB" and orden["estado"] == "enviada" and orden["lado"] == "buy"
    assert ev[2]["entra"] is True
    # Las velas salen de la latencia e2e que telemetría ya midió; si no
    # se midió, None -- nunca un 0 inventado.
    assert orden["velas"] is None or orden["velas"] >= 0


def test_bloqueo_y_rechazo_quedan_registrados(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    _correr(monkeypatch, tmp_path / "x", "ticker_comprometido")
    _correr(monkeypatch, tmp_path / "y", "alpaca_rechaza")

    ev = _eventos(ruta)
    bloqueos = [e for e in ev if e["tipo"] == "bloqueo_riesgo"]
    assert bloqueos and bloqueos[0]["limite"] == "ticker_comprometido"
    rechazos = [e for e in ev if e["tipo"] == "orden" and e["estado"] == "rechazada"]
    assert len(rechazos) == 1 and rechazos[0]["ticker"] == "RKLB"


def test_dry_run_no_escribe_eventos(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    _correr(monkeypatch, tmp_path, "orden_colocada", dry_run=True)
    assert not ruta.exists()


def test_log_event_nunca_lanza(monkeypatch, tmp_path):
    from dashboard.events import log_event

    # Ruta imposible (un archivo usado como carpeta) y un campo que json no sabe serializar.
    bloqueo = tmp_path / "soy_un_archivo"
    bloqueo.write_text("x")
    monkeypatch.setenv("DASH_EVENTOS", str(bloqueo / "events.jsonl"))
    log_event("orden", ticker="RKLB", raro=object())

    class _Str:
        def __str__(self):
            raise ValueError("ni siquiera str()")

    monkeypatch.setenv("DASH_EVENTOS", str(tmp_path / "ok" / "events.jsonl"))
    log_event("orden", ticker="RKLB", raro=_Str())


def test_orden_trae_latencia_completa_cuando_el_hunter_guardo_las_velas(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    e = _entrada_triggered()
    e.market_event_ts = "2026-08-11T13:57:00+00:00"   # 3 min antes de AHORA
    e.velas_desde_ruptura = 6
    _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_ENTRA)
    executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=AHORA)

    (orden,) = [x for x in _eventos(ruta) if x["tipo"] == "orden"]
    assert orden["medida"] == "ruptura_a_orden"
    assert orden["velas_desde_ruptura"] == 6
    # velas_desde_disparo sale del reloj real de la corrida, así que solo
    # se verifica la suma, no un valor fijo.
    assert orden["velas"] == round(6 + orden["velas_desde_disparo"], 1)


def test_sin_velas_desde_ruptura_la_latencia_no_se_calcula(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    e = _entrada_triggered()
    e.market_event_ts = "2026-08-11T13:57:00+00:00"
    assert e.velas_desde_ruptura is None
    _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_ENTRA)
    executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=AHORA)

    (orden,) = [x for x in _eventos(ruta) if x["tipo"] == "orden"]
    assert orden["velas"] is None
