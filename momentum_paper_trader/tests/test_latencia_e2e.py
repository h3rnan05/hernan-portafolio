"""Latencia por tramo: los tramos suman el total y no se inventa nada."""

from __future__ import annotations

import pytest

from momentum_paper_trader import latencia_e2e as le


def _rev(**kw):
    base = {"ticker": "ACME", "creado_en": "c1", "entro": True, "order_id": "o1",
            "market_event_ts": "2026-09-28T15:00:00+00:00",
            "watchlist_escrito_ts": "2026-09-28T15:01:12+00:00",
            "executor_leido_ts": "2026-09-28T15:01:14+00:00",
            "ia_decision_ts": "2026-09-28T15:01:25+00:00",
            "timestamp": "2026-09-28T15:01:26+00:00"}
    base.update(kw)
    return base


WL = {"velas_desde_ruptura": 2, "data_received_ts": "2026-09-28T15:01:10+00:00",
      "evaluador_ts": "2026-09-28T15:01:11+00:00"}


def test_los_tramos_suman_exactamente_el_total():
    c = le.cadena(_rev(), WL)
    assert c.tramos == {"patron": 120.0, "vela": 60.0, "dato": 10.0, "evaluacion": 1.0,
                        "persistir": 1.0, "entrega": 2.0, "ia": 11.0, "orden": 1.0}
    assert c.total_s == pytest.approx(sum(c.tramos.values())) == 206.0


def test_sin_watchlist_los_tramos_del_hunter_quedan_sin_medir():
    c = le.cadena(_rev(), None)
    assert c.tramos["patron"] is None and c.tramos["dato"] is None and c.tramos["evaluacion"] is None
    assert c.tramos["ia"] == 11.0
    assert c.total_s is None          # sin velas desde la ruptura no hay total, no se asume 0


def test_un_tramo_negativo_se_reporta_y_no_entra_a_la_estadistica():
    c = le.cadena(_rev(executor_leido_ts="2026-09-28T15:01:00+00:00"), WL)
    assert "entrega" in c.inconsistentes
    r = le.agregar([c])
    assert r["tramos"]["entrega"] is None
    assert "ACME|c1" in r["inconsistentes"]


def test_solo_ordenes_enviadas_y_desde_la_fecha():
    revs = [_rev(), _rev(ticker="NO", entro=False, order_id=None),
            _rev(ticker="VIEJA", timestamp="2026-09-01T15:01:26+00:00")]
    cadenas = le.construir(revs, {"ACME|c1": WL}, desde="2026-09-20")
    assert [c.ticker for c in cadenas] == ["ACME"]


def test_identifica_el_tramo_mas_lento_y_los_empates():
    lenta_ia = _rev(ticker="B", creado_en="c2", ia_decision_ts="2026-09-28T15:02:14+00:00",
                    timestamp="2026-09-28T15:02:15+00:00")
    r = le.agregar([le.cadena(_rev(), WL), le.cadena(lenta_ia, WL)])
    assert r["mas_lento_sistema_mediana"] == ["ia"]
    assert r["mas_lento_mediana"] == ["patron"]
    empate = le._maximos({"dato": {"mediana_s": 11}, "ia": {"mediana_s": 11}}, "mediana_s")
    assert empate == ["dato", "ia"]


def test_la_cola_explica_que_tramo_se_comio_el_tiempo():
    normales = [le.cadena(_rev(ticker=f"T{i}", creado_en=str(i)), WL) for i in range(9)]
    bloqueada = le.cadena(_rev(ticker="VRT", creado_en="x", executor_leido_ts="2026-09-28T16:48:00+00:00",
                               ia_decision_ts="2026-09-28T16:48:10+00:00",
                               timestamp="2026-09-28T16:48:11+00:00"), WL)
    r = le.agregar(normales + [bloqueada])
    assert r["cola"][0].startswith("VRT") and "hunter → ejecutor" in r["cola"][0]
    assert "Cola: VRT" in le.formatear(r)
