"""Veto de dilución EDGAR: duraciones decididas, vencimiento, estantería, fail-closed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from fuentes import FALTANTE, edgar, edgar_veto
from fuentes.cache import Cache
from fuentes.grabar import transporte_desde
from fuentes.tests.test_edgar import TODAS


def _fuente(tmp_path, monkeypatch):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", TODAS)
    lector = edgar.LectorEdgar(Cache(tmp_path / "c"), edgar.cliente_edgar(transport=t, dormir=lambda s: None))
    return edgar_veto.EdgarVeto(lector)


def _p(form, fecha, items=(), aceptada=None):
    return edgar.Presentacion(form, fecha, aceptada, tuple(items), "", "")


@pytest.mark.parametrize("form, items, esperado", [
    ("424B5", (), edgar_veto.VETO_72H), ("424B4", (), edgar_veto.VETO_72H), ("424B3", (), edgar_veto.VETO_72H),
    ("8-K", ("3.02", "9.01"), edgar_veto.VETO_72H),
    ("S-1", (), edgar_veto.VETO_30D), ("S-1/A", (), edgar_veto.VETO_30D), ("F-1", (), edgar_veto.VETO_30D),
    ("F-1/A", (), edgar_veto.VETO_30D),
    ("8-K", ("1.03",), edgar_veto.VETO_30D), ("8-K", ("3.01",), edgar_veto.VETO_30D),
    ("8-K", ("4.02", "9.01"), edgar_veto.VETO_30D),
    ("8-K", ("3.02", "4.02"), edgar_veto.VETO_30D),   # el más largo manda
    ("S-3", (), None), ("F-3", (), None), ("S-3ASR", (), None),
    ("8-K", ("2.02",), None), ("10-Q", (), None), ("4", (), None),
])
def test_duraciones_decididas(form, items, esperado):
    d = edgar_veto.duracion_veto(_p(form, date(2026, 9, 1), items))
    assert (d[0] if d else None) == esperado


def test_veto_vence_a_las_72h_exactas():
    acc = datetime(2026, 8, 20, 13, 15, tzinfo=UTC)
    pres = [_p("424B5", date(2026, 8, 20), aceptada=acc)]
    assert edgar_veto.veto_en(pres, acc - timedelta(minutes=1)) == (False, None, None)
    assert edgar_veto.veto_en(pres, acc)[0] is True
    v, motivo, horas = edgar_veto.veto_en(pres, acc + timedelta(hours=71))
    assert v and motivo == "424B5 2026-08-20" and horas == 1.0
    assert edgar_veto.veto_en(pres, acc + timedelta(hours=72)) == (False, None, None)


def test_con_varios_vetos_gana_el_que_vence_mas_tarde():
    t0 = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    pres = [_p("S-1", date(2026, 6, 1), aceptada=t0), _p("424B4", date(2026, 6, 10), aceptada=t0 + timedelta(days=9))]
    v, motivo, _ = edgar_veto.veto_en(pres, t0 + timedelta(days=10))
    assert v and motivo == "S-1 2026-06-01"


def test_aceptacion_ilegible_cercana_es_faltante_y_lejana_no():
    pres = [_p("S-1", date(2026, 6, 1))]
    with pytest.raises(Exception, match="aceptacion_ilegible"):
        edgar_veto.veto_en(pres, datetime(2026, 6, 20, 14, 0, tzinfo=UTC))
    assert edgar_veto.veto_en(pres, datetime(2026, 9, 20, 14, 0, tzinfo=UTC)) == (False, None, None)


def test_estanteria_activa_3_anios_y_no_es_veto():
    acc = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    pres = [_p("S-3", date(2026, 3, 2), aceptada=acc)]
    assert edgar_veto.estanteria_en(pres, acc + timedelta(days=400)) == (True, "S-3 2026-03-02")
    assert edgar_veto.estanteria_en(pres, acc - timedelta(days=1)) == (False, None)
    assert edgar_veto.estanteria_en(pres, acc + timedelta(days=3 * 365 + 1)) == (False, None)
    assert edgar_veto.veto_en(pres, acc + timedelta(hours=1)) == (False, None, None)


def test_columnas_con_las_presentaciones_ficticias(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    # 8-K 3.02 aceptado 2026-09-15 07:30 NY (11:30 UTC): el 16 a las 14:00 UTC sigue vetado.
    fila = f.columnas("FICA", datetime(2026, 9, 16, 14, 0, tzinfo=UTC))
    assert fila["edgar_veto"] is True and fila["edgar_veto_motivo"] == "8-K 3.02 2026-09-15"
    assert fila["edgar_veto_horas_restantes"] == 45.5
    assert fila["edgar_estanteria_activa"] is True and fila["edgar_estanteria_form"] == "S-3 2026-03-02"
    # El 25/9 ya no hay veto vigente (424B5 del 20/8 y S-1/A del 15/6 vencieron).
    fila = f.columnas("FICA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["edgar_veto"] is False and fila["edgar_veto_motivo"] is None
    # El 20/6: S-1 (1/6) y S-1/A (15/6) vigentes; gana la enmienda (vence el 15/7).
    fila = f.columnas("FICA", datetime(2026, 6, 20, 14, 0, tzinfo=UTC))
    assert fila["edgar_veto"] is True and fila["edgar_veto_motivo"] == "S-1/A 2026-06-15"
    assert fila["edgar_estanteria_activa"] is True


def test_sin_presentaciones_es_faltante(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NOEXISTE", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    assert set(fila) == set(f.nombres())


def test_emisor_limpio_da_false_no_faltante(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICB", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila == {"edgar_veto": False, "edgar_veto_motivo": None, "edgar_veto_horas_restantes": None,
                    "edgar_estanteria_activa": False, "edgar_estanteria_form": None}


def test_el_lector_se_comparte_con_el_8k_sin_pedir_dos_veces(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    ochok = edgar.Edgar8K(lector=f.lector)
    from fuentes.columnas import Registro
    reg = Registro([ochok, f])
    fila = reg.columnas("FICA", datetime(2026, 9, 25, 13, 40, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] == 1 and fila["edgar_veto"] is False
    assert len(f.lector.cliente()._transport.pedidos) == 2   # tickers + submissions
