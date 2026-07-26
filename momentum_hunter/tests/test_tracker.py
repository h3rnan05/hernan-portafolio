"""Pruebas de persistencia del tracker -- sin red, usa un archivo
temporal (nunca toca `alertas_enviadas.json` real del repo)."""

from __future__ import annotations

from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import CONFIG
from momentum_hunter.models import FactoresMomentum, Metadata, Oportunidad
from momentum_hunter.report import construir_oportunidad
from momentum_hunter.tracker import cargar, desde_oportunidad, guardar, registrar


def _oportunidad(ticker="ACME") -> Oportunidad:
    from momentum_hunter.alerts import Candidato
    from momentum_hunter.scoring import Puntuacion

    c = Candidato(
        ticker=ticker, nombre="Acme Corp", precio=10.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(rvol=6.0, breakout_20d=True, distancia_max_52s=0.99, atr=0.5),
        catalizador=Catalizador(tipo="fda", titular="x", fuente="Reuters"),
        meta=Metadata(ticker=ticker),
        puntuacion=Puntuacion(ticker=ticker, score_total=92.0, sub={}),
    )
    return construir_oportunidad(c, CONFIG, tiene_opciones_fn=lambda t: False)


def test_desde_oportunidad_copia_los_niveles():
    o = _oportunidad()
    a = desde_oportunidad(o)
    assert a.ticker == o.ticker
    assert a.precio_entrada == o.entrada
    assert a.stop == o.stop
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
