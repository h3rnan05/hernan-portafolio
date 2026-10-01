"""Memoria de trades (PR-A): solo datos, fail-closed, idempotente."""
import json
from types import SimpleNamespace as NS

from momentum_paper_trader import estado, memoria_trades as mt


def _rev(**kw):
    base = dict(ticker="ABC", creado_en="2026-09-30T15:00:00+00:00", entro=True, confianza=7,
                razonamiento="x", timestamp="2026-09-30T15:10:00+00:00", order_id="o1",
                cantidad=10, precio_entrada=10.0, stop=9.5, objetivo=11.0,
                es_large_cap=True,
                rasgos={"patron": "trend_continuation", "catalizador_tipo": "earnings",
                        "primer_disparo_ts": "2026-09-30T15:05:00+00:00"})
    base.update(kw)
    return estado.RevisionIA(**base)


def _orden(precio=10.0, ts="2026-09-30T15:11:00Z", legs=()):
    return {"status": "filled", "filled_avg_price": str(precio), "filled_at": ts,
            "filled_qty": "10", "legs": list(legs)}


def _leg(tipo, precio, ts="2026-09-30T16:00:00Z", status="filled"):
    return {"type": tipo, "status": status, "filled_avg_price": str(precio), "filled_at": ts,
            "filled_qty": "10"}


def test_stop_da_menos_un_r_y_motivo_stop():
    t = mt.construir_trade(_rev(), _orden(legs=[_leg("stop", 9.5), _leg("limit", None, status="canceled")]))
    assert t["motivo_salida"] == "stop"
    assert t["r"] == -1.0
    assert t["pnl"] == -5.0
    assert t["espera_desde_disparo_min"] == 6.0
    assert t["patron"] == "trend_continuation"
    assert t["cuenta_para_aprender"] is True


def test_objetivo():
    t = mt.construir_trade(_rev(), _orden(legs=[_leg("limit", 11.0)]))
    assert t["motivo_salida"] == "objetivo" and t["r"] == 2.0


def test_cierre_por_liquidacion():
    t = mt.construir_trade(_rev(), _orden(legs=[_leg("stop", None, status="canceled")]),
                           orden_cierre=_orden(precio=10.25, ts="2026-09-30T19:50:00Z"))
    assert t["motivo_salida"] == "cierre" and t["r"] == 0.5


def test_overnight_no_cuenta():
    t = mt.construir_trade(_rev(), _orden(legs=[_leg("stop", 9.4, ts="2026-10-01T14:00:00Z")]))
    assert t["motivo_salida"] == "arrastre_overnight"
    assert t["cuenta_para_aprender"] is False


def test_regla_vieja_antes_de_190_no_cuenta():
    t = mt.construir_trade(_rev(), _orden(ts="2026-09-23T15:11:00Z",
                                          legs=[_leg("limit", 11.0, ts="2026-09-23T16:00:00Z")]))
    assert t["cuenta_para_aprender"] is False


def test_riesgo_cero_o_ausente_da_r_none_no_cero():
    assert mt.construir_trade(_rev(stop=10.0), _orden(legs=[_leg("stop", 9.5)]))["r"] is None
    assert mt.construir_trade(_rev(stop=None), _orden(legs=[_leg("stop", 9.5)]))["r"] is None


def test_sin_fill_o_sin_salida_no_es_trade():
    assert mt.construir_trade(_rev(), {"status": "canceled"}) is None
    assert mt.construir_trade(_rev(), _orden(legs=[_leg("stop", None, status="new")])) is None
    assert mt.construir_trade(_rev(), None) is None


def test_campo_faltante_queda_none():
    t = mt.construir_trade(_rev(rasgos=None, es_large_cap=None), _orden(legs=[_leg("limit", 11.0)]))
    assert t["patron"] is None and t["es_large_cap"] is None and t["espera_desde_disparo_min"] is None
    assert t["rasgos_origen"] is None


