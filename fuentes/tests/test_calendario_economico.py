"""Calendario económico: parsers sobre las páginas reales, cobertura por año, ventana de 15 min solo Fed.

Fixtures REALES (VPS, 2026-09-28): Fed fomccalendars.htm, BLS cpi.htm y
empsit.htm (con User-Agent con correo; también el 403 del UA sin correo)
y BEA news/schedule/full."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

import pytest

from fuentes import FALTANTE, ErrorFuente, calendario_economico as ce
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde

PAGINAS = ["fomc", "cpi", "nominas", "pib"]
UA = "hernan-portafolio pruebas@example.com"


def _archivo(tmp_path, doc):
    ruta = tmp_path / "cal.json"
    ruta.write_text(json.dumps(doc), encoding="utf-8")
    return ruta


def _fuente(tmp_path, monkeypatch, doc=None, fallar=None, descargar=True, ua=UA):
    if ua:
        monkeypatch.setenv(ce.ENV_USER_AGENT, ua)
    t = transporte_desde("calendario_economico", PAGINAS, fallar)
    ruta = _archivo(tmp_path, doc if doc is not None else {"cobertura": {}, "eventos": []})
    c = ce.cliente_calendario(transport=t, dormir=lambda s: None) if ua else None
    return ce.CalendarioEconomico(Cache(tmp_path / "c"), c, ruta, descargar), t


def _fechas(an, anio):
    return [a.fecha.isoformat()[5:] for a in an if a.fecha.year == anio]


def test_parsear_fomc_real():
    an, anios = ce.parsear_fomc(cargar("calendario_economico", "fomc")["texto"])
    assert {2025, 2026, 2027} <= anios
    assert _fechas(an, 2026) == ["01-28", "03-18", "04-29", "06-17", "07-29", "09-16", "10-28", "12-09"]
    assert "12-10" in _fechas(an, 2025) and all(a.hora == time(14, 0) for a in an)
    assert ce.parsear_fomc("<html>nada</html>") == ([], set())


def test_parsear_bls_real_solo_la_tabla():
    an, anios = ce.parsear_bls(cargar("calendario_economico", "cpi")["texto"], "CPI")
    assert _fechas(an, 2026) == ["01-13", "02-13", "03-11", "04-10", "05-12", "06-10", "07-14", "08-12", "09-11",
                                 "10-14", "11-10", "12-10"]
    assert _fechas(an, 2025) == ["12-18"] and anios == {2025, 2026}
    assert all(a.hora == time(8, 30) for a in an)
    # Las fechas del <script> (PPI 12/2, productividad 5/2, importaciones 18/2) NO entran.
    assert "02-05" not in _fechas(an, 2026) and "02-12" not in _fechas(an, 2026) and "02-18" not in _fechas(an, 2026)
    an, _ = ce.parsear_bls(cargar("calendario_economico", "nominas")["texto"], "NOMINAS")
    assert _fechas(an, 2026) == ["01-09", "02-11", "03-06", "04-03", "05-08", "06-05", "07-02", "08-07", "09-04",
                                 "10-02", "11-06", "12-04"]
    assert _fechas(an, 2025) == ["12-16"]
    assert ce.parsear_bls("<html>sin tabla</html>", "CPI") == ([], set())


def test_parsear_bea_real_full():
    an, anios = ce.parsear_bea(cargar("calendario_economico", "pib")["texto"])
    assert anios == {2026}
    assert _fechas(an, 2026) == ["01-22", "02-20", "03-13", "04-09", "04-30", "05-28", "06-25", "07-30", "08-26",
                                 "09-30", "10-29", "11-25", "12-23"]
    assert all(a.hora == time(8, 30) for a in an)
    # "GDP by County" (5/2 y 2/12) no es el PIB.
    assert "02-05" not in _fechas(an, 2026) and "12-02" not in _fechas(an, 2026)
    assert ce.parsear_bea("<html>sin año</html>") == ([], set())


def test_archivo_del_repo_coincide_con_las_paginas():
    an, cob = ce.cargar_archivo()
    assert cob == {"FOMC": {2026}, "CPI": {2026}, "NOMINAS": {2026}, "PIB": {2026}}
    for tipo, nombre in (("FOMC", "fomc"), ("CPI", "cpi"), ("NOMINAS", "nominas"), ("PIB", "pib")):
        web, _ = ce.PARSERS[tipo](cargar("calendario_economico", nombre)["texto"])
        assert {a.fecha for a in an if a.tipo == tipo} == {a.fecha for a in web if a.fecha.year == 2026}, tipo
    doc = json.loads(ce.RUTA_ARCHIVO.read_text(encoding="utf-8"))
    assert all(e["verificado"] for e in doc["eventos"])


def test_user_agent_exige_correo(monkeypatch):
    monkeypatch.delenv(ce.ENV_USER_AGENT, raising=False)
    monkeypatch.delenv(ce.ENV_USER_AGENT_SEC, raising=False)
    with pytest.raises(ErrorFuente, match="sin_user_agent"):
        ce.cliente_calendario()
    monkeypatch.setenv(ce.ENV_USER_AGENT_SEC, "nombre correo@dominio.com")
    assert ce.user_agent_configurado() == "nombre correo@dominio.com"
    monkeypatch.setenv(ce.ENV_USER_AGENT, "sin-correo")
    assert ce.user_agent_configurado() == "nombre correo@dominio.com"   # el SEC vale de respaldo
    assert cargar("calendario_economico", "cpi_403")["status"] == 403   # lo que BLS responde sin correo


def test_columnas_dia_fomc_y_ventana_de_15_min(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    # 16/9/2026 es FOMC. 14:00 NY = 18:00 UTC.
    fila = f.columnas("SPY", datetime(2026, 9, 16, 17, 50, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is True and fila["eco_fed_ventana_15min"] is True
    assert fila["eco_anuncio_dia"] == "FOMC" and fila["eco_cpi_dia"] is False and fila["eco_pib_dia"] is False
    assert f.columnas("SPY", datetime(2026, 9, 16, 14, 0, tzinfo=UTC))["eco_fed_ventana_15min"] is False
    assert f.columnas("SPY", datetime(2026, 9, 16, 18, 15, tzinfo=UTC))["eco_fed_ventana_15min"] is True
    assert f.columnas("SPY", datetime(2026, 9, 16, 18, 16, tzinfo=UTC))["eco_fed_ventana_15min"] is False


def test_cpi_no_tiene_ventana_y_dia_limpio_es_none(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("SPY", datetime(2026, 9, 11, 12, 35, tzinfo=UTC))   # 08:35 NY, CPI recién salido
    assert fila["eco_cpi_dia"] is True and fila["eco_anuncio_dia"] == "CPI" and fila["eco_fed_ventana_15min"] is False
    fila = f.columnas("SPY", datetime(2026, 9, 30, 14, 0, tzinfo=UTC))
    assert fila == {"eco_fomc_dia": False, "eco_cpi_dia": False, "eco_nominas_dia": False, "eco_pib_dia": True,
                    "eco_anuncio_dia": "PIB", "eco_fed_ventana_15min": False}
    assert f.columnas("SPY", datetime(2026, 9, 22, 14, 0, tzinfo=UTC))["eco_anuncio_dia"] is None


def test_anio_sin_cobertura_es_faltante(tmp_path, monkeypatch):
    f, _ = _fuente(tmp_path, monkeypatch)
    # 2024: la Fed cubre 2021–2027 (False), BLS/BEA no (FALTANTE); el resumen es FALTANTE.
    fila = f.columnas("SPY", datetime(2024, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["eco_cpi_dia"] is FALTANTE and fila["eco_anuncio_dia"] is FALTANTE
    assert fila["eco_fomc_dia"] is False and fila["eco_fed_ventana_15min"] is False
    fila = f.columnas("SPY", datetime(2019, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is FALTANTE and fila["eco_fed_ventana_15min"] is FALTANTE
    # 2025: la Fed y la cola de BLS cubren (dic 2025); BEA no.
    fila = f.columnas("SPY", datetime(2025, 12, 10, 18, 0, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is True and fila["eco_pib_dia"] is FALTANTE and fila["eco_anuncio_dia"] == "FOMC"


def test_pagina_caida_deja_el_tipo_al_archivo(tmp_path, monkeypatch):
    doc = {"cobertura": {"CPI": [2026]}, "eventos": [{"tipo": "CPI", "fecha": "2026-09-11"}]}
    f, _ = _fuente(tmp_path, monkeypatch, doc, fallar={ce.URLS["CPI"], ce.URLS["FOMC"]})
    fila = f.columnas("SPY", datetime(2026, 9, 11, 14, 0, tzinfo=UTC))
    assert fila["eco_cpi_dia"] is True and fila["eco_fomc_dia"] is FALTANTE and fila["eco_pib_dia"] is False


def test_sin_user_agent_o_sin_descarga_solo_archivo(tmp_path, monkeypatch):
    doc = {"cobertura": {"FOMC": [2026]}, "eventos": [{"tipo": "FOMC", "fecha": "2026-09-16", "hora": "14:00"}]}
    f, t = _fuente(tmp_path, monkeypatch, doc, descargar=False)
    fila = f.columnas("SPY", datetime(2026, 9, 16, 18, 0, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is True and fila["eco_cpi_dia"] is FALTANTE and t.pedidos == []
    monkeypatch.delenv(ce.ENV_USER_AGENT, raising=False)
    monkeypatch.delenv(ce.ENV_USER_AGENT_SEC, raising=False)
    g = ce.CalendarioEconomico(Cache(tmp_path / "c2"), None, _archivo(tmp_path, doc))
    fila = g.columnas("SPY", datetime(2026, 9, 16, 18, 0, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is True and fila["eco_cpi_dia"] is FALTANTE
