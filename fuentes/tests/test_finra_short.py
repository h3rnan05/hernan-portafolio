"""FINRA short interest: cortes quincenales, fecha de publicación (no de corte), CSV fail-closed."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, finra_short
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde


def _fuente(tmp_path, float_yahoo=None, calendario=None, fallar=None):
    t = transporte_desde("finra_short", ["corte_20260915", "corte_20260831"], fallar)
    c = finra_short.cliente_finra(transport=t, transport_post=t, dormir=lambda s: None)
    cal = calendario if calendario is not None else finra_short.cargar_calendario()
    return finra_short.ShortInterestFinra(Cache(tmp_path / "c"), c, float_yahoo, cal), t


def test_cortes_quincenales_corridos_al_dia_habil_previo():
    cortes = finra_short.cortes_entre(date(2026, 8, 1), date(2026, 9, 30))
    # 15/8/2026 es sábado -> viernes 14; 31/8 lunes; 15/9 martes; 30/9 miércoles.
    assert cortes == [date(2026, 8, 14), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30)]


def test_publicacion_del_calendario_o_estimada():
    cal = finra_short.cargar_calendario()
    assert cal == {date(2026, 8, 31): date(2026, 9, 14)}
    assert finra_short.publicacion_de(date(2026, 8, 31), cal) == date(2026, 9, 14)
    assert finra_short.publicacion_de(date(2026, 9, 15), cal) == date(2026, 9, 25)   # + 8 hábiles
    assert finra_short.publicacion_de(date(2026, 9, 15), {}) == date(2026, 9, 25)


def test_leer_csv_descarta_filas_sin_posicion_y_rechaza_otro_formato():
    filas = finra_short.leer_csv(cargar("finra_short", "corte_20260915")["texto"], date(2026, 9, 15))
    assert filas == {"FICA": (2400000.0, 4.0, 20.0), "FICB": (150000.0, 3.0, -6.25)}
    with pytest.raises(ErrorFuente, match="cuerpo"):
        finra_short.leer_csv("a,b\n1,2\n", date(2026, 9, 15))


def test_el_reporte_visible_es_el_publicado_no_el_de_corte(tmp_path):
    f, t = _fuente(tmp_path)
    # 20/9: el corte del 15/9 existe pero se publica el 25/9. Visible: el del 31/8 (publicado el 14/9).
    fila = f.columnas("FICA", datetime(2026, 9, 20, 14, 0, tzinfo=UTC))
    assert fila["short_interest_corte"] == "2026-08-31" and fila["short_interest_publicado"] == "2026-09-14"
    assert fila["short_interest_acciones"] == 2000000.0 and fila["short_interest_dias"] == 6
    # 25/9 a las 14:00 NY (18:00 UTC): todavía no (se publica a las 16:00 NY).
    fila = f.columnas("FICA", datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    assert fila["short_interest_corte"] == "2026-08-31"
    # 28/9: ya se ve el del 15/9.
    fila = f.columnas("FICA", datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    assert fila["short_interest_corte"] == "2026-09-15" and fila["short_interest_acciones"] == 2400000.0
    assert fila["short_interest_dtc"] == 4.0 and fila["short_interest_cambio_pct"] == 20.0
    assert fila["short_interest_pct_float"] is FALTANTE   # sin float de Yahoo
    assert all(p[1].get("json") for p in t.pedidos)   # se pidió por POST con filtro


def test_pct_float_con_yahoo(tmp_path):
    f, _ = _fuente(tmp_path, float_yahoo=lambda t: 12_000_000)
    fila = f.columnas("FICA", datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    assert fila["short_interest_pct_float"] == 0.2
    f2, _ = _fuente(tmp_path, float_yahoo=lambda t: 0)
    assert f2.columnas("FICA", datetime(2026, 9, 28, 14, 0, tzinfo=UTC))["short_interest_pct_float"] is FALTANTE


def test_simbolo_ausente_del_reporte_es_none_con_fechas(tmp_path):
    f, _ = _fuente(tmp_path)
    fila = f.columnas("FICB", datetime(2026, 9, 20, 14, 0, tzinfo=UTC))
    assert fila["short_interest_acciones"] is None and fila["short_interest_corte"] == "2026-08-31"


def test_corte_caido_es_faltante_y_la_cache_evita_repetir(tmp_path):
    from fuentes.grabar import TransporteGrabado
    caido = TransporteGrabado.clave(finra_short.URL, None, {"limit": 5000, "offset": 0, "compareFilters": [
        {"fieldName": "settlementDate", "compareType": "EQUAL", "fieldValue": "2026-09-30"}]})
    f, t = _fuente(tmp_path, fallar={caido})
    # El corte del 30/9 (publicado ~12/10) se cae en la red.
    fila = f.columnas("FICA", datetime(2026, 10, 20, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    f.columnas("FICA", datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    n = len(t.pedidos)
    g = finra_short.ShortInterestFinra(f.cache, f.cliente(), None, f.calendario)
    g.columnas("FICB", datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n
