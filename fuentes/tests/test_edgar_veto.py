"""Veto de dilución EDGAR: duraciones decididas, vencimiento, estantería, fail-closed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from fuentes import FALTANTE, edgar, edgar_veto
from fuentes.cache import Cache
from fuentes.grabar import transporte_desde
from fuentes.tests.test_edgar import DESDE, TODAS


def _fuente(tmp_path, monkeypatch):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", TODAS)
    lector = edgar.LectorEdgar(Cache(tmp_path / "c"), edgar.cliente_edgar(transport=t, dormir=lambda s: None), DESDE)
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


def test_columnas_con_presentaciones_reales(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    # MRNA: 8-K con ítem 3.02 aceptado 2026-09-01 20:37:04 UTC -> 72 h de veto. El 2/9 a las 14:00 UTC sigue.
    fila = f.columnas("MRNA", datetime(2026, 9, 2, 14, 0, tzinfo=UTC))
    assert fila["edgar_veto"] is True and fila["edgar_veto_motivo"] == "8-K 3.02 2026-09-01"
    assert fila["edgar_veto_horas_restantes"] == 54.62
    # A las 21:00 UTC del 1/9 ya estaba vetado (la lectura NY lo habría dejado pasar hasta las 00:37).
    assert f.columnas("MRNA", datetime(2026, 9, 1, 21, 0, tzinfo=UTC))["edgar_veto"] is True
    assert f.columnas("MRNA", datetime(2026, 9, 1, 20, 0, tzinfo=UTC))["edgar_veto"] is False
    # El 5/9 venció.
    assert f.columnas("MRNA", datetime(2026, 9, 5, 14, 0, tzinfo=UTC))["edgar_veto"] is False
    # NTLA: dos 424B5 (27/4 21:19 UTC y 29/4 21:28 UTC); el 30/4 gana el que vence más tarde.
    fila = f.columnas("NTLA", datetime(2026, 4, 30, 14, 0, tzinfo=UTC))
    assert fila["edgar_veto"] is True and fila["edgar_veto_motivo"] == "424B5 2026-04-29"
    assert fila["edgar_estanteria_activa"] is False   # NTLA no tiene S-3 en las 150 recientes
    # AAPL: S-3ASR del 2024-11-01 sigue vigente (3 años); sin veto.
    fila = f.columnas("AAPL", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["edgar_veto"] is False and fila["edgar_veto_motivo"] is None
    assert fila["edgar_estanteria_activa"] is True and fila["edgar_estanteria_form"] == "S-3ASR 2024-11-01"


def test_sin_presentaciones_es_faltante(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NOEXISTE", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    assert set(fila) == set(f.nombres())


def test_emisor_limpio_da_false_no_faltante(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("TSLA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila == {"edgar_veto": False, "edgar_veto_motivo": None, "edgar_veto_horas_restantes": None,
                    "edgar_estanteria_activa": False, "edgar_estanteria_form": None}


def test_el_lector_se_comparte_con_el_8k_sin_pedir_dos_veces(tmp_path, monkeypatch):
    f = _fuente(tmp_path, monkeypatch)
    ochok = edgar.Edgar8K(lector=f.lector)
    from fuentes.columnas import Registro
    reg = Registro([ochok, f])
    fila = reg.columnas("AAPL", datetime(2026, 7, 30, 21, 0, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] == 1 and fila["edgar_veto"] is False
    assert len(f.lector.cliente()._transport.pedidos) == 2   # tickers + submissions
