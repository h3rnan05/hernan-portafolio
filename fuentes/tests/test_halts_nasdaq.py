"""Halts Nasdaq Trader: T1 y LUDP por día, halt activo, 1 consulta/min, caché histórica."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, halts_nasdaq as hn
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde


def _fuente(tmp_path, fallar=None):
    t = transporte_desde("halts_nasdaq", ["halts_20260925", "halts_20260924"], fallar)
    dormidas = []
    c = hn.cliente_nasdaq(transport=t, dormir=dormidas.append)
    return hn.HaltsNasdaq(Cache(tmp_path / "c"), c, hoy=date(2026, 10, 1)), t, dormidas


def test_leer_rss_solo_el_dia_y_horas_en_ny():
    halts = hn.leer_rss(cargar("halts_nasdaq", "halts_20260925")["texto"], date(2026, 9, 25))
    assert [h.simbolo for h in halts] == ["FICA", "FICA", "FICB", "FICC"]
    assert halts[0].inicio == datetime(2026, 9, 25, 13, 41, 12, tzinfo=UTC)
    assert halts[0].reanudacion == datetime(2026, 9, 25, 13, 51, 12, tzinfo=UTC)
    assert halts[3].reanudacion is None
    assert hn.leer_rss(cargar("halts_nasdaq", "halts_20260924")["texto"], date(2026, 9, 24)) == []


def test_item_ilegible_invalida_el_dia():
    xml = cargar("halts_nasdaq", "halts_20260925")["texto"].replace("<ndaq:ReasonCode>LUDP</ndaq:ReasonCode>", "<ndaq:ReasonCode></ndaq:ReasonCode>", 1)
    with pytest.raises(ErrorFuente, match="item_ilegible"):
        hn.leer_rss(xml, date(2026, 9, 25))
    with pytest.raises(ErrorFuente, match="xml_ilegible"):
        hn.leer_rss("<html/>", date(2026, 9, 25))


def test_columnas_ludp_antes_y_despues(tmp_path):
    f, _, _ = _fuente(tmp_path)
    # 09:45 NY: primer LUDP en curso.
    fila = f.columnas("FICA", datetime(2026, 9, 25, 13, 45, tzinfo=UTC))
    assert fila == {"halts_t1_dia": 0, "halts_ludp_dia": 1, "halts_otros_dia": 0, "halt_activo": True,
                    "halt_ultimo_motivo": "LUDP", "halt_reanudado_hace_min": None}
    # 10:00 NY: reanudado hace 8,8 min.
    fila = f.columnas("FICA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["halt_activo"] is False and fila["halt_reanudado_hace_min"] == 8.8
    # 10:30 NY: dos LUDP, el último reanudado a las 10:07:30.
    fila = f.columnas("FICA", datetime(2026, 9, 25, 14, 30, tzinfo=UTC))
    assert fila["halts_ludp_dia"] == 2 and fila["halt_reanudado_hace_min"] == 22.5


def test_t1_sin_reanudacion_sigue_activo_y_t1_premarket_cuenta(tmp_path):
    f, _, _ = _fuente(tmp_path)
    fila = f.columnas("FICC", datetime(2026, 9, 25, 16, 0, tzinfo=UTC))
    assert fila["halts_t1_dia"] == 1 and fila["halt_activo"] is True
    assert f.columnas("FICC", datetime(2026, 9, 25, 15, 0, tzinfo=UTC))["halts_t1_dia"] == 0   # aún no empezó
    fila = f.columnas("FICB", datetime(2026, 9, 25, 14, 30, tzinfo=UTC))
    assert fila["halts_t1_dia"] == 1 and fila["halt_activo"] is False and fila["halt_reanudado_hace_min"] == 30.0


def test_dia_sin_halts_es_cero_afirmado(tmp_path):
    f, _, _ = _fuente(tmp_path)
    fila = f.columnas("FICA", datetime(2026, 9, 24, 14, 30, tzinfo=UTC))
    assert fila == {"halts_t1_dia": 0, "halts_ludp_dia": 0, "halts_otros_dia": 0, "halt_activo": False,
                    "halt_ultimo_motivo": None, "halt_reanudado_hace_min": None}


def test_dia_caido_es_faltante_y_precargar_lo_cuenta(tmp_path):
    from fuentes.grabar import TransporteGrabado
    caido = TransporteGrabado.clave(hn.URL, {"feed": "tradehalts", "haltdate": "09/23/2026"})
    f, _, _ = _fuente(tmp_path, fallar={caido})
    assert all(v is FALTANTE for v in f.columnas("FICA", datetime(2026, 9, 23, 14, 30, tzinfo=UTC)).values())
    assert f.precargar(date(2026, 9, 23), date(2026, 9, 25)) == 1


def test_un_pedido_por_minuto_y_cache_para_siempre(tmp_path):
    f, t, dormidas = _fuente(tmp_path)
    f.columnas("FICA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    f.columnas("FICA", datetime(2026, 9, 24, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == 2 and dormidas and dormidas[0] > 50
    g = hn.HaltsNasdaq(f.cache, f.cliente(), hoy=date(2026, 10, 1))
    g.columnas("FICB", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == 2
    reg = f.cache.leer("halts_nasdaq", f"{hn.URL}?feed=tradehalts&haltdate=2026-09-25")
    assert reg is not None
