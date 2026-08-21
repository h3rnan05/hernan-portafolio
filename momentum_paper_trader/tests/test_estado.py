from __future__ import annotations

import json

from momentum_paper_trader.estado import RevisionIA, cargar, guardar, ya_revisada


def _revision(ticker="RKLB", creado_en="2026-08-11T14:00:00+00:00", entro=True) -> RevisionIA:
    return RevisionIA(
        ticker=ticker, creado_en=creado_en, entro=entro, confianza=8,
        razonamiento="catalizador sólido, asimetría clara", timestamp="2026-08-11T14:05:00+00:00",
        order_id="abc123" if entro else None, cantidad=65 if entro else None,
        precio_entrada=78.42 if entro else None, stop=76.90 if entro else None,
        objetivo=82.50 if entro else None,
    )


def test_cargar_archivo_inexistente_devuelve_lista_vacia(tmp_path):
    assert cargar(tmp_path / "no_existe.json") == []


def test_guardar_y_cargar_roundtrip(tmp_path):
    path = tmp_path / "revisiones.json"
    guardar([_revision()], path)
    recargadas = cargar(path)
    assert len(recargadas) == 1
    assert recargadas[0].ticker == "RKLB"
    assert recargadas[0].order_id == "abc123"


def test_guardar_y_cargar_roundtrip_revision_rechazada(tmp_path):
    # Una revisión que terminó en "no entrar" también debe persistir --
    # sin orden, pero con el razonamiento, para no volver a preguntar.
    path = tmp_path / "revisiones.json"
    guardar([_revision(entro=False)], path)
    recargadas = cargar(path)
    assert len(recargadas) == 1
    assert recargadas[0].entro is False
    assert recargadas[0].order_id is None


def test_cargar_archivo_corrupto_no_lanza(tmp_path):
    path = tmp_path / "revisiones.json"
    path.write_text("{esto no es json")
    assert cargar(path) == []


def test_cargar_formato_inesperado_no_lanza(tmp_path):
    path = tmp_path / "revisiones.json"
    path.write_text(json.dumps([1, 2, 3]))
    assert cargar(path) == []


def test_ya_revisada_true_para_misma_clave():
    revisiones = [_revision("RKLB", "2026-08-11T14:00:00+00:00")]
    assert ya_revisada(revisiones, "RKLB", "2026-08-11T14:00:00+00:00") is True


def test_ya_revisada_true_aunque_la_ia_haya_dicho_que_no():
    # El punto central del rediseño: un "no" de la IA también cuenta como
    # revisada -- si no, cada corrida le volvería a preguntar lo mismo.
    revisiones = [_revision("RKLB", "2026-08-11T14:00:00+00:00", entro=False)]
    assert ya_revisada(revisiones, "RKLB", "2026-08-11T14:00:00+00:00") is True


def test_ya_revisada_false_para_mismo_ticker_distinto_creado_en():
    # El mismo ticker disparando en dos días distintos son dos
    # oportunidades distintas -- cada una debe poder revisarse por separado.
    revisiones = [_revision("RKLB", "2026-08-11T14:00:00+00:00")]
    assert ya_revisada(revisiones, "RKLB", "2026-08-12T14:00:00+00:00") is False


def test_ya_revisada_false_para_ticker_distinto():
    revisiones = [_revision("RKLB", "2026-08-11T14:00:00+00:00")]
    assert ya_revisada(revisiones, "TTWO", "2026-08-11T14:00:00+00:00") is False
