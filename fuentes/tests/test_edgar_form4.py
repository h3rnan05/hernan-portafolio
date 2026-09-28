"""Form 4: solo P y S cuentan, ventana de 30 días, XML fail-closed.

El índice de Form 4 es el real de NTLA (submissions grabado en el VPS);
el XML de cada uno es ficticio con la accession real (no se grabó)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, edgar, edgar_form4
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde
from fuentes.tests.test_edgar import DESDE, TODAS

XMLS = ["form4_NTLA_363567", "form4_NTLA_316279", "form4_NTLA_2019493", "form4_NTLA_1821530", "form4_roto"]
URL_316279 = "https://www.sec.gov/Archives/edgar/data/1652130/000119312526316279/ownership.xml"


def _fuente(tmp_path, monkeypatch, fallar=None):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", TODAS + XMLS, fallar)
    lector = edgar.LectorEdgar(Cache(tmp_path / "c"), edgar.cliente_edgar(transport=t, dormir=lambda s: None), DESDE)
    return edgar_form4.EdgarForm4(lector), t


def test_leer_form4_suma_solo_p_y_s():
    assert edgar_form4.leer_form4(cargar("edgar", "form4_NTLA_363567")["texto"]) == edgar_form4.Operaciones(12500.0, 0.0)
    assert edgar_form4.leer_form4(cargar("edgar", "form4_NTLA_316279")["texto"]) == edgar_form4.Operaciones(0.0, 3000.0)


def test_cantidad_ilegible_no_es_cero():
    with pytest.raises(ErrorFuente, match="cantidad_ilegible"):
        edgar_form4.leer_form4(cargar("edgar", "form4_roto")["texto"])
    with pytest.raises(ErrorFuente, match="xml_ilegible"):
        edgar_form4.leer_form4("<html>no</html>")
    with pytest.raises(ErrorFuente, match="xml_ilegible"):
        edgar_form4.leer_form4("<<<")


def test_url_del_xml_quita_el_prefijo_xsl():
    p = edgar.Presentacion("4", datetime(2026, 8, 24).date(), None, (), "0001193125-26-363567", "xslF345X06/ownership.xml")
    assert edgar_form4.url_xml(1652130, p) == "https://www.sec.gov/Archives/edgar/data/1652130/000119312526363567/ownership.xml"
    assert edgar_form4.url_xml(1, edgar.Presentacion("4", p.fecha, None, (), "", "")) is None
    assert edgar_form4.url_xml(1, edgar.Presentacion("4", p.fecha, None, (), "x", "doc.htm")) is None


def test_un_form4_en_30_dias(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    # NTLA: Form 4 aceptado 2026-08-24 20:05:04 UTC; el 25/8 a las 14:00 UTC lleva 17,92 h.
    fila = f.columnas("NTLA", datetime(2026, 8, 25, 14, 0, tzinfo=UTC))
    assert fila == {"form4_cantidad_30d": 1, "form4_compras_acciones": 12500.0, "form4_ventas_acciones": 0.0,
                    "form4_neto": "compra", "form4_ultimo_horas": 17.92}
    # A las 20:00 UTC del 24/8 todavía no existía.
    assert f.columnas("NTLA", datetime(2026, 8, 24, 20, 0, tzinfo=UTC))["form4_cantidad_30d"] == 0
    n = len(t.pedidos)
    f.columnas("NTLA", datetime(2026, 8, 26, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n   # el XML se cachea


def test_tres_form4_dan_mixto_y_dos_dan_compra(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    # 25/7 14:00 UTC: 24/7 (venta 3000) + dos del 6/7 (compras 1000 y 500).
    fila = f.columnas("NTLA", datetime(2026, 7, 25, 14, 0, tzinfo=UTC))
    assert fila["form4_cantidad_30d"] == 3 and fila["form4_compras_acciones"] == 1500.0
    assert fila["form4_ventas_acciones"] == 3000.0 and fila["form4_neto"] == "mixto"
    fila = f.columnas("NTLA", datetime(2026, 7, 15, 14, 0, tzinfo=UTC))
    assert fila["form4_cantidad_30d"] == 2 and fila["form4_neto"] == "compra"


def test_sin_form4_en_la_ventana_es_cero_afirmado(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NTLA", datetime(2026, 5, 30, 14, 0, tzinfo=UTC))
    assert fila == {"form4_cantidad_30d": 0, "form4_compras_acciones": 0.0, "form4_ventas_acciones": 0.0,
                    "form4_neto": None, "form4_ultimo_horas": None}


def test_xml_caido_o_roto_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch, fallar={URL_316279})
    assert all(v is FALTANTE for v in f.columnas("NTLA", datetime(2026, 7, 25, 14, 0, tzinfo=UTC)).values())
    g, t = _fuente(tmp_path / "otro", monkeypatch)
    t.registros[URL_316279] = cargar("edgar", "form4_roto")
    assert all(v is FALTANTE for v in g.columnas("NTLA", datetime(2026, 7, 25, 14, 0, tzinfo=UTC)).values())


def test_sin_indice_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NOEXISTE", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values()) and set(fila) == set(f.nombres())
