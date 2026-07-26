"""Pruebas de persistencia del tracker -- sin red, usa un archivo
temporal (nunca toca `alertas_enviadas.json` real del repo)."""

from __future__ import annotations

from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import Oportunidad
from momentum_hunter.tracker import cargar, desde_oportunidad, guardar, registrar


def _oportunidad(ticker="ACME") -> Oportunidad:
    return Oportunidad(
        ticker=ticker, nombre="Acme Corp", urgencia="Muy Alta", urgencia_emoji="🔴",
        titular_corto="rompiendo AHORA", por_que_aparecio=["Hace 5 min: fda -- x (Reuters)."],
        patron="🚀 GAP AND GO", veredicto_temprano=True, veredicto_texto="Vamos temprano: x.",
        entrada=5.20, stop=5.00, objetivo=5.60, invalidacion="Se cancela si pierde $5.00.",
        que_espero="Que aguante sobre VWAP.", score=92.0,
        catalizador=Catalizador(tipo="fda", titular="x", fuente="Reuters"),
        fecha="2026-07-26T13:35:00+00:00",
    )


def test_desde_oportunidad_copia_los_niveles():
    o = _oportunidad()
    a = desde_oportunidad(o)
    assert a.ticker == o.ticker
    assert a.precio_entrada == o.entrada
    assert a.stop == o.stop
    assert a.objetivo1 == o.objetivo
    assert a.clasificacion == o.patron
    assert a.resultados_pct == {}
    assert a.resuelta is False


def test_registrar_y_cargar_roundtrip(tmp_path):
    path = tmp_path / "alertas.json"
    nuevas = registrar([_oportunidad("ACME"), _oportunidad("BETA")], path=path)
    assert len(nuevas) == 2

    cargadas = cargar(path)
    assert {a.ticker for a in cargadas} == {"ACME", "BETA"}


def test_registrar_acumula_sobre_lo_existente(tmp_path):
    path = tmp_path / "alertas.json"
    registrar([_oportunidad("ACME")], path=path)
    registrar([_oportunidad("BETA")], path=path)
    assert {a.ticker for a in cargar(path)} == {"ACME", "BETA"}


def test_cargar_archivo_inexistente_devuelve_vacio(tmp_path):
    assert cargar(tmp_path / "no_existe.json") == []


def test_guardar_y_cargar_conserva_resultados(tmp_path):
    path = tmp_path / "alertas.json"
    a = desde_oportunidad(_oportunidad())
    a.resultados_pct["1d"] = 0.05
    a.resuelta = False
    guardar([a], path)
    recargada = cargar(path)[0]
    assert recargada.resultados_pct["1d"] == 0.05
