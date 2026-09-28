"""Calendario económico: parsers tolerantes, cobertura por año, ventana de 15 min solo para la Fed."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

from fuentes import FALTANTE, calendario_economico as ce
from fuentes.cache import Cache
from fuentes.grabar import cargar, transporte_desde

PAGINAS = ["fomc", "cpi", "nominas", "pib"]


def _archivo(tmp_path, doc):
    ruta = tmp_path / "cal.json"
    ruta.write_text(json.dumps(doc), encoding="utf-8")
    return ruta


def _fuente(tmp_path, doc=None, fallar=None, descargar=True):
    t = transporte_desde("calendario_economico", PAGINAS, fallar)
    ruta = _archivo(tmp_path, doc if doc is not None else {"cobertura": {}, "eventos": []})
    c = ce.cliente_calendario(transport=t, dormir=lambda s: None)
    return ce.CalendarioEconomico(Cache(tmp_path / "c"), c, ruta, descargar), t


def test_parsear_fomc_ultimo_dia_meses_cruzados_y_sin_notation():
    an, anios = ce.parsear_fomc(cargar("calendario_economico", "fomc")["texto"])
    assert anios == {2025, 2026}
    assert [a.fecha for a in an] == [date(2026, 1, 28), date(2026, 3, 18), date(2026, 5, 1), date(2026, 9, 16),
                                     date(2025, 12, 10)]
    assert all(a.hora == time(14, 0) for a in an)
    assert ce.parsear_fomc("<html>nada</html>") == ([], set())


def test_parsear_bls_y_bea():
    an, anios = ce.parsear_bls(cargar("calendario_economico", "cpi")["texto"], "CPI")
    assert [a.fecha for a in an] == [date(2026, 9, 11), date(2026, 10, 14), date(2026, 11, 12)] and anios == {2026}
    an, _ = ce.parsear_bea(cargar("calendario_economico", "pib")["texto"])
    assert [a.fecha for a in an] == [date(2026, 9, 25), date(2026, 10, 29)]   # PCE no es PIB


def test_archivo_del_repo_es_legible_y_declara_cobertura():
    an, cob = ce.cargar_archivo()
    assert cob["FOMC"] == {2026} and cob["CPI"] == set()
    assert date(2026, 9, 16) in {a.fecha for a in an if a.tipo == "FOMC"}


def test_columnas_dia_fomc_y_ventana_de_15_min(tmp_path):
    f, _ = _fuente(tmp_path)
    # 16/9/2026 es FOMC (página). 14:00 NY = 18:00 UTC.
    fila = f.columnas("SPY", datetime(2026, 9, 16, 17, 50, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is True and fila["eco_fed_ventana_15min"] is True
    assert fila["eco_anuncio_dia"] == "FOMC" and fila["eco_cpi_dia"] is False and fila["eco_pib_dia"] is False
    fila = f.columnas("SPY", datetime(2026, 9, 16, 14, 0, tzinfo=UTC))
    assert fila["eco_fed_ventana_15min"] is False
    fila = f.columnas("SPY", datetime(2026, 9, 16, 18, 15, tzinfo=UTC))
    assert fila["eco_fed_ventana_15min"] is True
    fila = f.columnas("SPY", datetime(2026, 9, 16, 18, 16, tzinfo=UTC))
    assert fila["eco_fed_ventana_15min"] is False


def test_cpi_no_tiene_ventana_y_dia_limpio_es_none(tmp_path):
    f, _ = _fuente(tmp_path)
    fila = f.columnas("SPY", datetime(2026, 9, 11, 12, 35, tzinfo=UTC))   # 08:35 NY, CPI recién salido
    assert fila["eco_cpi_dia"] is True and fila["eco_anuncio_dia"] == "CPI" and fila["eco_fed_ventana_15min"] is False
    fila = f.columnas("SPY", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila == {"eco_fomc_dia": False, "eco_cpi_dia": False, "eco_nominas_dia": False, "eco_pib_dia": True,
                    "eco_anuncio_dia": "PIB", "eco_fed_ventana_15min": False}
    fila = f.columnas("SPY", datetime(2026, 9, 22, 14, 0, tzinfo=UTC))
    assert fila["eco_anuncio_dia"] is None


def test_anio_sin_cobertura_es_faltante(tmp_path):
    f, _ = _fuente(tmp_path)
    fila = f.columnas("SPY", datetime(2024, 9, 25, 14, 0, tzinfo=UTC))
    assert fila["eco_cpi_dia"] is FALTANTE and fila["eco_anuncio_dia"] is FALTANTE and fila["eco_fed_ventana_15min"] is FALTANTE
    # 2025: la página de la Fed cubre (dic 2025), BLS no.
    fila = f.columnas("SPY", datetime(2025, 12, 10, 18, 0, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is True and fila["eco_cpi_dia"] is FALTANTE and fila["eco_anuncio_dia"] == "FOMC"


def test_pagina_caida_deja_el_tipo_al_archivo(tmp_path):
    doc = {"cobertura": {"CPI": [2026]}, "eventos": [{"tipo": "CPI", "fecha": "2026-09-11"}]}
    f, _ = _fuente(tmp_path, doc, fallar={ce.URLS["CPI"], ce.URLS["FOMC"]})
    fila = f.columnas("SPY", datetime(2026, 9, 11, 14, 0, tzinfo=UTC))
    assert fila["eco_cpi_dia"] is True and fila["eco_fomc_dia"] is FALTANTE and fila["eco_pib_dia"] is False


def test_sin_descarga_solo_archivo(tmp_path):
    f, t = _fuente(tmp_path, {"cobertura": {"FOMC": [2026]}, "eventos": [{"tipo": "FOMC", "fecha": "2026-09-16", "hora": "14:00"}]},
                   descargar=False)
    fila = f.columnas("SPY", datetime(2026, 9, 16, 18, 0, tzinfo=UTC))
    assert fila["eco_fomc_dia"] is True and fila["eco_cpi_dia"] is FALTANTE and t.pedidos == []
