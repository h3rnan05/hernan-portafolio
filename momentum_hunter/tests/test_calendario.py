"""El hunter solo lee el calendario. No hay red en este módulo."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from momentum_hunter import calendario, sesion
from momentum_hunter.factors import intradia as fi


def _utc(anio, mes, dia, hora, minuto=0):
    return datetime(anio, mes, dia, hora, minuto, tzinfo=UTC)


def test_el_lector_no_abre_conexiones():
    fuente = inspect.getsource(calendario)
    assert "requests" not in fuente
    assert "paper-api" not in fuente
    assert "data.alpaca.markets" not in fuente


def test_verano_con_calendario_normal_coincide_con_el_horario_viejo():
    # 26/8/2026, EDT. 13:30 UTC = 9:30 ET; 20:00 UTC = 16:00 ET.
    assert sesion.en_sesion(_utc(2026, 8, 26, 13, 30)) is True
    assert sesion.en_sesion(_utc(2026, 8, 26, 13, 29)) is False
    assert sesion.en_sesion(_utc(2026, 8, 26, 20, 0)) is False
    assert fi.es_premarket("2026-07-26T13:00:00+00:00") is True
    assert fi.es_premarket("2026-07-26T13:29:00+00:00") is True
    assert fi.es_sesion_regular("2026-07-26T13:30:00+00:00") is True
    assert fi.es_sesion_regular("2026-07-26T13:00:00+00:00") is False


def test_invierno_la_sesion_regular_empieza_a_las_1430_utc(monkeypatch, tmp_path):
    path = tmp_path / "cal.json"
    calendario.guardar(path, {
        "fetched_at": "2026-11-02T12:00:00+00:00",
        "rango": {"desde": "2026-11-02", "hasta": "2026-11-02"},
        "dias": {"2026-11-02": {"open": "09:30", "close": "16:00"}},
    })
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(path))
    # 14:00 UTC = 9:00 ET (premarket). 14:30 UTC = 9:30. 21:00 UTC = 16:00.
    assert fi.es_premarket("2026-11-02T14:00:00+00:00") is True
    assert fi.es_sesion_regular("2026-11-02T14:00:00+00:00") is False
    assert fi.es_sesion_regular("2026-11-02T14:30:00+00:00") is True
    assert fi.es_sesion_regular("2026-11-02T20:59:00+00:00") is True
    assert fi.es_sesion_regular("2026-11-02T21:00:00+00:00") is False
    assert sesion.en_sesion(_utc(2026, 11, 2, 20, 30)) is True
    assert sesion.en_sesion(_utc(2026, 11, 2, 14, 0)) is False


def test_media_sesion_la_vela_de_despues_de_las_1300_ny_no_es_regular(monkeypatch, tmp_path):
    path = tmp_path / "cal.json"
    calendario.guardar(path, {
        "fetched_at": "2026-11-27T12:00:00+00:00",
        "rango": {"desde": "2026-11-27", "hasta": "2026-11-27"},
        "dias": {"2026-11-27": {"open": "09:30", "close": "13:00"}},
    })
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(path))
    # 17:59 UTC = 12:59 ET, todavía regular. 18:00 UTC = 13:00 ET, ya cerró.
    assert fi.es_sesion_regular("2026-11-27T17:59:00+00:00") is True
    assert fi.es_sesion_regular("2026-11-27T18:00:00+00:00") is False
    assert fi.es_premarket("2026-11-27T14:00:00+00:00") is True


def test_feriado_no_clasifica_velas_como_sesion(monkeypatch, tmp_path):
    path = tmp_path / "cal.json"
    calendario.guardar(path, {
        "fetched_at": "2026-11-25T12:00:00+00:00",
        "rango": {"desde": "2026-11-25", "hasta": "2026-11-27"},
        "dias": {
            "2026-11-25": {"open": "09:30", "close": "16:00"},
            "2026-11-27": {"open": "09:30", "close": "13:00"},
        },
    })
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(path))
    assert fi.es_sesion_regular("2026-11-26T15:00:00+00:00") is False
    assert fi.es_premarket("2026-11-26T14:00:00+00:00") is False
    assert sesion.en_sesion(_utc(2026, 11, 26, 15, 0)) is False
    assert sesion.hay_tiempo_para_operar(_utc(2026, 11, 26, 15, 0), 20) is False


def test_sin_archivo_no_hay_sesion_ni_se_inventa_un_horario(monkeypatch, tmp_path):
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(tmp_path / "no-esta.json"))
    assert sesion.en_sesion(_utc(2026, 8, 26, 17, 0)) is False
    assert sesion.hay_tiempo_para_operar(_utc(2026, 8, 26, 17, 0), 20) is False
    assert sesion.minutos_hasta_el_cierre(_utc(2026, 8, 26, 17, 0)) < 0
    assert fi.es_sesion_regular("2026-07-26T13:30:00+00:00") is False
    # Liquidar sí tiene hora: las 13:00 NY, no un cero inventado.
    # 26/8/2026 16:50 UTC = 12:50 EDT, faltan 10 min para las 13:00.
    assert sesion.minutos_para_liquidar(_utc(2026, 8, 26, 16, 50)) == 10


def test_archivo_que_no_cubre_hoy_es_lo_mismo_que_no_tenerlo(monkeypatch, tmp_path):
    path = tmp_path / "viejo.json"
    calendario.guardar(path, {
        "fetched_at": "2026-11-01T12:00:00+00:00",
        "rango": {"desde": "2026-10-25", "hasta": "2026-11-01"},
        "dias": {"2026-11-01": {"open": "09:30", "close": "16:00"}},
    })
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(path))
    assert calendario.consultar(_utc(2026, 11, 2, 15, 0)).motivo == calendario.MOTIVO_NO_CUBRE
    assert sesion.en_sesion(_utc(2026, 11, 2, 15, 0)) is False
    # 2/11/2026 17:50 UTC = 12:50 EST.
    assert sesion.minutos_para_liquidar(_utc(2026, 11, 2, 17, 50)) == 10


def test_una_hora_ausente_no_se_completa_con_cero(monkeypatch, tmp_path):
    path = tmp_path / "roto.json"
    calendario.guardar(path, {
        "fetched_at": "2026-11-02T12:00:00+00:00",
        "rango": {"desde": "2026-11-02", "hasta": "2026-11-02"},
        "dias": {"2026-11-02": {"open": "09:30"}},
    })
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(path))
    consulta = calendario.consultar(_utc(2026, 11, 2, 15, 0))
    assert consulta.desconocido is True
    assert consulta.dia is None
    assert sesion.en_sesion(_utc(2026, 11, 2, 15, 0)) is False
