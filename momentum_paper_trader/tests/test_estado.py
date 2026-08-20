from __future__ import annotations

import json

from momentum_paper_trader.estado import OrdenRegistrada, cargar, guardar, ya_procesada


def _orden(ticker="RKLB", creado_en="2026-08-11T14:00:00+00:00") -> OrdenRegistrada:
    return OrdenRegistrada(
        ticker=ticker, creado_en=creado_en, order_id="abc123", cantidad=65,
        precio_entrada=78.42, stop=76.90, objetivo=82.50, timestamp="2026-08-11T14:05:00+00:00",
    )


def test_cargar_archivo_inexistente_devuelve_lista_vacia(tmp_path):
    assert cargar(tmp_path / "no_existe.json") == []


def test_guardar_y_cargar_roundtrip(tmp_path):
    path = tmp_path / "ordenes.json"
    guardar([_orden()], path)
    recargadas = cargar(path)
    assert len(recargadas) == 1
    assert recargadas[0].ticker == "RKLB"
    assert recargadas[0].order_id == "abc123"


def test_cargar_archivo_corrupto_no_lanza(tmp_path):
    path = tmp_path / "ordenes.json"
    path.write_text("{esto no es json")
    assert cargar(path) == []


def test_cargar_formato_inesperado_no_lanza(tmp_path):
    path = tmp_path / "ordenes.json"
    path.write_text(json.dumps([1, 2, 3]))
    assert cargar(path) == []


def test_ya_procesada_true_para_misma_clave():
    ordenes = [_orden("RKLB", "2026-08-11T14:00:00+00:00")]
    assert ya_procesada(ordenes, "RKLB", "2026-08-11T14:00:00+00:00") is True


def test_ya_procesada_false_para_mismo_ticker_distinto_creado_en():
    # El mismo ticker disparando en dos días distintos son dos
    # oportunidades distintas -- cada una debe poder generar su propia orden.
    ordenes = [_orden("RKLB", "2026-08-11T14:00:00+00:00")]
    assert ya_procesada(ordenes, "RKLB", "2026-08-12T14:00:00+00:00") is False


def test_ya_procesada_false_para_ticker_distinto():
    ordenes = [_orden("RKLB", "2026-08-11T14:00:00+00:00")]
    assert ya_procesada(ordenes, "TTWO", "2026-08-11T14:00:00+00:00") is False
