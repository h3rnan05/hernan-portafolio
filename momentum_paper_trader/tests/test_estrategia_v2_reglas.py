"""Reglas puras de la v2: catalizador (veto + formato de la IA), señal y riesgo."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from estrategia_v2 import catalizador as cat
from estrategia_v2 import reglas
from estrategia_v2.config import cargar
from estrategia_v2.reglas import Vela

CFG = cargar()
K = CFG.catalizador
T930 = datetime(2026, 9, 25, 13, 30, tzinfo=UTC)     # 09:30 NY (verano) = 07:30 Monterrey


# ---------------------------------------------------------------- catalizador


def test_veto_por_frase_en_titular_o_resumen():
    assert cat.veto("Acme Announces $50M Public Offering", "", K) == "oferta_de_acciones"
    assert cat.veto("Acme update", "auditor raises substantial doubt", K) == "going_concern"
    assert cat.veto("Acme wins $20M contract", "with the Army", K) is None


def test_respuesta_valida():
    c = cat.interpretar('{"nivel": 1, "tipo": "contrato", "direccion": "alcista", "confianza": 0.8}')
    assert c.valida and c.nivel == 1 and c.confianza == 0.8
    assert cat.operable(c, K)


@pytest.mark.parametrize("texto, motivo", [
    ("no es json", "json_invalido"),
    ('Claro: {"nivel": 1, "tipo": "x", "direccion": "alcista", "confianza": 0.8}', "json_invalido"),
    ('{"nivel": 1, "tipo": "x", "direccion": "alcista"}', "claves"),
    ('{"nivel": 1, "tipo": "x", "direccion": "alcista", "confianza": 0.8, "entrar": true}', "claves"),
    ('{"nivel": 3, "tipo": "x", "direccion": "alcista", "confianza": 0.8}', "nivel"),
    ('{"nivel": true, "tipo": "x", "direccion": "alcista", "confianza": 0.8}', "nivel"),
    ('{"nivel": 1, "tipo": "x", "direccion": "arriba", "confianza": 0.8}', "direccion"),
    ('{"nivel": 1, "tipo": "x", "direccion": "alcista", "confianza": 8}', "confianza"),
])
def test_respuesta_invalida_nunca_adivina_un_nivel(texto, motivo):
    c = cat.interpretar(texto)
    assert not c.valida and c.motivo == motivo and c.nivel is None


def test_nivel_0_y_bajista_no_son_operables_y_gana_el_nivel_mas_fuerte():
    n0 = cat.Clasificacion(True, 0, "opinion", "alcista", 0.9)
    n2 = cat.Clasificacion(True, 2, "upgrade", "alcista", 0.7)
    n1b = cat.Clasificacion(True, 1, "resultados", "bajista", 0.9)
    n1 = cat.Clasificacion(True, 1, "fda", "alcista", 0.6)
    assert cat.mejor_nivel([n0, n1b], K) is None
    assert cat.mejor_nivel([n0, n2], K) == 2
    assert cat.mejor_nivel([n2, n1], K) == 1


def test_el_prompt_sale_del_yaml_y_pide_solo_json():
    system, user = cat.prompts("Acme wins FDA approval", "Drug X approved", K)
    assert "SOLO un objeto JSON" in system and "aprobación de la FDA" in system
    assert "Acme wins FDA approval" in user and "Drug X approved" in user
    json.loads('{"nivel": 0, "tipo": "t", "direccion": "neutral", "confianza": 0}')   # el formato pedido es JSON


# ---------------------------------------------------------------------- señal


def _velas(cierres, vols=None, desde=T930, altos=None, bajos=None):
    vols = vols or [1000.0] * len(cierres)
    out = []
    for k, c in enumerate(cierres):
        h = altos[k] if altos else c + 0.05
        lo = bajos[k] if bajos else c - 0.05
        out.append(Vela(desde + timedelta(minutes=k), c, h, lo, c, vols[k]))
    return out


def _dia_ruptura():
    # 09:30-09:34 rango 10.00-10.20; luego lateral; 09:40 rompe con volumen.
    cierres = [10.1, 10.15, 10.1, 10.05, 10.1] + [10.1] * 5 + [10.4]
    altos = [10.2] * 5 + [10.15] * 5 + [10.45]
    bajos = [10.0] * 5 + [10.05] * 5 + [10.3]
    vols = [1000.0] * 10 + [5000.0]
    return _velas(cierres, vols, altos=altos, bajos=bajos)


def _eval(velas, i, **kw):
    base = dict(gap_oficial=0.06, rvol_valor=4.0, spread_pct=0.001, indice_sobre_vwap=True, cfg=CFG)
    base.update(kw)
    return reglas.evaluar_ruptura(velas, i, **base)


def test_ruptura_que_cumple_todo():
    ev = _eval(_dia_ruptura(), 10)
    assert ev.ok, ev.fallos
    assert (ev.orb_alto, ev.orb_bajo) == (10.2, 10.0)


@pytest.mark.parametrize("cambio, fallo", [
    ({"gap_oficial": 0.03}, "gap"),
    ({"gap_oficial": None}, "gap_sin_dato"),
    ({"rvol_valor": 2.9}, "rvol"),
    ({"rvol_valor": None}, "rvol_sin_dato"),
    ({"spread_pct": 0.004}, "spread"),
    ({"spread_pct": None}, "spread_sin_dato"),
    ({"indice_sobre_vwap": False}, "indice"),
    ({"indice_sobre_vwap": None}, "indice_sin_dato"),
])
def test_cada_condicion_falla_sola(cambio, fallo):
    ev = _eval(_dia_ruptura(), 10, **cambio)
    assert not ev.ok and ev.fallos == [fallo]


def test_vela_de_ruptura_sin_volumen_suficiente():
    velas = _dia_ruptura()
    velas[10] = Vela(velas[10].t, 10.4, 10.45, 10.3, 10.4, 1400.0)   # < 1.5 × 1000
    assert _eval(velas, 10).fallos == ["vol_ruptura"]


def test_sin_cruce_no_hay_ruptura_y_la_segunda_vela_arriba_tampoco():
    velas = _dia_ruptura() + _velas([10.5], [5000.0], desde=T930 + timedelta(minutes=11))
    assert "sin_ruptura" in _eval(velas, 11).fallos      # la anterior ya cerraba arriba


def test_fuera_de_ventana():
    # Una ruptura que cierra 09:35 NY (antes de 09:36) no cuenta.
    cierres = [10.1, 10.15, 10.1, 10.05, 10.5]
    velas = _velas(cierres, [1000, 1000, 1000, 1000, 5000], altos=[10.2] * 4 + [10.55], bajos=[10.0] * 5)
    assert "fuera_de_ventana" in _eval(velas, 4).fallos


def test_rvol_exige_todas_las_sesiones():
    s = CFG.senal
    assert reglas.rvol(300, [100.0] * s.rvol_dias, s) == 3.0
    assert reglas.rvol(300, [100.0] * (s.rvol_dias - 1), s) is None
    assert reglas.rvol(300, [100.0] * (s.rvol_dias - 1) + [None], s) is None


def test_minuto_de_sesion_en_hora_de_nueva_york_con_y_sin_horario_de_verano():
    s = CFG.senal
    assert reglas.minuto_de_sesion(datetime(2026, 7, 1, 13, 45, tzinfo=UTC), s) == 15
    assert reglas.minuto_de_sesion(datetime(2026, 12, 1, 14, 45, tzinfo=UTC), s) == 15


# --------------------------------------------------------------------- riesgo


def test_stop_acotado_entre_minimo_y_maximo():
    r = CFG.riesgo
    assert reglas.stop_inicial(10.0, 9.7, r) == 9.7                       # 3 %: dentro
    assert reglas.stop_inicial(10.0, 9.95, r) == pytest.approx(9.85)      # 0,5 % -> 1,5 %
    assert reglas.stop_inicial(10.0, 9.5, r) == 9.5                       # 5 % entra (tope 6 % desde 2026-09-28)
    assert reglas.stop_inicial(10.0, 9.3, r) is None                      # 7 % > 6 %: no entra


def test_objetivo_2r():
    assert reglas.objetivo(10.0, 9.7, CFG.riesgo) == pytest.approx(10.6)


def test_tamano_por_riesgo_nivel_y_topes():
    r = CFG.riesgo
    # 0,5 % de 5.000 = 25 / 0,30 = 83 acciones; tope 25 % = 1.250 / 10 = 125.
    assert reglas.tamano(5000, 5000, 10.0, 9.7, 1, r) == 83
    assert reglas.tamano(5000, 5000, 10.0, 9.7, 2, r) == 41             # nivel 2: medio tamaño
    assert reglas.tamano(5000, 5000, 10.0, 9.99, 1, r) == 125           # topado al 25 %
    assert reglas.tamano(5000, 300, 10.0, 9.7, 1, r) == 30              # nunca más que el efectivo
    assert reglas.tamano(5000, 5000, 10.0, 10.0, 1, r) == 0
    assert reglas.tamano(5000, 5000, 10.0, 9.7, 0, r) == 0              # nivel 0 no opera
