"""Calendario de resultados: Finnhub (con token fuera de la caché) y respaldo 8-K 2.02."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, edgar, resultados
from fuentes.cache import Cache
from fuentes.grabar import TransporteGrabado, cargar, transporte_desde
from fuentes.tests.test_edgar import TODAS


def _finnhub(tmp_path, monkeypatch, token="clave-secreta"):
    if token:
        monkeypatch.setenv(resultados.ENV_TOKEN, token)
    reg = cargar("resultados", "calendario_FICA")
    t = TransporteGrabado()
    t.agregar(reg["url"], {**reg["params"], "token": token}, reg)
    return resultados.ResultadosFinnhub(Cache(tmp_path / "c"), resultados.cliente_finnhub(transport=t, dormir=lambda s: None)), t


E = resultados.Evento


def test_leer_calendario_filtra_por_simbolo_y_normaliza_la_hora():
    import json
    ev = resultados.leer_calendario(json.loads(cargar("resultados", "calendario_FICA")["texto"]), "FICA")
    assert len(ev) == 6 and ev[0].fecha == date(2025, 11, 5) and ev[3].hora is None
    with pytest.raises(ErrorFuente, match="cuerpo"):
        resultados.leer_calendario({"error": "x"}, "FICA")


def test_reciente_amc_de_ayer_y_bmo_de_hoy():
    ev = [E(date(2026, 9, 24), "amc")]
    assert resultados.reciente(ev, datetime(2026, 9, 25, 13, 40, tzinfo=UTC)) == (True, ev[0])
    # 24 h después del cierre del 24 (20:00 UTC) ya no es reciente.
    assert resultados.reciente(ev, datetime(2026, 9, 25, 21, 0, tzinfo=UTC)) == (False, None)
    # Antes del cierre del 24 no había salido.
    assert resultados.reciente(ev, datetime(2026, 9, 24, 15, 0, tzinfo=UTC)) == (False, None)
    ev = [E(date(2026, 9, 25), "bmo")]
    assert resultados.reciente(ev, datetime(2026, 9, 25, 13, 40, tzinfo=UTC)) == (True, ev[0])
    # A las 08:00 NY con un bmo el mismo día: no se sabe si ya salió.
    assert resultados.reciente(ev, datetime(2026, 9, 25, 12, 0, tzinfo=UTC)) == (FALTANTE, None)


def test_dmh_o_sin_hora_del_dia_es_faltante_no_false():
    assert resultados.reciente([E(date(2026, 9, 25), "dmh")], datetime(2026, 9, 25, 14, 0, tzinfo=UTC)) == (FALTANTE, None)
    assert resultados.reciente([E(date(2026, 9, 25), None)], datetime(2026, 9, 25, 14, 0, tzinfo=UTC)) == (FALTANTE, None)
    # Un dmh de hace una semana no molesta.
    assert resultados.reciente([E(date(2026, 9, 18), "dmh")], datetime(2026, 9, 25, 14, 0, tzinfo=UTC)) == (False, None)


def test_proximo_en_dias():
    ev = [E(date(2026, 9, 24), "amc"), E(date(2026, 11, 4), "amc")]
    assert resultados.proximo(ev, datetime(2026, 9, 24, 14, 0, tzinfo=UTC)) == 0   # hoy, tras el cierre
    assert resultados.proximo(ev, datetime(2026, 9, 24, 21, 0, tzinfo=UTC)) is None  # el de nov está a > 30 d
    assert resultados.proximo(ev, datetime(2026, 10, 20, 14, 0, tzinfo=UTC)) == 15


def test_columnas_finnhub(tmp_path, monkeypatch):
    f, t = _finnhub(tmp_path, monkeypatch)
    fila = f.columnas("FICA", datetime(2026, 9, 25, 13, 40, tzinfo=UTC))
    assert fila == {"resultados_reciente": True, "resultados_fecha": "2026-09-24", "resultados_hora": "amc",
                    "resultados_proximo_dias": None, "resultados_fuente": "finnhub"}
    fila = f.columnas("FICA", datetime(2026, 10, 20, 14, 0, tzinfo=UTC))
    assert fila["resultados_reciente"] is False and fila["resultados_proximo_dias"] == 15
    assert len(t.pedidos) == 1   # misma ventana anual: un solo pedido


def test_el_token_no_va_a_la_cache_ni_a_la_respuesta_grabada(tmp_path, monkeypatch):
    f, t = _finnhub(tmp_path, monkeypatch)
    f.columnas("FICA", datetime(2026, 9, 25, 13, 40, tzinfo=UTC))
    assert t.pedidos[0][1]["token"] == "clave-secreta"
    archivos = list((tmp_path / "c").rglob("*.json"))
    assert archivos and all("clave-secreta" not in a.read_text(encoding="utf-8") for a in archivos)
    assert "token" not in cargar("resultados", "calendario_FICA")["params"]


def test_sin_token_es_faltante(tmp_path, monkeypatch):
    f, t = _finnhub(tmp_path, monkeypatch, token="")
    monkeypatch.delenv(resultados.ENV_TOKEN, raising=False)
    fila = f.columnas("FICA", datetime(2026, 9, 25, 13, 40, tzinfo=UTC))
    assert all(v is FALTANTE for v in fila.values()) and t.pedidos == []


def test_respaldo_8k_202(tmp_path, monkeypatch):
    monkeypatch.setenv(edgar.ENV_USER_AGENT, "hernan-portafolio pruebas@example.com")
    t = transporte_desde("edgar", TODAS)
    lector = edgar.LectorEdgar(Cache(tmp_path / "c"), edgar.cliente_edgar(transport=t, dormir=lambda s: None))
    f = resultados.ResultadosEdgar(lector)
    assert f.nombres() == resultados.ResultadosFinnhub._NOMBRES
    fila = f.columnas("FICA", datetime(2026, 9, 25, 13, 40, tzinfo=UTC))
    assert fila == {"resultados_reciente": True, "resultados_fecha": "2026-09-24", "resultados_hora": "amc",
                    "resultados_proximo_dias": FALTANTE, "resultados_fuente": "8k_2.02"}
    # El 8-K 8.01 del 25 no es resultados.
    fila = f.columnas("FICA", datetime(2026, 9, 26, 14, 0, tzinfo=UTC))
    assert fila["resultados_reciente"] is False and fila["resultados_proximo_dias"] is FALTANTE
    assert all(v is FALTANTE for v in f.columnas("NOEXISTE", datetime(2026, 9, 26, 14, 0, tzinfo=UTC)).values())
