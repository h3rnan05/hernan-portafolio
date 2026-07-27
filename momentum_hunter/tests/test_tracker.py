"""Pruebas de persistencia del tracker -- sin red, usa un archivo
temporal (nunca toca `alertas_enviadas.json` real del repo). Verifica
también que la "memoria" para el aprendizaje futuro (patrón, hora,
catalizador, float, gap, RVOL) se guarde completa."""

from __future__ import annotations

from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import Oportunidad
from momentum_hunter.tracker import cargar, desde_oportunidad, guardar, registrar


def _oportunidad(ticker="ACME") -> Oportunidad:
    return Oportunidad(
        ticker=ticker, nombre="Acme Corp", urgencia="Muy Alta", urgencia_emoji="🔴",
        titular_corto="rompiendo con fuerza justo al abrir",
        que_paso="Hace 5 min: la FDA le aprobó algo importante.",
        que_hizo_mercado="Entró muchísimo dinero apenas abrió el mercado.",
        que_pasa_ahora="Sigue subiendo sin parar desde que abrió el mercado.",
        vale_la_pena=True, por_que_vale_la_pena="Sí. Apenas lleva unos minutos.",
        por_que_esta_alerta="Es una oportunidad sólida.",
        entrada=5.20, stop=5.00, objetivo=5.60,
        invalidacion="Si vuelve a caer por debajo de $5.00, se cancela la idea.",
        catalizador=Catalizador(tipo="fda", titular="x", fuente="Reuters"),
        score=92.0, fecha="2026-07-26T13:35:00+00:00",
        patron_clave="gap_and_go", hora_utc=13, catalizador_tipo="fda",
        float_acciones=12_000_000.0, gap_pct=0.10, rvol=4.0,
    )


def test_desde_oportunidad_copia_los_niveles():
    o = _oportunidad()
    a = desde_oportunidad(o)
    assert a.ticker == o.ticker
    assert a.precio_entrada == o.entrada
    assert a.stop == o.stop
    assert a.objetivo1 == o.objetivo
    assert a.clasificacion == o.patron_clave
    assert a.resultados_pct == {}
    assert a.resuelta is False


def test_desde_oportunidad_copia_la_materia_prima_de_aprendizaje():
    o = _oportunidad()
    a = desde_oportunidad(o)
    assert a.hora_utc == 13
    assert a.catalizador_tipo == "fda"
    assert a.float_acciones == 12_000_000.0
    assert a.gap_pct == 0.10
    assert a.rvol == 4.0


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
    assert recargada.hora_utc == 13
