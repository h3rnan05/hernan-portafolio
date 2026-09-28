"""FINRA short interest: cortes quincenales, fecha de publicación oficial, JSON, 204 = sin datos.

Fixtures REALES (VPS, 2026-09-28): cortes 2026-09-15 y 2026-08-31 recortados a
150 filas, un 204 de un corte inexistente y el 400 que da pedir CSV."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, finra_short
from fuentes.cache import Cache
from fuentes.grabar import TransporteGrabado, cargar, transporte_desde

# Calendario de prueba: el 15/9 lleva la publicación oficial (24/9), que la
# estimación (+8 hábiles = 25/9) no acierta. Solo de pruebas.
CALENDARIO_PRUEBA = {date(2026, 9, 15): date(2026, 9, 24), date(2026, 8, 31): date(2026, 9, 10)}


def _cuerpo(corte, limit=5000):
    return {"limit": limit, "offset": 0, "compareFilters": [{"fieldName": "settlementDate", "compareType": "EQUAL", "fieldValue": corte}]}


def _fuente(tmp_path, float_yahoo=None, calendario=None, fallar=None):
    t = transporte_desde("finra_short", ["corte_20260915", "corte_20260831"], fallar)
    # El 204 real se grabó con limit=5; se sirve también bajo el cuerpo que manda el código.
    reg204 = cargar("finra_short", "corte_20260829_http204")
    t.registros[TransporteGrabado.clave(finra_short.URL, None, _cuerpo("2026-08-29"))] = reg204
    c = finra_short.cliente_finra(transport=t, transport_post=t, dormir=lambda s: None)
    cal = calendario if calendario is not None else CALENDARIO_PRUEBA
    return finra_short.ShortInterestFinra(Cache(tmp_path / "c"), c, float_yahoo, cal), t


def test_cortes_quincenales_corridos_al_dia_habil_previo():
    cortes = finra_short.cortes_entre(date(2026, 8, 1), date(2026, 9, 30))
    # 15/8/2026 es sábado -> viernes 14; 31/8 lunes; 15/9 martes; 30/9 miércoles.
    assert cortes == [date(2026, 8, 14), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30)]


def test_el_calendario_del_repo_es_la_tabla_oficial_2026():
    cal = finra_short.cargar_calendario()
    assert len(cal) == 24 and cal[date(2026, 8, 31)] == date(2026, 9, 10) and cal[date(2026, 9, 15)] == date(2026, 9, 24)
    assert cal[date(2026, 12, 31)] == date(2027, 1, 12)
    assert set(cal) == set(finra_short.cortes_entre(date(2026, 1, 1), date(2026, 12, 31)))


def test_publicacion_del_calendario_o_estimada():
    assert finra_short.publicacion_de(date(2026, 9, 15), CALENDARIO_PRUEBA) == date(2026, 9, 24)
    assert finra_short.publicacion_de(date(2026, 9, 15), {}) == date(2026, 9, 25)   # la estimación no acierta
    assert finra_short.publicacion_de(date(2026, 7, 15), {}) == date(2026, 7, 27)   # oficial: 07-24


def test_leer_json_real_y_rechaza_csv_o_vacio():
    filas = finra_short.leer_json(cargar("finra_short", "corte_20260915")["texto"], date(2026, 9, 15))
    assert len(filas) == 150 and filas["A"] == (5078629.0, 2.54, -10.54) and filas["AA"] == (11984650.0, 3.52, -1.67)
    with pytest.raises(ErrorFuente, match="sin_datos"):
        finra_short.leer_json("", date(2026, 8, 29))
    with pytest.raises(ErrorFuente, match="cuerpo"):
        finra_short.leer_json("settlementDate,symbolCode\n2026-09-15,A\n", date(2026, 9, 15))
    assert cargar("finra_short", "corte_20260915_textcsv_http400")["status"] == 400


def test_el_cliente_pide_json_no_csv(tmp_path):
    f, t = _fuente(tmp_path)
    assert f.cliente()._headers["Accept"] == "application/json"


def test_el_reporte_visible_es_el_publicado_no_el_de_corte(tmp_path):
    f, t = _fuente(tmp_path)
    # 20/9: el corte del 15/9 existe pero se publica el 24/9. Visible: el del 31/8 (publicado el 10/9).
    fila = f.columnas("A", datetime(2026, 9, 20, 14, 0, tzinfo=UTC))
    assert fila["short_interest_corte"] == "2026-08-31" and fila["short_interest_publicado"] == "2026-09-10"
    assert fila["short_interest_acciones"] == 5677007.0 and fila["short_interest_dias"] == 10
    # 24/9 a las 14:00 NY (18:00 UTC): todavía no (se publica a las 16:00 NY).
    assert f.columnas("A", datetime(2026, 9, 24, 18, 0, tzinfo=UTC))["short_interest_corte"] == "2026-08-31"
    # 25/9: ya se ve el del 15/9.
    fila = f.columnas("A", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["short_interest_corte"] == "2026-09-15" and fila["short_interest_acciones"] == 5078629.0
    assert fila["short_interest_dtc"] == 2.54 and fila["short_interest_cambio_pct"] == -10.54
    assert fila["short_interest_pct_float"] is FALTANTE   # sin float de Yahoo
    assert all(p[1].get("json") for p in t.pedidos)   # se pidió por POST con filtro


def test_pct_float_con_yahoo(tmp_path):
    f, _ = _fuente(tmp_path, float_yahoo=lambda t: 253_931_450)
    fila = f.columnas("A", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["short_interest_pct_float"] == 0.02
    f2, _ = _fuente(tmp_path, float_yahoo=lambda t: 0)
    assert f2.columnas("A", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))["short_interest_pct_float"] is FALTANTE


def test_simbolo_ausente_del_reporte_es_none_con_fechas(tmp_path):
    f, _ = _fuente(tmp_path)
    fila = f.columnas("ZZZZ", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["short_interest_acciones"] is None and fila["short_interest_corte"] == "2026-09-15"


def test_corte_204_es_faltante(tmp_path):
    # Con un calendario que apunte a un corte inexistente (29/8), la API responde 204: FALTANTE, no cero.
    f, _ = _fuente(tmp_path, calendario={date(2026, 8, 29): date(2026, 9, 10)})
    f.calendario = {date(2026, 8, 29): date(2026, 9, 10)}
    fila = f.columnas("A", datetime(2026, 9, 12, 14, 0, tzinfo=UTC))
    assert fila["short_interest_corte"] == "2026-08-31"   # el corte real del 31/8 sigue siendo el visible
    assert f.reporte_del_corte(date(2026, 8, 29)) is None


def test_corte_caido_es_faltante_y_la_cache_evita_repetir(tmp_path):
    caido = TransporteGrabado.clave(finra_short.URL, None, _cuerpo("2026-09-30"))
    f, t = _fuente(tmp_path, fallar={caido}, calendario={**CALENDARIO_PRUEBA, date(2026, 9, 30): date(2026, 10, 9)})
    fila = f.columnas("A", datetime(2026, 10, 20, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    f.columnas("A", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    n = len(t.pedidos)
    g = finra_short.ShortInterestFinra(f.cache, f.cliente(), None, f.calendario)
    g.columnas("AA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n
