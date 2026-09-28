"""Backtest v2: ejecución simulada, cartera, métricas y clasificador. Sin red."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest

from estrategia_v2 import catalizador as cat
from estrategia_v2.config import cargar
from estrategia_v2.reglas import Vela
from shadow_alpaca.backtest_v2 import informe, motor
from shadow_alpaca.backtest_v2.clasificador import Clasificador
from shadow_alpaca.backtest_v2.datos import Noticia

CFG = cargar()
DIA = date(2026, 9, 25)
T = datetime(2026, 9, 25, 13, 45, tzinfo=UTC)      # 09:45 NY = 07:45 Monterrey
P = motor.Parametros(slippage=0.0)


def _senal(precio=10.0, orb_bajo=9.7, nivel=1, momento=T, ticker="ACME", dia=DIA):
    return motor.Senal(ticker, dia, momento, precio, orb_bajo, nivel, 0.06, 4.0, "titular")


def _camino(filas, desde=T):
    """filas = [(o, h, l, c)] minuto a minuto desde `desde`."""
    return [Vela(desde + timedelta(minutes=k), o, h, lo, c, 1000.0) for k, (o, h, lo, c) in enumerate(filas)]


def _trade(filas, **kw):
    s = _senal(**kw)
    stop = 9.7
    return motor._salida(s, _camino(filas), 83, stop, 10.6, CFG, P)


# ---------------------------------------------------------------- ejecución


def test_objetivo_2r():
    t = _trade([(10.0, 10.1, 9.95, 10.05), (10.1, 10.65, 10.05, 10.6)])
    assert t.motivo == "objetivo" and t.r == pytest.approx(2.0)


def test_stop_y_en_la_vela_del_llenado_solo_se_mira_el_stop():
    t = _trade([(10.0, 10.7, 9.6, 10.0)])            # toca objetivo y stop en la vela del llenado
    assert t.motivo == "stop" and t.r == pytest.approx(-1.0)


def test_gap_por_debajo_del_stop_sale_a_la_apertura():
    t = _trade([(10.0, 10.05, 9.95, 10.0), (9.5, 9.6, 9.4, 9.5)])
    assert t.salida == pytest.approx(9.5) and t.r < -1


def test_breakeven_a_1r_y_sale_en_el_llenado():
    t = _trade([(10.0, 10.05, 9.95, 10.0), (10.0, 10.32, 10.0, 10.3), (10.2, 10.25, 9.9, 9.95)])
    assert t.motivo == "breakeven" and t.r == pytest.approx(0.0)


def test_stop_de_tiempo_sin_llegar_a_medio_r():
    filas = [(10.0, 10.1, 9.9, 10.0)] * 31 + [(10.05, 10.1, 10.0, 10.05)]
    t = _trade(filas)
    assert t.motivo == "tiempo" and t.salida_t == T + timedelta(minutes=30)


def test_si_toco_medio_r_no_hay_stop_de_tiempo():
    filas = [(10.0, 10.16, 9.9, 10.0)] + [(10.0, 10.1, 9.9, 10.0)] * 40
    assert _trade(filas).motivo != "tiempo"


def test_cierre_10_min_antes_del_final():
    tarde = datetime(2026, 9, 25, 19, 45, tzinfo=UTC)          # 15:45 NY
    filas = [(10.0, 10.05, 9.95, 10.0)] * 10
    s = _senal(momento=tarde)
    t = motor._salida(s, _camino(filas, tarde), 83, 9.9, 10.2, CFG,
                      motor.Parametros(slippage=0.0))
    # Con el tope de tiempo más lejos que las 15:50, manda el cierre.
    assert t.motivo in ("cierre", "tiempo") and t.salida_t <= datetime(2026, 9, 25, 19, 50, tzinfo=UTC)


def test_slippage_por_lado():
    p = motor.Parametros(slippage=0.0015)
    t = motor._salida(_senal(), _camino([(10.0, 10.1, 9.95, 10.05), (10.1, 10.65, 10.05, 10.6)]),
                      83, 9.7, 10.6, CFG, p)
    assert t.llenado == pytest.approx(10.0 * 1.0015) and t.salida == pytest.approx(10.6 * 0.9985)


def test_mfe_y_mae_en_r():
    t = _trade([(10.0, 10.15, 9.85, 10.0), (10.0, 10.65, 10.0, 10.6)])
    assert t.mae_r == pytest.approx(0.5) and t.mfe_r == pytest.approx(2.0 + 0.05 / 0.3, rel=1e-3)


# ------------------------------------------------------------------ cartera


def _velas_por(senales, filas):
    return {(s.ticker, s.dia): _camino(filas, s.momento) for s in senales}


def test_limites_de_cartera_posiciones_y_entradas():
    senales = [_senal(ticker=f"T{i}", momento=T + timedelta(seconds=i)) for i in range(8)]
    res = motor.Resultado()
    motor.simular(senales, _velas_por(senales, [(10.0, 10.05, 9.95, 10.0)] * 200), CFG, P, res)
    assert len(res.trades) == 4 and res.no_entradas["max_posiciones"] == 4


def test_stop_mas_lejos_que_el_maximo_no_entra():
    res = motor.Resultado()
    s = _senal(orb_bajo=9.4)
    motor.simular([s], _velas_por([s], [(10.0, 10.05, 9.95, 10.0)]), CFG, P, res)
    assert res.trades == [] and res.no_entradas["stop_mayor_al_maximo"] == 1


def test_freno_diario_corta_las_entradas_del_resto_del_dia():
    # Cuatro stops seguidos de 0,5 % = -2 % realizado: la quinta no entra.
    senales = [_senal(ticker=f"T{i}", momento=T + timedelta(minutes=5 * i)) for i in range(6)]
    velas = {(s.ticker, s.dia): _camino([(10.0, 10.05, 9.6, 9.65)], s.momento) for s in senales}
    res = motor.Resultado()
    motor.simular(senales, velas, CFG, P, res)
    # 3 stops = -1,47 % (todavía no); el 4.º lleva a -1,96 %: la 5.ª y la 6.ª no entran.
    assert len(res.trades) == 4 and res.no_entradas["freno_diario"] == 2


def test_nivel_2_opera_a_medio_tamano():
    res = motor.Resultado()
    s1, s2 = _senal(ticker="A"), _senal(ticker="B", nivel=2, momento=T + timedelta(seconds=1))
    motor.simular([s1, s2], _velas_por([s1, s2], [(10.0, 10.05, 9.95, 10.0)] * 5), CFG, P, res)
    assert [t.cantidad for t in res.trades] == [83, 41]


# ------------------------------------------------------------------ métricas


def test_metricas_y_criterios():
    class T_:
        def __init__(self, r):
            self.r, self.pnl, self.mfe_r, self.mae_r = r, r * 25, max(r, 0) + 0.5, 0.4
    trades = [T_(2.0)] * 4 + [T_(-1.0)] * 6
    curva = [(None, 5000.0), (None, 5100.0), (None, 4950.0), (None, 5200.0)]
    m = informe.metricas(trades, curva)
    assert m["trades"] == 10 and m["acierto"] == 0.4
    assert m["expectativa_r"] == pytest.approx(0.2) and m["factor_beneficio"] == pytest.approx(8 / 6)
    assert m["drawdown_max"] == pytest.approx(150 / 5100)
    veredicto = informe.evaluar(m, informe.Criterios())
    assert [ok for _, _, ok in veredicto] == [False, False, True, True]   # 10 trades; 0,2 no es > 0,2


# --------------------------------------------------------------- clasificador


NOTICIA = Noticia("n1", "Acme wins FDA approval", "Drug approved", T, ("ACME",))


def test_clasificador_cachea_audita_y_respeta_el_tope(tmp_path):
    llamadas = []

    def llamar(system, user):
        llamadas.append(user)
        return '{"nivel": 1, "tipo": "fda", "direccion": "alcista", "confianza": 0.9}'

    res = motor.Resultado()
    c = Clasificador(tmp_path, llamar, max_llamadas=1)
    assert c.clasificar(NOTICIA, CFG, res).nivel == 1
    assert c.clasificar(NOTICIA, CFG, res).nivel == 1          # caché: no vuelve a llamar
    assert len(llamadas) == 1
    otra = Noticia("n2", "Acme", "", T, ("ACME",))
    assert c.clasificar(otra, CFG, res).motivo == "tope_de_llamadas"
    audit = [json.loads(x) for x in (tmp_path / "v2" / "clasificaciones" / "auditoria.jsonl").read_text().splitlines()]
    assert audit[0]["system"] and "Acme wins FDA approval" in audit[0]["user"] and audit[0]["respuesta"]
    # Una corrida nueva lee la caché del disco.
    assert Clasificador(tmp_path, None).clasificar(NOTICIA, CFG).nivel == 1


def test_error_de_api_no_se_cachea(tmp_path):
    def falla(system, user):
        raise RuntimeError("https://api.x/?key=secreto")
    c = Clasificador(tmp_path, falla)
    r = c.clasificar(NOTICIA, CFG, motor.Resultado())
    assert not r.valida and "secreto" not in (r.motivo or "")
    assert not (tmp_path / "v2" / "clasificaciones" / "cache.jsonl").exists()


# ------------------------------------------------------- señal (con datos falsos)


def test_senal_del_dia_con_catalizador_y_sin_veto():
    base = datetime(2026, 9, 25, 13, 30, tzinfo=UTC)
    cierres = [10.1, 10.15, 10.1, 10.05, 10.1] + [10.1] * 5 + [10.4]
    altos = [10.2] * 5 + [10.15] * 5 + [10.45]
    bajos = [10.0] * 5 + [10.05] * 5 + [10.3]
    vols = [1000.0] * 10 + [5000.0]
    velas = [Vela(base + timedelta(minutes=k), c, altos[k], bajos[k], c, vols[k]) for k, c in enumerate(cierres)]
    spy = [Vela(base + timedelta(minutes=k), 500 + k, 500.5 + k, 499.5 + k, 500.4 + k, 1e5) for k in range(11)]
    tope = motor._minutos_de_ventana(CFG, DIA)
    previos = [[100.0 * (m + 1) for m in range(tope + 1)] for _ in range(CFG.senal.rvol_dias)]
    buena = Noticia("a", "Acme wins $20M Army contract", "", base - timedelta(hours=2), ("ACME",))

    def clasificar(n):
        return cat.Clasificacion(True, 1, "contrato", "alcista", 0.9)

    res = motor.Resultado()
    s = motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [buena], clasificar,
                            lambda m: 0.001, CFG, res)
    assert s is not None and s.nivel == 1 and s.orb_bajo == 10.0
    mala = Noticia("b", "Acme prices $30M public offering", "", base - timedelta(hours=1), ("ACME",))
    res2 = motor.Resultado()
    assert motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [buena, mala], clasificar,
                               lambda m: 0.001, CFG, res2) is None
    assert res2.descartes_senal["veto"] == 1
    tardia = Noticia("c", "Acme wins contract", "", base + timedelta(hours=1), ("ACME",))
    res3 = motor.Resultado()
    assert motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [tardia], clasificar,
                               lambda m: 0.001, CFG, res3) is None     # publicada después de la señal
    assert isinstance(res3.descartes_senal, Counter)
