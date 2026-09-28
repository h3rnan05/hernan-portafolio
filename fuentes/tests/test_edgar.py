"""EDGAR 8-K: mapa ítem→nivel, zona horaria NY, ventana de 24 h, fail-closed."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, edgar
from fuentes.cache import Cache
from fuentes.grabar import transporte_desde

TODAS = ["company_tickers", "submissions_FICA", "submissions_FICB", "submissions_FICB_001", "submissions_FICC"]


def _fuente(tmp_path, monkeypatch, nombres=TODAS, fallar=None):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", nombres, fallar)
    cliente = edgar.cliente_edgar(transport=t, dormir=lambda s: None)
    return edgar.Edgar8K(Cache(tmp_path / "c"), cliente), t


# ------------------------------------------------------------ mapa ítem→nivel


@pytest.mark.parametrize("items, nivel", [
    (("2.02", "9.01"), 1), (("1.01",), 1), (("8.01",), 2), (("7.01", "9.01"), 2), (("2.01",), 2),
    (("5.02",), None), (("5.03", "5.07"), None), (("9.01",), None), ((), None),
    (("8.01", "2.02"), 1),   # el mejor nivel manda
    (("3.02", "9.01"), None),  # el ítem de veto no es un catalizador
])
def test_nivel_por_item(items, nivel):
    assert edgar.nivel_de_items(items) == nivel


def test_el_mapa_es_el_decidido_y_no_asigna_direccion():
    assert edgar.NIVEL_POR_ITEM == {"2.02": 1, "1.01": 1, "8.01": 2, "7.01": 2, "2.01": 2}
    assert edgar.ITEMS_SIN_NIVEL == {"5.02", "5.03", "5.07", "9.01"}
    assert "direccion" not in edgar.Edgar8K._NOMBRES and not any("alcista" in n for n in edgar.Edgar8K._NOMBRES)


# ------------------------------------------------------------- zona horaria


def test_la_aceptacion_con_z_se_lee_como_nueva_york():
    # 16:05 NY en verano = 20:05 UTC.
    assert edgar.leer_aceptacion("2026-09-25T16:05:12.000Z") == datetime(2026, 9, 25, 20, 5, 12, tzinfo=UTC)
    # En invierno, +5.
    assert edgar.leer_aceptacion("2026-02-11T21:00:00.000Z") == datetime(2026, 2, 12, 2, 0, tzinfo=UTC)
    assert edgar.leer_aceptacion("") is None and edgar.leer_aceptacion("basura") is None


def test_verificar_zona_con_las_presentaciones_ficticias():
    import json
    from fuentes.grabar import cargar
    recent = json.loads(cargar("edgar", "submissions_FICA")["texto"])["filings"]["recent"]
    pres = edgar._presentaciones_de(recent)
    veredicto, detalle = edgar.verificar_zona(pres, recent["acceptanceDateTime"])
    assert veredicto == "ZONA OK" and detalle == {"consistentes_ny": 3, "consistentes_utc": 0}


def test_verificar_zona_detecta_una_lectura_utc():
    # 18:10 con filingDate del MISMO día solo cuadra si la hora es UTC.
    p = edgar.Presentacion("8-K", date(2026, 9, 25), None, ("8.01",), "", "")
    assert edgar.verificar_zona([p], ["2026-09-25T18:10:00.000Z"])[0] == "ZONA A REVISAR"
    assert edgar.verificar_zona([], [])[0] == "ZONA A REVISAR"   # sin evidencia no hay OK


# ------------------------------------------------------------------ columnas


def test_8k_de_resultados_dentro_de_24h_da_nivel_1(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    # Señal el 25/9 a las 09:40 NY (13:40 UTC): el 8-K 2.02 del 24 a las 16:05 NY está a 17,6 h.
    fila = f.columnas("FICA", datetime(2026, 9, 25, 13, 40, tzinfo=UTC))
    assert fila == {"edgar_8k_nivel": 1, "edgar_8k_items": "2.02,9.01", "edgar_8k_horas": 17.58,
                    "edgar_8k_cantidad_24h": 1}


def test_nada_aceptado_despues_del_instante_entra(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    # El 8-K 8.01 se aceptó el 25 a las 18:10 NY (22:10 UTC). A las 21:00 UTC todavía no existe.
    antes = f.columnas("FICA", datetime(2026, 9, 25, 21, 0, tzinfo=UTC))
    assert antes["edgar_8k_cantidad_24h"] == 0 and antes["edgar_8k_nivel"] is None
    despues = f.columnas("FICA", datetime(2026, 9, 25, 22, 30, tzinfo=UTC))
    assert despues["edgar_8k_nivel"] == 2 and despues["edgar_8k_items"] == "8.01"


def test_8k_sin_nivel_da_none_pero_cuenta(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", datetime(2026, 9, 10, 15, 0, tzinfo=UTC))
    assert fila == {"edgar_8k_nivel": None, "edgar_8k_items": "5.02", "edgar_8k_horas": 3.0,
                    "edgar_8k_cantidad_24h": 1}


def test_sin_8k_en_la_ventana_es_none_no_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICB", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] is None and fila["edgar_8k_cantidad_24h"] == 0
    assert FALTANTE not in fila.values()


def test_los_archivos_extra_de_submissions_se_leen(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICB", datetime(2025, 5, 7, 14, 0, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] == 1
    assert any("submissions-001" in url for url, _ in t.pedidos)


def test_ticker_fuera_del_mapa_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NOEXISTE", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_aceptacion_ilegible_en_la_vecindad_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICC", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    # Fuera de la vecindad la ilegible no molesta (FICB tiene una de 2025-11).
    assert f.columnas("FICB", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))["edgar_8k_cantidad_24h"] == 0


def test_descarga_caida_es_faltante_y_no_se_reintenta_en_el_proceso(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch, fallar={"https://data.sec.gov/submissions/CIK0001900001.json"})
    fila = f.columnas("FICA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    n = len(t.pedidos)
    f.columnas("FICA", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n
    assert f.cache.leer("edgar_submissions", "https://data.sec.gov/submissions/CIK0001900001.json") is None


def test_sin_user_agent_no_se_pide_nada(tmp_path):
    with pytest.raises(ErrorFuente, match="sin_user_agent"):
        edgar.cliente_edgar()
    f = edgar.Edgar8K(Cache(tmp_path / "c"))
    fila = f.columnas("FICA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_user_agent_sin_contacto_no_vale(monkeypatch):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "solo-un-nombre")
    with pytest.raises(ErrorFuente, match="sin_user_agent"):
        edgar.user_agent_configurado()


def test_la_cache_evita_repetir_y_el_limitador_existe(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    f.columnas("FICA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    n = len(t.pedidos)
    g = edgar.Edgar8K(f.cache, f.cliente())
    g.columnas("FICA", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n
    assert f.cliente().limitador is not None and f.cliente().limitador.llamadas <= 10


def test_cuerpo_sin_filings_es_faltante(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    t.registros["https://data.sec.gov/submissions/CIK0001900001.json"] = {"status": 200, "headers": {}, "texto": "{}"}
    fila = f.columnas("FICA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_el_comando_grabar_esta_registrado():
    from fuentes import __main__ as cli
    cli._cargar_comandos()
    assert "edgar" in cli.COMANDOS