def test_rasgos_de_entrada_nunca_lanza_y_copia():
    e = NS(ultimo_patron="opening_range_breakout", catalizador_tipo="fda", gap_pct_congelado=0.01,
           es_large_cap=False, transiciones=[{"estado": "watching", "timestamp": "a"},
                                             {"estado": "triggered", "timestamp": "b"}])
    r = mt.rasgos_de_entrada(e, NS(fraccion=0.4))
    assert r["patron"] == "opening_range_breakout" and r["gap_pct"] == 0.01
    assert r["primer_disparo_ts"] == "b" and r["fraccion_ia"] == 0.4
    assert r["catalizador_fuente"] is None

    class Rompe:
        def __getattr__(self, k):
            raise RuntimeError("boom")
    assert mt.rasgos_de_entrada(Rompe()) is None


def test_actualizar_idempotente_y_errores_aislados(tmp_path):
    ruta = tmp_path / "m.jsonl"
    revs = [_rev(), _rev(ticker="XYZ", order_id="o2"), _rev(ticker="NO", order_id=None),
            _rev(ticker="ERR", order_id="o3")]
    ordenes = {"o1": _orden(legs=[_leg("limit", 11.0)]), "o2": _orden(legs=[_leg("stop", None, status="new")])}

    def obtener(oid):
        if oid == "o3":
            raise RuntimeError("red")
        return ordenes[oid]
    r1 = mt.actualizar(ruta, revs, obtener)
    assert r1 == {"nuevos": 1, "total": 1, "sin_cerrar": 1, "errores": 1}
    r2 = mt.actualizar(ruta, revs, obtener)
    assert r2["nuevos"] == 0 and len(mt.cargar(ruta)) == 1


def test_respaldo_desde_auditoria(tmp_path):
    aud = tmp_path / "aud"
    aud.mkdir()
    cand = {"ticker": "ABC", "decision": "alertada", "es_large_cap": True,
            "factores_intradia": {"gap_pct": 0.02, "velas_desde_ruptura": 1},
            "evaluacion": {"patron": "micro_pullback", "score_base": 60},
            "catalizador": {"tipo": "fda", "fuente": "X"}, "meta": {"float_acciones": 1e6}}
    (aud / "2026-09-30.json").write_text(json.dumps({"corridas": [
        {"timestamp": "2026-09-30T15:04:00+00:00", "candidatos": [cand]},
        {"timestamp": "2026-09-30T18:00:00+00:00", "candidatos": [dict(cand, evaluacion={"patron": "tarde"})]},
    ]}))
    ruta = tmp_path / "m.jsonl"
    mt.actualizar(ruta, [_rev(rasgos=None)], lambda oid: _orden(legs=[_leg("limit", 11.0)]), aud)
    t = mt.cargar(ruta)[0]
    assert t["patron"] == "micro_pullback" and t["rasgos_origen"] == "auditoria"
    assert t["espera_desde_disparo_min"] == 7.0


def test_linea_corrupta_se_salta(tmp_path):
    ruta = tmp_path / "m.jsonl"
    ruta.write_text('{"order_id": "a"}\nno-json\n')
    assert mt.cargar(ruta) == [{"order_id": "a"}]


def test_revision_vieja_sin_rasgos_sigue_cargando(tmp_path):
    p = tmp_path / "r.json"
    d = {k: v for k, v in _rev().__dict__.items() if k != "rasgos"}
    p.write_text(json.dumps({"revisiones": [d]}))
    assert estado.cargar(p)[0].rasgos is None


def test_bandas_y_franjas():
    from datetime import datetime, UTC
    assert mt.banda_precio(None) is None and mt.banda_precio(4.9) == "<5" and mt.banda_precio(150) == ">=100"
    assert mt.franja_et(datetime(2026, 9, 30, 14, 0, tzinfo=UTC)) == "apertura"
    assert mt.franja_et(datetime(2026, 9, 30, 16, 0, tzinfo=UTC)) == "media"
    assert mt.franja_et(datetime(2026, 9, 30, 19, 0, tzinfo=UTC)) == "tarde"
    assert mt.franja_et(None) is None
