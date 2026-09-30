"""EDGAR 8-K: mapa ítem→nivel, zona horaria (Z = UTC), ventana de 24 h, fail-closed.

Respuestas REALES de sec.gov grabadas en el VPS el 2026-09-28 (recortadas
a las 150 presentaciones más recientes por emisor): NTLA, AAPL, TSLA y
MRNA, más una muestra de `company_tickers.json`."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, edgar
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde

TODAS = ["company_tickers", "submissions_NTLA", "submissions_AAPL", "submissions_TSLA", "submissions_MRNA"]
DESDE = date(2025, 9, 1)     # las presentaciones extra de AAPL/TSLA/MRNA terminan antes: no se piden


def _fuente(tmp_path, monkeypatch, nombres=TODAS, fallar=None, desde=DESDE):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", nombres, fallar)
    cliente = edgar.cliente_edgar(transport=t, dormir=lambda s: None)
    return edgar.Edgar8K(lector=edgar.LectorEdgar(Cache(tmp_path / "c"), cliente, desde)), t


def _recent(nombre: str) -> dict:
    return json.loads(cargar("edgar", nombre)["texto"])["filings"]["recent"]


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


def test_la_z_de_la_aceptacion_es_utc():
    # AAPL 8-K 2.02 0000320193-26-000018: el índice de sec.gov dice Accepted 2026-07-30 16:30:28 ET.
    assert edgar.leer_aceptacion("2026-07-30T20:30:28.000Z") == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    assert edgar.leer_aceptacion("2026-07-30T16:30:28") is None     # sin zona no se adivina
    assert edgar.leer_aceptacion("") is None and edgar.leer_aceptacion("basura") is None


@pytest.mark.parametrize("nombre", ["submissions_NTLA", "submissions_AAPL", "submissions_TSLA", "submissions_MRNA"])
def test_verificar_zona_con_presentaciones_reales_da_zona_ok(nombre):
    veredicto, detalle = edgar.verificar_zona(_recent(nombre))
    assert veredicto == "ZONA OK", detalle
    assert detalle["discriminantes"] > 20 and detalle["consistentes_utc"] > detalle["consistentes_ny"]


def test_verificar_zona_detecta_una_lectura_ny_y_no_se_desalinea():
    # Si la hora fuera NY, un 18:10 se fecharía al día siguiente; como UTC (14:10 ET) el mismo día.
    recent = {"filingDate": ["2026-09-28", "no-fecha", "2026-09-25"], "acceptanceDateTime":
              ["2026-09-25T18:10:00.000Z", "2026-09-25T18:10:00.000Z", "2026-09-25T18:10:00.000Z"]}
    veredicto, detalle = edgar.verificar_zona(recent)
    assert veredicto == "ZONA A REVISAR" and detalle == {"discriminantes": 2, "consistentes_utc": 1, "consistentes_ny": 1}
    assert edgar.verificar_zona({"filingDate": [], "acceptanceDateTime": []})[0] == "ZONA A REVISAR"


def test_fecha_esperada_salta_el_fin_de_semana():
    viernes_tarde = datetime(2026, 9, 25, 18, 0, tzinfo=edgar.NY)
    assert edgar._fecha_presentacion_esperada(viernes_tarde) == date(2026, 9, 28)
    assert edgar._fecha_presentacion_esperada(datetime(2026, 9, 25, 17, 29, tzinfo=edgar.NY)) == date(2026, 9, 25)


# ------------------------------------------------------------------ columnas


def test_8k_de_resultados_de_apple_dentro_de_24h_da_nivel_1(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    # 8-K 2.02,9.01 aceptado 2026-07-30 20:30:28 UTC (16:30 ET). A las 21:00 UTC ya cuenta.
    fila = f.columnas("AAPL", datetime(2026, 7, 30, 21, 0, tzinfo=UTC))
    assert fila == {"edgar_8k_nivel": 1, "edgar_8k_items": "2.02,9.01", "edgar_8k_horas": 0.49,
                    "edgar_8k_cantidad_24h": 1}
    # A las 20:00 UTC todavía no existía.
    antes = f.columnas("AAPL", datetime(2026, 7, 30, 20, 0, tzinfo=UTC))
    assert antes["edgar_8k_cantidad_24h"] == 0 and antes["edgar_8k_nivel"] is None
    # El archivo extra de AAPL (hasta 2015) no se pidió.
    assert not any("submissions-001" in url for url, _ in t.pedidos)


def test_8k_nivel_2_de_ntla_y_ventana_de_24h(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    # 8-K 7.01,8.01,9.01 aceptado 2026-09-08 20:05:15 UTC. La mañana siguiente (09:40 NY) está a 17,58 h.
    fila = f.columnas("NTLA", datetime(2026, 9, 9, 13, 40, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] == 2 and fila["edgar_8k_items"] == "7.01,8.01,9.01" and fila["edgar_8k_horas"] == 17.58
    # 24 h después ya no está en la ventana.
    assert f.columnas("NTLA", datetime(2026, 9, 9, 20, 6, tzinfo=UTC))["edgar_8k_cantidad_24h"] == 0
    # 8-K 2.02 aceptado 2026-08-06 11:45:16 UTC (07:45 ET, antes de abrir): nivel 1 a la apertura.
    fila = f.columnas("NTLA", datetime(2026, 8, 6, 13, 40, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] == 1 and fila["edgar_8k_horas"] == 1.91


def test_8k_sin_nivel_da_none_pero_cuenta(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    # AAPL 8-K/A 5.02 aceptado 2026-09-01 20:30:35 UTC.
    fila = f.columnas("AAPL", datetime(2026, 9, 2, 14, 0, tzinfo=UTC))
    assert fila == {"edgar_8k_nivel": None, "edgar_8k_items": "5.02", "edgar_8k_horas": 17.49,
                    "edgar_8k_cantidad_24h": 1}


def test_con_varios_8k_gana_el_mejor_nivel(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    # MRNA: 8-K 1.01,2.03,3.02,8.01,9.01 el 2026-09-01 20:37 UTC -> nivel 1 por el 1.01 (el 3.02 es asunto del veto).
    fila = f.columnas("MRNA", datetime(2026, 9, 2, 14, 0, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] == 1 and fila["edgar_8k_items"] == "1.01,2.03,3.02,8.01,9.01"


def test_sin_8k_en_la_ventana_es_none_no_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("TSLA", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["edgar_8k_nivel"] is None and fila["edgar_8k_cantidad_24h"] == 0
    assert FALTANTE not in fila.values()


def test_ticker_fuera_del_mapa_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("NOEXISTE", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_aceptacion_ilegible_en_la_vecindad_es_faltante(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    reg = dict(cargar("edgar", "submissions_NTLA"))
    cuerpo = json.loads(reg["texto"])
    r = cuerpo["filings"]["recent"]
    i = r["acceptanceDateTime"].index("2026-09-08T20:05:15.000Z")
    r["acceptanceDateTime"][i] = "no-es-una-fecha"
    reg["texto"] = json.dumps(cuerpo)
    t.registros[reg["url"]] = reg
    assert all(v is FALTANTE for v in f.columnas("NTLA", datetime(2026, 9, 9, 13, 40, tzinfo=UTC)).values())
    # Lejos de la vecindad la ilegible no molesta.
    assert f.columnas("NTLA", datetime(2026, 8, 6, 13, 40, tzinfo=UTC))["edgar_8k_nivel"] == 1


def test_los_archivos_extra_se_piden_solo_si_cubren_el_rango(tmp_path, monkeypatch):
    # Con `desde` en 2010, el archivo de AAPL (1994–2015) hace falta y no está grabado: FALTANTE.
    f, t = _fuente(tmp_path, monkeypatch, fallar={"https://data.sec.gov/submissions/CIK0000320193-submissions-001.json"},
                   desde=date(2010, 1, 1))
    fila = f.columnas("AAPL", datetime(2026, 7, 30, 21, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    assert any("submissions-001" in url for url, _ in t.pedidos)


def test_descarga_caida_es_faltante_y_no_se_reintenta_en_el_proceso(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch, fallar={"https://data.sec.gov/submissions/CIK0001652130.json"})
    fila = f.columnas("NTLA", datetime(2026, 9, 9, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())
    n = len(t.pedidos)
    f.columnas("NTLA", datetime(2026, 9, 10, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n
    assert f.cache.leer("edgar_submissions", "https://data.sec.gov/submissions/CIK0001652130.json") is None


def test_sin_user_agent_no_se_pide_nada(tmp_path):
    with pytest.raises(ErrorFuente, match="sin_user_agent"):
        edgar.cliente_edgar()
    f = edgar.Edgar8K(Cache(tmp_path / "c"))
    fila = f.columnas("AAPL", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_user_agent_sin_contacto_no_vale(monkeypatch):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "solo-un-nombre")
    with pytest.raises(ErrorFuente, match="sin_user_agent"):
        edgar.user_agent_configurado()


def test_la_cache_evita_repetir_y_el_limitador_existe(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    f.columnas("AAPL", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    n = len(t.pedidos)
    g = edgar.Edgar8K(lector=edgar.LectorEdgar(f.cache, f.cliente(), DESDE))
    g.columnas("AAPL", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert len(t.pedidos) == n
    assert f.cliente().limitador is not None and f.cliente().limitador.llamadas <= 10


def test_cuerpo_sin_filings_es_faltante(tmp_path, monkeypatch):
    f, t = _fuente(tmp_path, monkeypatch)
    t.registros["https://data.sec.gov/submissions/CIK0000320193.json"] = {"status": 200, "headers": {}, "texto": "{}"}
    fila = f.columnas("AAPL", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values())


def test_el_comando_grabar_esta_registrado():
    from fuentes import __main__ as cli_main
    from fuentes.cli import COMANDOS
    cli_main._cargar_comandos()
    assert "edgar" in COMANDOS
