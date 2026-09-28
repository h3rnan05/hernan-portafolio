"""Form 4: solo P y S cuentan, ventana de 30 días, XML fail-closed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, edgar, edgar_form4
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde
from fuentes.tests.test_edgar import TODAS

XMLS = ["form4_FICA_000001", "form4_FICA_000003", "form4_roto"]


def _fuente(tmp_path, monkeypatch, fallar=None):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", TODAS + XMLS, fallar)
    lector = edgar.LectorEdgar(Cache(tmp_path / "c"), edgar.cliente_edgar(transport=t, dormir=lambda s: None))
    return edgar_form4.EdgarForm4(lector), t


def test_leer_form4_suma_solo_p_y_s():
    ops = edgar_form4.leer_form4(cargar("edgar", "form4_FICA_000001")["texto"])
    assert ops == edgar_form4.Operaciones(compras=12500.0, ventas=0.0)
    ops = edgar_form4.leer_form4(cargar("edgar", "form4_FICA_000003")["texto"])
    assert ops == edgar_form4.Operaciones(compras=0.0, ventas=3000.0)


def test_cantidad_ilegible_no_es_cero():
    with pytest.raises(ErrorFuente, match="cantidad_ilegible"):
        edgar_form4.leer_form4(cargar("edgar", "form4_roto")["texto"])
    with pytest.raises(ErrorFuente, match="xml_ilegible"):
        edgar_form4.leer_form4("<html>no</html>")
    with pytest.raises(ErrorFuente, match="xml_ilegible"):
        edgar_form4.leer_form4("<<<")


def test_url_del_xml_quita_el_prefijo_xsl():
    p = edgar.Presentacion("4", datetime(2026, 9, 25).date(), None, (), "0001900001-26-000001", "xslF345X05/form4.xml")
    assert edgar_form4.url_xml(1900001, p) == "https://www.sec.gov/Archives/edgar/data/1900001/000190000126000001/form4.xml"
    assert edgar_form4.url_xml(1, edgar.Presentacion("4", p.fecha, None, (), "", "")) is None
    assert edgar_form4.url_xml(1, edgar.Presentacion("4", p.fecha, None, (), "x", "doc.htm")) is None


def test_columnas_con_dos_form4_en_30_dias(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert fila == {"form4_cantidad_30d": 2, "form4_compras_acciones": 12500.0, "form4_ventas_acciones": 3000.0,
                    "form4_neto": "mixto", "form4_ultimo_horas": 16.96}
    # Solo el del 22/9 existía el 24/9: neto venta.
    fila = f.columnas("FICA", datetime(2026, 9, 24, 14, 0, tzinfo=UTC))
    assert fila["form4_cantidad_30d"] == 1 and fila["form4_neto"] == "venta"
    # Los XML se cachean: una segunda pasada no vuelve a pedirlos.
    n = len(t.pedidos)
    f.columnas("FICA", datetime(2026, 9, 26, 15, 0, tzinfo=UTC))
    assert len(t.pedidos) == n


def test_sin_form4_en_la_ventana_es_cero_afirmado(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", datetime(2026, 8, 1, 14, 0, tzinfo=UTC))
    assert fila == {"form4_cantidad_30d": 0, "form4_compras_acciones": 0.0, "form4_ventas_acciones": 0.0,
                    "form4_neto": None, "form4_ultimo_horas": None}


def test_xml_caido_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch,
                   fallar={"https://www.sec.gov/Archives/edgar/data/1900001/000190000126000003/form4b.xml"})
    fila = f.columnas("FICA", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_sin_indice_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NOEXISTE", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values()) and set(fila) == set(f.nombres())
