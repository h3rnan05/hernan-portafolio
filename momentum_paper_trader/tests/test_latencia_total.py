"""Latencia completa ruptura -> orden (VERSION_MEDICION 2).

`e2e` mide desde la vela del disparo; el presupuesto de 8 velas cuenta
desde la ruptura. `total` suma las velas que el hunter ya contaba al
disparar. Si falta cualquiera de las dos partes, no se calcula."""

from __future__ import annotations

from types import SimpleNamespace

from momentum_paper_trader import telemetria


def _rev(e2e_ms, entro=True):
    return SimpleNamespace(entro=entro, order_id="x" if entro else None,
                           latencia_descubrimiento_ms=None, latencia_e2e_ms=e2e_ms)


def test_velas_totales_suma_las_dos_partes():
    assert telemetria.velas_totales(6, 3 * 60_000) == 9.0


def test_velas_totales_none_si_falta_una_parte():
    assert telemetria.velas_totales(None, 60_000) is None
    assert telemetria.velas_totales(3, None) is None
    assert telemetria.velas_totales(True, 60_000) is None   # un bool no es un conteo


def test_cero_real_se_respeta():
    assert telemetria.velas_totales(0, 0.0) == 0.0


def test_metricas_cuentan_total_contra_presupuesto():
    m = telemetria.Metricas()
    m.anotar_revision(_rev(3 * 60_000), None, 6)     # 9 velas -> sobre presupuesto
    m.anotar_revision(_rev(1 * 60_000), None, 2)     # 3 velas
    m.anotar_revision(_rev(1 * 60_000), None, None)  # sin dato: no entra
    d = m.como_dict()
    assert d["version_medicion"] == 2
    assert d["latencias_velas"]["total"] == [9.0, 3.0]
    assert d["sobre_presupuesto"]["total"] == 1
    # e2e sigue midiendo solo desde el disparo: ninguna pasa de 8 min
    assert d["sobre_presupuesto"]["e2e"] == 0


def test_anotar_sin_el_argumento_nuevo_sigue_funcionando():
    m = telemetria.Metricas()
    m.anotar_revision(_rev(60_000), None)
    assert m.latencias_total_velas == []


def test_sesion_con_corridas_viejas_no_inventa_muestras():
    vieja = {"triggered_nuevos": 1, "latencias_ms": {"e2e": [120_000.0]}}
    nueva = {"latencias_velas": {"total": [9.0, 4.0]}, "latencias_ms": {"e2e": [60_000.0, 60_000.0]}}
    s = telemetria.resumir_sesion([vieja, nueva])
    assert s["latencia_total_velas"]["muestras"] == 2
    assert s["latencia_total_velas"]["sobre_presupuesto"] == 1
    assert s["version_medicion"] == 2
