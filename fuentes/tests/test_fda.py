"""openFDA: aprobación por día (sin hora), mapa laboratorio→ticker, dudoso = FALTANTE."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, fda
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde


def _fuente(tmp_path, fallar=None):
    t = transporte_desde("fda", ["aprobaciones_202609", "aprobaciones_202608_404"], fallar)
    return fda.AprobacionesFDA(Cache(tmp_path / "c"), fda.cliente_fda(transport=t, dormir=lambda s: None),
                               hoy=date(2026, 10, 15)), t


def test_mapa_del_repo_separa_alta_de_dudosa():
    m = fda.cargar_mapa()
    assert m["FICC"] == ["FICTICIA C BIOPHARMA INC", "FICTICIA C PHARMACEUTICALS LLC"]
    assert m["FICD"] == []      # dudosa: está, pero sin nombres -> FALTANTE
    assert "FICA" not in m


def test_leer_pagina_solo_ap_del_rango():
    import json
    aps, total = fda.leer_pagina(json.loads(cargar("fda", "aprobaciones_202609")["texto"]), date(2026, 9, 1), date(2026, 9, 30))
    assert total == 3 and [(a.fecha, a.aplicacion) for a in aps] == [
        (date(2026, 9, 24), "BLA761999"), (date(2026, 9, 25), "NDA219999"), (date(2026, 9, 10), "ANDA209999"),
        (date(2026, 9, 25), "ANDA209999")]
    with pytest.raises(ErrorFuente, match="cuerpo"):
        fda.leer_pagina({"results": []}, date(2026, 9, 1), date(2026, 9, 30))
    assert fda._fecha_fda("2026-09-24") is None and fda._fecha_fda("20261332") is None


def test_columnas_aprobacion_del_dia_y_de_la_vispera(tmp_path):
    f, t = _fuente(tmp_path)
    fila = f.columnas("FICC", datetime(2026, 9, 24, 14, 0, tzinfo=UTC))
    assert fila == {"fda_aprobacion_dia": True, "fda_aprobacion_vispera": False, "fda_aplicacion": "BLA761999",
                    "fda_tipo": "BLA", "fda_laboratorio": "FICTICIA C BIOPHARMA INC"}
    fila = f.columnas("FICC", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is False and fila["fda_aprobacion_vispera"] is True
    # El lunes 28 la víspera hábil es el viernes 25: nada.
    fila = f.columnas("FICC", datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is False and fila["fda_aprobacion_vispera"] is False and fila["fda_aplicacion"] is None
    assert len(t.pedidos) == 1


def test_laboratorio_dudoso_o_desconocido_es_faltante(tmp_path):
    f, t = _fuente(tmp_path)
    for tk in ("FICD", "FICA"):
        fila = f.columnas(tk, datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
        assert all(v is FALTANTE for v in fila.values()), tk
    assert t.pedidos == []   # sin nombre que buscar no se pide nada


def test_mes_sin_resultados_404_es_false_no_faltante(tmp_path):
    f, _ = _fuente(tmp_path)
    fila = f.columnas("FICC", datetime(2026, 8, 20, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is False and fila["fda_aprobacion_vispera"] is False


def test_mes_caido_es_faltante(tmp_path):
    f, _ = _fuente(tmp_path, fallar={fda.URL + "?" + "limit=100&search=submissions.submission_status_date%3A%5B20260901+TO+20260930%5D+AND+submissions.submission_status%3AAP&skip=0"})
    fila = f.columnas("FICC", datetime(2026, 9, 24, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_no_hay_columna_de_hora():
    assert not any("hora" in n for n in fda.AprobacionesFDA._NOMBRES)
