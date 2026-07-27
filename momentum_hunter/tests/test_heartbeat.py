"""Pruebas de la confirmación de fin de día -- solo debe activarse cerca
del cierre, solo si no hubo alertas, y solo una vez por día."""

from __future__ import annotations

from datetime import date

from momentum_hunter.heartbeat import (
    HORA_UTC_CIERRE_RESUMEN,
    EstadoDiario,
    cargar_estado,
    guardar_estado,
    necesita_resumen_cierre,
    registrar_enviado,
)

HOY = date(2026, 7, 27)


def test_no_antes_de_la_hora_de_cierre():
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN - 0.1, 0, None) is False


def test_si_despues_del_cierre_sin_alertas_y_sin_estado_previo():
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN, 0, None) is True


def test_no_si_hubo_alertas_hoy():
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN, 1, None) is False


def test_no_si_ya_se_envio_hoy():
    estado = EstadoDiario(fecha=HOY.isoformat(), resumen_enviado=True)
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN, 0, estado) is False


def test_si_el_estado_previo_es_de_otro_dia():
    estado = EstadoDiario(fecha="2026-07-20", resumen_enviado=True)
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN, 0, estado) is True


def test_si_hay_estado_del_dia_pero_no_se_habia_enviado():
    estado = EstadoDiario(fecha=HOY.isoformat(), resumen_enviado=False)
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN, 0, estado) is True


def test_guardar_y_cargar_estado_roundtrip(tmp_path):
    path = tmp_path / "estado.json"
    guardar_estado(EstadoDiario(fecha=HOY.isoformat(), resumen_enviado=True), path)
    recargado = cargar_estado(path)
    assert recargado == EstadoDiario(fecha=HOY.isoformat(), resumen_enviado=True)


def test_cargar_estado_archivo_inexistente(tmp_path):
    assert cargar_estado(tmp_path / "no_existe.json") is None


def test_cargar_estado_archivo_corrupto_no_lanza(tmp_path):
    path = tmp_path / "estado.json"
    path.write_text("{esto no es json")
    assert cargar_estado(path) is None


def test_registrar_enviado_deja_el_estado_marcado(tmp_path):
    path = tmp_path / "estado.json"
    registrar_enviado(HOY, path)
    estado = cargar_estado(path)
    assert estado is not None
    assert estado.fecha == HOY.isoformat()
    assert estado.resumen_enviado is True


def test_flujo_completo_no_se_repite_en_la_misma_corrida_del_dia(tmp_path):
    path = tmp_path / "estado.json"
    estado = cargar_estado(path)
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN, 0, estado) is True
    registrar_enviado(HOY, path)

    # Una segunda corrida del cron el mismo día, sin alertas, ya no debe disparar otra vez.
    estado = cargar_estado(path)
    assert necesita_resumen_cierre(HOY, HORA_UTC_CIERRE_RESUMEN + 0.5, 0, estado) is False
