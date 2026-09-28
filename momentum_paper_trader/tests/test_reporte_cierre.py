"""Reporte de cierre con subastas oficiales -- núcleo puro, sin red."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from momentum_hunter.data.subastas import SubastaDia
from momentum_paper_trader import reporte_cierre as rc
from momentum_paper_trader.estado import RevisionIA

HOY = "2026-09-28"
# 20:30 UTC = 16:30 Nueva York = 14:30 Monterrey (UTC−6).
AHORA = datetime(2026, 9, 28, 20, 30, tzinfo=UTC)
DIAS = {
    "ACME": {
        "2026-09-25": SubastaDia("2026-09-25", 48.0, 1.0, 50.0, 9.0),
        HOY: SubastaDia(HOY, 55.0, 4.0, 57.2, 8.0),
    },
}


def _senal(ticker, ts="2026-09-28T14:05:00+00:00", gap=0.12):
    return SimpleNamespace(ticker=ticker, market_event_ts=ts, gap_pct_congelado=gap)


def _trade(ticker, ts="2026-09-28T14:07:00+00:00"):
    return RevisionIA(ticker=ticker, creado_en="c", entro=True, confianza=7, razonamiento="",
                      timestamp=ts, order_id="o1", cantidad=10, precio_entrada=56.0,
                      stop=55.0, objetivo=58.0, resultado="cerrada", pnl=4.0, precio_salida=56.4)


def test_senal_y_trade_del_dia_con_numeros_oficiales():
    filas = rc.construir([_senal("ACME")], [_trade("ACME")], DIAS, HOY)
    f = filas[0]
    assert f.origen == "señal+trade"
    assert f.gap_oficial == pytest.approx(0.10)
    assert f.gap_hunter == pytest.approx(0.12)
    assert f.movimiento_dia == pytest.approx(57.2 / 55.0 - 1)
    assert f.diferencia_vs_cierre == pytest.approx((57.2 - 56.4) * 10)


def test_lo_de_otro_dia_no_entra():
    viejo = "2026-09-25T14:05:00+00:00"
    assert rc.construir([_senal("ACME", viejo)], [_trade("ACME", viejo)], DIAS, HOY) == []


def test_la_fecha_es_la_de_nueva_york():
    # 01:00 UTC del 29/9 = 21:00 del 28/9 en Nueva York: sigue siendo hoy.
    filas = rc.construir([_senal("ACME", "2026-09-29T01:00:00+00:00")], [], DIAS, HOY)
    assert [f.ticker for f in filas] == ["ACME"]


def test_sin_subasta_de_cierre_no_se_inventa_con_el_ultimo_trade():
    dias = {"ACME": {HOY: SubastaDia(HOY, 55.0, 4.0, None, None)}}
    f = rc.construir([], [_trade("ACME")], dias, HOY)[0]
    assert f.cierre_oficial is None and f.diferencia_vs_cierre is None and f.gap_oficial is None
    texto = rc.formatear([f], HOY, AHORA)
    assert "sin dato" in texto and "no se reemplaza" in texto


def test_un_rechazo_de_la_ia_no_es_un_trade():
    rechazo = _trade("ACME")
    rechazo.entro, rechazo.order_id = False, None
    assert rc.construir([], [rechazo], DIAS, HOY) == []


def test_el_texto_trae_utc_y_monterrey():
    texto = rc.formatear(rc.construir([_senal("ACME")], [], DIAS, HOY), HOY, AHORA)
    assert "20:30 UTC" in texto and "14:30 Monterrey (UTC−6)" in texto
    assert "gap oficial +10.00%" in texto


def test_guardar_escribe_fuera_de_git(tmp_path, monkeypatch):
    monkeypatch.setenv(rc.ENV_DIR, str(tmp_path / "rep"))
    filas = rc.construir([_senal("ACME")], [], DIAS, HOY)
    carpeta = rc.guardar(filas, "x", HOY)
    assert (carpeta / f"{HOY}.json").exists() and (carpeta / f"{HOY}.txt").read_text() == "x\n"


def test_el_reporte_no_importa_el_cliente_del_broker():
    import inspect
    fuente = inspect.getsource(rc)
    assert "alpaca_client" not in fuente and "place_order" not in fuente
