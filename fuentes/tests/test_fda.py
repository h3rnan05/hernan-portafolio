"""openFDA: solo ORIG es aprobación, tipo por prefijo, sin hora, dudoso = FALTANTE.

Fixtures REALES (grabadas en el VPS el 2026-09-28): primera página de
septiembre y de agosto 2026. La segunda página de septiembre es sintética
(el VPS solo grabó la primera) y el 404 es de un mes futuro."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, fda
from fuentes.cache import Cache
from fuentes.grabar import TransporteGrabado, cargar, transporte_desde

GRABADAS = ["aprobaciones_202609", "aprobaciones_202609_p2", "aprobaciones_202608", "aprobaciones_sintetico_404_203001"]


def _fuente(tmp_path, fallar=None):
    t = transporte_desde("fda", GRABADAS, fallar)
    return fda.AprobacionesFDA(Cache(tmp_path / "c"), fda.cliente_fda(transport=t, dormir=lambda s: None),
                               hoy=date(2026, 10, 15)), t


def _clave(ini, fin, skip):
    return TransporteGrabado.clave(fda.URL, {"search": f"submissions.submission_status_date:[{ini} TO {fin}] AND submissions.submission_status:AP",
                                             "limit": 100, "skip": skip})


def test_mapa_del_repo_separa_alta_de_dudosa():
    m = fda.cargar_mapa()
    assert m["SRRK"] == ["SCHOLAR ROCK INC"] and m["IONS"] == ["IONIS PHARMS INC"]
    assert m["FICD"] == []      # dudosa: está, pero sin nombres -> FALTANTE
    assert "AAPL" not in m


def test_tipo_por_prefijo_y_solo_orig_suppl():
    assert fda.tipo_de("ANDA203901") == "ANDA" and fda.tipo_de("NDA220210") == "NDA" and fda.tipo_de("BLA761463") == "BLA"
    assert fda.tipo_de("X123") == ""


def test_leer_pagina_real_de_septiembre():
    import json
    aps, total = fda.leer_pagina(json.loads(cargar("fda", "aprobaciones_202609")["texto"]), date(2026, 9, 1), date(2026, 9, 30))
    assert total == 141
    orig = [a for a in aps if not a.suplemento]
    assert len(orig) == 19 and len(aps) - len(orig) == 106
    srrk = next(a for a in aps if a.aplicacion == "BLA761463")
    assert srrk == fda.Aprobacion(date(2026, 9, 11), "SCHOLAR ROCK INC", "BLA761463", "BLA", False, "TYPE 1")
    assert all(a.tipo in ("NDA", "ANDA", "BLA") for a in aps)
    with pytest.raises(ErrorFuente, match="cuerpo"):
        fda.leer_pagina({"results": []}, date(2026, 9, 1), date(2026, 9, 30))
    assert fda._fecha_fda("2026-09-24") is None and fda._fecha_fda("20261332") is None


def test_aprobacion_orig_del_dia_y_de_la_vispera(tmp_path):
    f, t = _fuente(tmp_path)
    # Scholar Rock, BLA761463 ORIG aprobada el viernes 11/9/2026.
    fila = f.columnas("SRRK", datetime(2026, 9, 11, 14, 0, tzinfo=UTC))
    assert fila == {"fda_aprobacion_dia": True, "fda_aprobacion_vispera": False, "fda_aplicacion": "BLA761463",
                    "fda_tipo": "BLA", "fda_clase": "TYPE 1", "fda_laboratorio": "SCHOLAR ROCK INC", "fda_suplementos_dia": 0}
    # El lunes 14/9 la víspera hábil es el viernes 11.
    fila = f.columnas("SRRK", datetime(2026, 9, 14, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is False and fila["fda_aprobacion_vispera"] is True and fila["fda_tipo"] == "BLA"
    # Ionis, NDA220210 ORIG el 3/9.
    fila = f.columnas("IONS", datetime(2026, 9, 3, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is True and fila["fda_tipo"] == "NDA" and fila["fda_aplicacion"] == "NDA220210"
    assert len(t.pedidos) == 2   # dos páginas, un solo mes


def test_un_suplemento_no_es_aprobacion_pero_se_cuenta(tmp_path):
    f, _ = _fuente(tmp_path)
    # Eli Lilly: 2 suplementos AP el 18/9, ninguna ORIG.
    fila = f.columnas("LLY", datetime(2026, 9, 18, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is False and fila["fda_suplementos_dia"] == 2 and fila["fda_aplicacion"] is None
    # Segunda página (sintética): Ficticia C tiene ORIG el 24 y SUPPL el 25.
    assert f.columnas("FICC", datetime(2026, 9, 24, 14, 0, tzinfo=UTC))["fda_aprobacion_dia"] is True
    fila = f.columnas("FICC", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is False and fila["fda_aprobacion_vispera"] is True and fila["fda_suplementos_dia"] == 1


def test_laboratorio_dudoso_o_desconocido_es_faltante(tmp_path):
    f, t = _fuente(tmp_path)
    for tk in ("FICD", "AAPL"):
        fila = f.columnas(tk, datetime(2026, 9, 11, 14, 0, tzinfo=UTC))
        assert all(v is FALTANTE for v in fila.values()), tk
    assert t.pedidos == []   # sin nombre que buscar no se pide nada


def test_mes_sin_resultados_404_es_false_no_faltante(tmp_path):
    f, _ = _fuente(tmp_path)
    fila = f.columnas("SRRK", datetime(2030, 1, 20, 14, 0, tzinfo=UTC))
    assert fila["fda_aprobacion_dia"] is False and fila["fda_suplementos_dia"] == 0


def test_mes_con_paginas_sin_grabar_es_faltante(tmp_path):
    # Agosto 2026 real: total=365, solo la primera página grabada. La segunda cae: FALTANTE, no "cero".
    f, _ = _fuente(tmp_path, fallar={_clave("20260801", "20260831", 100)})
    fila = f.columnas("SRRK", datetime(2026, 8, 20, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_mes_caido_es_faltante(tmp_path):
    f, _ = _fuente(tmp_path, fallar={_clave("20260901", "20260930", 0)})
    assert all(v is FALTANTE for v in f.columnas("SRRK", datetime(2026, 9, 11, 14, 0, tzinfo=UTC)).values())


def test_no_hay_columna_de_hora():
    assert not any("hora" in n for n in fda.AprobacionesFDA._NOMBRES)
