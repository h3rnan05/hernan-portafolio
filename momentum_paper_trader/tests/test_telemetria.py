"""Pruebas de la telemetría paper -- sin red, archivos en tmp_path."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from momentum_paper_trader import telemetria


def test_delta_ms_none_si_falta_un_extremo():
    # Un campo ausente no es un cero. Eso ya nos costó semanas.
    assert telemetria.delta_ms(None, "2026-08-11T14:00:04+00:00") is None
    assert telemetria.delta_ms("2026-08-11T14:00:00+00:00", None) is None


def test_delta_ms_none_si_el_iso_no_parsea():
    assert telemetria.delta_ms("m", "2026-08-11T14:00:04+00:00") is None
    assert telemetria.delta_ms("2026-08-11T14:00:00+00:00", "no-es-fecha") is None


def test_delta_ms_calcula_la_diferencia():
    assert telemetria.delta_ms(
        "2026-08-11T14:00:00+00:00", "2026-08-11T14:00:04+00:00") == 4000.0


def test_percentil_vacio_es_none_no_cero():
    assert telemetria.percentil([], 0.50) is None
    assert telemetria.percentil([], 0.95) is None


def test_percentil_una_muestra_es_esa_muestra():
    assert telemetria.percentil([1200.0], 0.50) == 1200.0
    assert telemetria.percentil([1200.0], 0.95) == 1200.0


def test_percentil_p50_y_p95_interpolan():
    muestras = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert telemetria.percentil(muestras, 0.50) == 30.0
    assert telemetria.percentil(muestras, 0.95) == 48.0


def test_instrumentar_copia_relojes_y_calcula_sin_inventar():
    e = SimpleNamespace(
        market_event_ts="2026-08-11T14:00:00+00:00",
        watchlist_escrito_ts="2026-08-11T14:04:00+00:00",
        actualizado_en="2026-08-11T14:03:50+00:00",
        signal_latency_ms=180_000.0,
    )
    r = SimpleNamespace(
        timestamp="2026-08-11T14:08:00+00:00",
        market_event_ts=None, watchlist_escrito_ts=None,
        executor_leido_ts=None, ia_decision_ts=None,
        latencia_descubrimiento_ms=None, latencia_e2e_ms=None,
    )
    telemetria.instrumentar_revision(
        r, e,
        executor_leido_ts="2026-08-11T14:05:00+00:00",
        ia_decision_ts="2026-08-11T14:07:00+00:00",
    )
    assert r.market_event_ts == e.market_event_ts
    assert r.watchlist_escrito_ts == e.watchlist_escrito_ts
    assert r.executor_leido_ts == "2026-08-11T14:05:00+00:00"
    assert r.ia_decision_ts == "2026-08-11T14:07:00+00:00"
    assert r.latencia_descubrimiento_ms == 240_000.0
    assert r.latencia_e2e_ms == 480_000.0


def test_instrumentar_cae_a_actualizado_en_si_no_hay_sello_de_escritura():
    # Entradas anteriores al campo: actualizado_en SÍ se midió.
    e = SimpleNamespace(
        market_event_ts="2026-08-11T14:00:00+00:00",
        watchlist_escrito_ts=None,
        actualizado_en="2026-08-11T14:03:00+00:00",
    )
    r = SimpleNamespace(
        timestamp="2026-08-11T14:04:00+00:00",
        market_event_ts=None, watchlist_escrito_ts=None,
        executor_leido_ts=None, ia_decision_ts=None,
        latencia_descubrimiento_ms=None, latencia_e2e_ms=None,
    )
    telemetria.instrumentar_revision(
        r, e, executor_leido_ts="t", ia_decision_ts="t")
    assert r.watchlist_escrito_ts == "2026-08-11T14:03:00+00:00"
    assert r.latencia_descubrimiento_ms == 180_000.0


def test_instrumentar_no_inventa_latencia_sin_vela():
    e = SimpleNamespace(
        market_event_ts=None, watchlist_escrito_ts="2026-08-11T14:04:00+00:00",
        actualizado_en="2026-08-11T14:04:00+00:00",
    )
    r = SimpleNamespace(
        timestamp="2026-08-11T14:08:00+00:00",
        market_event_ts=None, watchlist_escrito_ts=None,
        executor_leido_ts=None, ia_decision_ts=None,
        latencia_descubrimiento_ms=None, latencia_e2e_ms=None,
    )
    telemetria.instrumentar_revision(
        r, e, executor_leido_ts="t", ia_decision_ts="t")
    assert r.latencia_descubrimiento_ms is None
    assert r.latencia_e2e_ms is None


def test_como_dict_serializa_el_esquema_y_el_presupuesto():
    m = telemetria.Metricas(
        timestamp="2026-08-11T14:10:00+00:00",
        triggered_nuevos=2, revisiones=2, ordenes_colocadas=1,
        paper_step_success_zero_orders=0,
        latencias_descubrimiento_ms=[120_000.0],
        latencias_alerta_ms=[180_000.0],
        latencias_e2e_ms=[600_000.0],
    )
    d = m.como_dict()
    assert d["triggered_nuevos"] == 2
    assert d["revisiones"] == 2
    assert d["ordenes_colocadas"] == 1
    assert d["paper_step_success_zero_orders"] == 0
    assert d["presupuesto_velas"] == telemetria.PRESUPUESTO_VELAS
    assert d["presupuesto_ms"] == telemetria.PRESUPUESTO_MS
    assert d["latencias_ms"]["e2e"] == [600_000.0]
    assert d["sobre_presupuesto"]["e2e"] == 1
    assert d["sobre_presupuesto"]["descubrimiento"] == 0
    json.dumps(d)  # tiene que ser JSON puro


def test_cerrar_corrida_marca_cero_ordenes_solo_si_no_coloco():
    vacia = telemetria.Metricas(triggered_nuevos=3)
    vacia.cerrar_corrida()
    assert vacia.paper_step_success_zero_orders == 1

    con_orden = telemetria.Metricas(ordenes_colocadas=1)
    con_orden.cerrar_corrida()
    assert con_orden.paper_step_success_zero_orders == 0


def test_anotar_revision_no_cuenta_rechazo_como_orden():
    m = telemetria.Metricas()
    rechazo = SimpleNamespace(
        entro=False, order_id=None,
        latencia_descubrimiento_ms=10.0, latencia_e2e_ms=20.0,
    )
    m.anotar_revision(rechazo, signal_latency_ms=15.0)
    assert m.revisiones == 1
    assert m.ordenes_colocadas == 0
    assert m.latencias_alerta_ms == [15.0]


def test_resumir_sesion_calcula_p50_p95_del_dia():
    corridas = [
        {"triggered_nuevos": 1, "revisiones": 1, "ordenes_colocadas": 0,
         "paper_step_success_zero_orders": 1,
         "latencias_ms": {"descubrimiento": [100.0], "alerta": [200.0], "e2e": [300.0]}},
        {"triggered_nuevos": 1, "revisiones": 1, "ordenes_colocadas": 1,
         "paper_step_success_zero_orders": 0,
         "latencias_ms": {"descubrimiento": [400.0], "alerta": [500.0], "e2e": [600.0]}},
    ]
    sesion = telemetria.resumir_sesion(corridas)
    assert sesion["triggered_nuevos"] == 2
    assert sesion["revisiones"] == 2
    assert sesion["ordenes_colocadas"] == 1
    assert sesion["paper_step_success_zero_orders"] == 1
    assert sesion["latencia_p50_ms"]["e2e"] == 450.0
    assert sesion["muestras_latencia"]["e2e"] == 2
    assert sesion["latencia_p50_ms"]["descubrimiento"] == 250.0


def test_resumir_sesion_sin_muestras_deja_percentiles_en_none():
    sesion = telemetria.resumir_sesion([
        {"triggered_nuevos": 0, "revisiones": 0, "ordenes_colocadas": 0,
         "paper_step_success_zero_orders": 1, "latencias_ms": {}},
    ])
    assert sesion["latencia_p50_ms"]["e2e"] is None
    assert sesion["latencia_p95_ms"]["alerta"] is None


def test_registrar_corrida_crea_jsonl_y_rollup_de_su_fuente(tmp_path):
    ahora = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
    m = telemetria.Metricas(triggered_nuevos=1, revisiones=1)
    m.latencias_e2e_ms.append(240_000.0)
    m.cerrar_corrida()
    path = telemetria.registrar_corrida(m, tmp_path, ahora, fuente="vps")
    assert path is not None
    assert path == tmp_path / "2026-09-11" / "vps" / "events.jsonl"
    lineas = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(lineas) == 1
    assert lineas[0]["triggered_nuevos"] == 1
    assert lineas[0]["fuente"] == "vps"
    rollup = json.loads((tmp_path / "2026-09-11" / "vps" / "sesion.json").read_text())
    assert rollup["sesion"]["triggered_nuevos"] == 1
    assert rollup["sesion"]["latencia_p50_ms"]["e2e"] == 240_000.0
    assert not (tmp_path / "2026-09-11.json").exists()


def test_varias_corridas_acumulan_la_sesion(tmp_path):
    ahora = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
    a = telemetria.Metricas(triggered_nuevos=1, revisiones=1, ordenes_colocadas=1)
    a.latencias_e2e_ms.append(100.0)
    a.cerrar_corrida()
    b = telemetria.Metricas(triggered_nuevos=2, revisiones=2)
    b.latencias_e2e_ms.append(200.0)
    b.cerrar_corrida()
    telemetria.registrar_corrida(a, tmp_path, ahora, fuente="vps")
    telemetria.registrar_corrida(b, tmp_path, ahora, fuente="vps")
    data = json.loads((tmp_path / "2026-09-11" / "vps" / "sesion.json").read_text())
    assert len(data["corridas"]) == 2
    assert data["sesion"]["triggered_nuevos"] == 3
    assert data["sesion"]["ordenes_colocadas"] == 1
    assert data["sesion"]["paper_step_success_zero_orders"] == 1
    assert data["sesion"]["latencia_p50_ms"]["e2e"] == 150.0


def test_vps_y_gha_no_comparten_archivo(tmp_path):
    ahora = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
    telemetria.registrar_corrida(
        telemetria.Metricas(triggered_nuevos=1), tmp_path, ahora, fuente="vps")
    telemetria.registrar_corrida(
        telemetria.Metricas(triggered_nuevos=4), tmp_path, ahora, fuente="gha")
    assert (tmp_path / "2026-09-11" / "vps" / "events.jsonl").is_file()
    assert (tmp_path / "2026-09-11" / "gha" / "events.jsonl").is_file()
    assert not (tmp_path / "2026-09-11.json").exists()
    sesion = telemetria.cargar_sesion("2026-09-11", tmp_path)
    assert sesion["triggered_nuevos"] == 5


def test_cargar_dias_lee_legacy_y_jsonl(tmp_path):
    (tmp_path / "2026-09-10.json").write_text(json.dumps({
        "corridas": [{"triggered_nuevos": 2, "revisiones": 1,
                      "ordenes_colocadas": 0, "paper_step_success_zero_orders": 1,
                      "latencias_ms": {}}],
    }))
    ahora = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
    telemetria.registrar_corrida(
        telemetria.Metricas(triggered_nuevos=3), tmp_path, ahora, fuente="vps")
    corridas = telemetria.cargar_dias("2026-09-10", "2026-09-11", tmp_path)
    assert [c["triggered_nuevos"] for c in corridas] == [2, 3]
    # Un JSON legado ilegible no inventa corridas ni tumba el resto.
    (tmp_path / "2026-09-09.json").write_text("{roto")
    assert len(telemetria.cargar_dias("2026-09-09", "2026-09-11", tmp_path)) == 2


def test_linea_jsonl_rota_no_tumba_ni_borra_las_demas(tmp_path):
    dest = tmp_path / "2026-09-11" / "vps" / "events.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"triggered_nuevos": 1}\n{roto\n')
    ahora = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
    path = telemetria.registrar_corrida(
        telemetria.Metricas(triggered_nuevos=2), tmp_path, ahora, fuente="vps")
    assert path is not None
    # Append-only: la línea rota sigue ahí, la nueva se suma.
    assert "{roto" in path.read_text()
    corridas = telemetria.cargar_dias("2026-09-11", "2026-09-11", tmp_path)
    assert [c["triggered_nuevos"] for c in corridas] == [1, 2]


def test_fuente_env_manda_sobre_github_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("MOMENTUM_TELEM_FUENTE", "vps")
    assert telemetria.resolver_fuente() == "vps"


def test_fuente_desconocida_no_se_inventa_como_vps(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("MOMENTUM_TELEM_FUENTE", "prod")
    assert telemetria.resolver_fuente() == "local"


def test_fuente_gha_si_github_actions(monkeypatch):
    monkeypatch.delenv("MOMENTUM_TELEM_FUENTE", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert telemetria.resolver_fuente() == "gha"


def test_un_fallo_al_guardar_nunca_propaga(tmp_path, monkeypatch):
    monkeypatch.setattr(
        telemetria.Path, "mkdir",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("disco lleno")))
    assert telemetria.registrar_corrida(
        telemetria.Metricas(), tmp_path / "x", fuente="vps") is None
