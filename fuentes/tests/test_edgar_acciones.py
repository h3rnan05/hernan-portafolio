"""Acciones en circulación EDGAR: dato vigente por fecha de presentación y regla del 20 %."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, edgar, edgar_acciones
from fuentes.cache import Cache
from fuentes.grabar import transporte_desde
from fuentes.tests.test_edgar import DESDE, TODAS


def _fuente(tmp_path, monkeypatch, yahoo=None):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", TODAS + ["acciones_NTLA", "acciones_TSLA_404"])
    lector = edgar.LectorEdgar(Cache(tmp_path / "c"), edgar.cliente_edgar(transport=t, dormir=lambda s: None), DESDE)
    return edgar_acciones.EdgarAcciones(lector, yahoo), t


def test_dato_vigente_es_el_ultimo_presentado_no_el_ultimo_medido():
    d = [edgar_acciones.Dato(40e6, date(2026, 2, 20), date(2026, 3, 2), "10-K"),
         edgar_acciones.Dato(52e6, date(2026, 7, 31), date(2026, 8, 6), "10-Q")]
    assert edgar_acciones.dato_vigente(d, date(2026, 8, 5)).valor == 40e6
    assert edgar_acciones.dato_vigente(d, date(2026, 8, 6)).valor == 52e6
    assert edgar_acciones.dato_vigente(d, date(2026, 3, 1)) is None


def test_leer_concepto_omite_entradas_incompletas_y_rechaza_cuerpo_ajeno():
    assert edgar_acciones.leer_concepto({"units": {"shares": [{"val": 5, "end": "2026-01-01"}]}}) == []
    with pytest.raises(ErrorFuente, match="cuerpo"):
        edgar_acciones.leer_concepto({"units": {}})
    with pytest.raises(ErrorFuente, match="cuerpo"):
        edgar_acciones.leer_concepto([])


def test_columnas_con_yahoo_y_regla_del_20(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch, yahoo=lambda t: 103_750_000)
    fila = f.columnas("NTLA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["edgar_acciones"] == 130_000_000 and fila["edgar_acciones_fecha"] == "2026-07-31"
    assert fila["edgar_acciones_presentado"] == "2026-08-06" and fila["edgar_acciones_dias"] == 56
    assert fila["acciones_yahoo"] == 103_750_000 and fila["acciones_discrepancia_pct"] == 0.253
    assert fila["acciones_discrepancia_20"] is True
    # Antes del 10-Q de agosto, el vigente era el de mayo y la discrepancia es chica.
    fila = f.columnas("NTLA", datetime(2026, 7, 1, 14, 0, tzinfo=UTC))
    assert fila["edgar_acciones"] == 102_500_000 and fila["acciones_discrepancia_20"] is False


def test_sin_yahoo_las_columnas_de_comparacion_son_faltante_y_no_se_sustituye(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NTLA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["edgar_acciones"] == 130_000_000
    assert fila["acciones_yahoo"] is FALTANTE and fila["acciones_discrepancia_20"] is FALTANTE
    f2, _ = _fuente(tmp_path, monkeypatch, yahoo=lambda t: FALTANTE)
    assert f2.columnas("NTLA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))["acciones_discrepancia_pct"] is FALTANTE
    f3, _ = _fuente(tmp_path, monkeypatch, yahoo=lambda t: 0)
    assert f3.columnas("NTLA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))["acciones_discrepancia_pct"] is FALTANTE


def test_emisor_sin_concepto_es_faltante_no_cero(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch, yahoo=lambda t: 10e6)
    fila = f.columnas("TSLA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values()) and set(fila) == set(f.nombres())
    n = len(t.pedidos)
    f.columnas("TSLA", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n   # el 404 se recuerda en el proceso


def test_antes_de_la_primera_presentacion_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    assert f.columnas("NTLA", datetime(2026, 1, 15, 14, 0, tzinfo=UTC))["edgar_acciones"] is FALTANTE


def test_las_cuatro_columnas_edgar_conviven_en_un_registro(tmp_path, monkeypatch):
    from fuentes.columnas import Registro
    from fuentes.edgar_form4 import EdgarForm4
    from fuentes.edgar_veto import EdgarVeto
    f, _ = _fuente(tmp_path, monkeypatch)
    reg = Registro([edgar.Edgar8K(lector=f.lector), EdgarVeto(f.lector), EdgarForm4(f.lector), f])
    assert len(reg.nombres()) == 4 + 5 + 5 + 7
