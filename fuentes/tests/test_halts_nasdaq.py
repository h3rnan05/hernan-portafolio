"""Halts Nasdaq Trader: haltdate MMDDYYYY, BOM, milisegundos, T1/LUDP por día, 1 consulta/min.

Fixture REAL del 25/9/2026 (64 ítems; VPS, 2026-09-28) y un canal vacío
sintético con BOM para el fail-closed."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, halts_nasdaq as hn
from fuentes.cache import Cache
from fuentes.grabar import TransporteGrabado, cargar, transporte_desde
from fuentes.http import Respuesta


def _fuente(tmp_path, fallar=None, hoy=date(2026, 10, 1)):
    t = transporte_desde("halts_nasdaq", ["halts_20260925", "halts_20260924_vacio_sintetico"], fallar)
    dormidas = []
    c = hn.cliente_nasdaq(transport=t, dormir=dormidas.append)
    return hn.HaltsNasdaq(Cache(tmp_path / "c"), c, hoy=hoy), t, dormidas


def test_el_parametro_va_sin_barras(tmp_path):
    f, t, _ = _fuente(tmp_path)
    f.columnas("JAGX", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert t.pedidos[0][1] == {"feed": "tradehalts", "haltdate": "09252026"}


def test_leer_rss_real_horas_con_milisegundos_y_relleno():
    halts = hn.leer_rss(cargar("halts_nasdaq", "halts_20260925")["texto"], date(2026, 9, 25))
    assert len(halts) == 64
    motivos = {m: sum(1 for h in halts if h.motivo == m) for m in ("LUDP", "M", "T3", "D")}
    assert motivos == {"LUDP": 41, "M": 12, "T3": 8, "D": 3}
    jagx = [h for h in halts if h.simbolo == "JAGX"]
    assert jagx[0].inicio == datetime(2026, 9, 25, 13, 32, 12, tzinfo=UTC)      # 09:32:12 .540 ET
    assert jagx[0].reanudacion == datetime(2026, 9, 25, 13, 37, 12, tzinfo=UTC)
    # T3 con reanudación el lunes siguiente.
    dlxy = next(h for h in halts if h.simbolo == "DLXY")
    assert dlxy.reanudacion == datetime(2026, 9, 28, 13, 0, tzinfo=UTC)


def test_bom_y_mojibake_no_rompen_el_xml():
    xml = cargar("halts_nasdaq", "halts_20260925")["texto"]
    assert hn.leer_rss("﻿" + xml, date(2026, 9, 25))
    assert hn.leer_rss("ï»¿" + xml, date(2026, 9, 25))
    r = Respuesta(200, {}, "ï»¿<?xml", "u", contenido=("﻿<?xml").encode("utf-8"))
    assert r.texto_utf8() == "<?xml"
    assert Respuesta(200, {}, "ï»¿<?xml", "u").texto_utf8() == "<?xml"


def test_canal_vacio_en_dia_habil_pasado_es_sospechoso():
    vacio = cargar("halts_nasdaq", "halts_20260924_vacio_sintetico")["texto"]
    with pytest.raises(ErrorFuente, match="sin_items"):
        hn.leer_rss(vacio, date(2026, 9, 24), hoy=date(2026, 10, 1))
    assert hn.leer_rss(vacio, date(2026, 9, 24), hoy=date(2026, 9, 24)) == []   # hoy: aún no hubo
    assert hn.leer_rss(vacio, date(2026, 9, 26), hoy=date(2026, 10, 1)) == []   # sábado
    assert hn.leer_rss(vacio, date(2026, 9, 24)) == []                          # sin `hoy` no se juzga


def test_item_ilegible_invalida_el_dia():
    xml = cargar("halts_nasdaq", "halts_20260925")["texto"].replace("<ndaq:ReasonCode>LUDP</ndaq:ReasonCode>", "<ndaq:ReasonCode></ndaq:ReasonCode>", 1)
    with pytest.raises(ErrorFuente, match="item_ilegible"):
        hn.leer_rss(xml, date(2026, 9, 25))
    with pytest.raises(ErrorFuente, match="xml_ilegible"):
        hn.leer_rss("<html/>", date(2026, 9, 25))


def test_columnas_ludp_de_jagx(tmp_path):
    f, _, _ = _fuente(tmp_path)
    # 10:00 ET: dos LUDP empezados (09:32:12 y 09:58:44); el segundo sigue detenido hasta 10:03:44.
    fila = f.columnas("JAGX", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila == {"halts_t1_dia": 0, "halts_ludp_dia": 2, "halts_otros_dia": 0, "halt_activo": True,
                    "halt_ultimo_motivo": "LUDP", "halt_reanudado_hace_min": 22.8}
    # 10:20 ET: tres LUDP, reanudado a las 10:11:42 (hace 8,3 min).
    fila = f.columnas("JAGX", datetime(2026, 9, 25, 14, 20, tzinfo=UTC))
    assert fila["halts_ludp_dia"] == 3 and fila["halt_activo"] is False and fila["halt_reanudado_hace_min"] == 8.3
    # A las 09:30 ET todavía nada.
    assert f.columnas("JAGX", datetime(2026, 9, 25, 13, 30, tzinfo=UTC))["halts_ludp_dia"] == 0


def test_t3_cuenta_como_otro_y_sigue_activo_hasta_reanudar(tmp_path):
    f, _, _ = _fuente(tmp_path)
    # WYY: cinco pausas M (09:51–10:30 ET) y un T3 a las 11:15:22 ET que reanuda a las 13:05:00 ET.
    fila = f.columnas("WYY", datetime(2026, 9, 25, 16, 0, tzinfo=UTC))
    assert fila["halts_otros_dia"] == 6 and fila["halts_t1_dia"] == 0 and fila["halts_ludp_dia"] == 0
    assert fila["halt_activo"] is True
    assert fila["halt_ultimo_motivo"] == "T3"
    fila = f.columnas("WYY", datetime(2026, 9, 25, 17, 30, tzinfo=UTC))
    assert fila["halt_activo"] is False and fila["halt_reanudado_hace_min"] == 25.0


def test_simbolo_sin_halts_ese_dia_es_cero_afirmado(tmp_path):
    f, _, _ = _fuente(tmp_path)
    fila = f.columnas("AAPL", datetime(2026, 9, 25, 14, 30, tzinfo=UTC))
    assert fila == {"halts_t1_dia": 0, "halts_ludp_dia": 0, "halts_otros_dia": 0, "halt_activo": False,
                    "halt_ultimo_motivo": None, "halt_reanudado_hace_min": None}


def test_dia_pasado_vacio_es_faltante_y_hoy_vacio_es_cero(tmp_path):
    f, _, _ = _fuente(tmp_path)
    assert all(v is FALTANTE for v in f.columnas("JAGX", datetime(2026, 9, 24, 14, 30, tzinfo=UTC)).values())
    g, _, _ = _fuente(tmp_path / "hoy", hoy=date(2026, 9, 24))
    assert g.columnas("JAGX", datetime(2026, 9, 24, 14, 30, tzinfo=UTC))["halts_ludp_dia"] == 0


def test_dia_caido_es_faltante_y_precargar_lo_cuenta(tmp_path):
    caido = TransporteGrabado.clave(hn.URL, {"feed": "tradehalts", "haltdate": "09232026"})
    f, _, _ = _fuente(tmp_path, fallar={caido})
    assert all(v is FALTANTE for v in f.columnas("JAGX", datetime(2026, 9, 23, 14, 30, tzinfo=UTC)).values())
    assert f.precargar(date(2026, 9, 23), date(2026, 9, 25)) == 2   # el 23 caído y el 24 vacío


def test_un_pedido_por_minuto_y_cache_para_siempre(tmp_path):
    f, t, dormidas = _fuente(tmp_path)
    f.columnas("JAGX", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    f.columnas("JAGX", datetime(2026, 9, 24, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == 2 and dormidas and dormidas[0] > 50
    g = hn.HaltsNasdaq(f.cache, f.cliente(), hoy=date(2026, 10, 1))
    g.columnas("WYY", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == 2
    assert f.cache.leer("halts_nasdaq", f"{hn.URL}?feed=tradehalts&haltdate=2026-09-25") is not None
