"""Pruebas del horario de sesión -- puro calendario, sin red.

El caso que motivó el módulo: el cron corre `13-20` y esa hora `20`
abarca hasta las 20:55, así que la última hora de escaneo de cada día
ocurría entera con el mercado cerrado (ver `sesion.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_hunter import sesion


def _utc(mes, dia, hora, minuto=0):
    return datetime(2026, mes, dia, hora, minuto, tzinfo=UTC)


# ------------------------- verano (EDT, UTC-4) -------------------------

def test_verano_en_plena_sesion():
    assert sesion.en_sesion(_utc(8, 26, 17, 0)) is True     # 13:00 ET


def test_verano_apertura_incluida_cierre_excluido():
    assert sesion.en_sesion(_utc(8, 26, 13, 30)) is True    # 9:30 ET, abre
    assert sesion.en_sesion(_utc(8, 26, 13, 29)) is False   # un minuto antes
    assert sesion.en_sesion(_utc(8, 26, 20, 0)) is False    # 16:00 ET, ya cerró


def test_el_caso_real_las_2023(): 
    # FLEX disparó a las 20:23 UTC del 2026-08-25 y nunca se pudo operar.
    assert sesion.en_sesion(_utc(8, 25, 20, 23)) is False


# ------------------------- invierno (EST, UTC-5) -------------------------

def test_invierno_la_sesion_se_corre_una_hora():
    # En diciembre el mercado cierra a las 21:00 UTC, no a las 20:00.
    # Con la constante de verano hardcodeada, esto se equivocaba.
    assert sesion.en_sesion(_utc(12, 2, 20, 30)) is True
    assert sesion.en_sesion(_utc(12, 2, 21, 0)) is False
    assert sesion.en_sesion(_utc(12, 2, 14, 30)) is True    # abre 9:30 ET
    assert sesion.en_sesion(_utc(12, 2, 14, 0)) is False


def test_el_cambio_de_horario_no_necesita_tocar_codigo():
    # Mismo instante UTC, un día antes y un día después del cambio
    # (primer domingo de noviembre de 2026 = 1 de noviembre).
    assert sesion.en_sesion(_utc(10, 30, 20, 30)) is False   # aún EDT: ya cerró
    assert sesion.en_sesion(_utc(11, 2, 20, 30)) is True     # ya EST: sigue abierto


# ------------------------- fin de semana -------------------------

def test_sabado_y_domingo_cerrado():
    assert sesion.en_sesion(_utc(8, 29, 17, 0)) is False
    assert sesion.en_sesion(_utc(8, 30, 17, 0)) is False


def test_el_fin_de_semana_no_reporta_tiempo_restante():
    assert sesion.minutos_hasta_el_cierre(_utc(8, 29, 17, 0)) < 0


# ------------------------- margen antes del cierre -------------------------

def test_minutos_hasta_el_cierre():
    assert sesion.minutos_hasta_el_cierre(_utc(8, 26, 19, 30)) == 30
    assert sesion.minutos_hasta_el_cierre(_utc(8, 26, 20, 30)) == -30


def test_hay_tiempo_exige_sesion_abierta_Y_margen():
    assert sesion.hay_tiempo_para_operar(_utc(8, 26, 17, 0), 20) is True
    assert sesion.hay_tiempo_para_operar(_utc(8, 26, 19, 50), 20) is False   # solo 10 min
    assert sesion.hay_tiempo_para_operar(_utc(8, 26, 19, 40), 20) is True    # justo 20
    assert sesion.hay_tiempo_para_operar(_utc(8, 26, 20, 23), 20) is False   # cerrado


def test_premarket_no_cuenta_aunque_falte_mucho_para_el_cierre():
    # Queda muchísimo tiempo para el cierre, pero todavía no abrió: los
    # dos chequeos son necesarios, no basta con mirar cuánto falta.
    t = _utc(8, 26, 12, 0)
    assert sesion.minutos_hasta_el_cierre(t) > 200
    assert sesion.hay_tiempo_para_operar(t, 20) is False
