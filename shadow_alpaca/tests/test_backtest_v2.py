"""Backtest v2: ejecución simulada, cartera, métricas y clasificador. Sin red."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

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
    s = _senal(orb_bajo=9.4)   # 6 % > tope 4 %
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
            self.motivo = "objetivo" if r > 0 else "stop"
    trades = [T_(2.0)] * 4 + [T_(-1.0)] * 6
    curva = [(None, 5000.0), (None, 5100.0), (None, 4950.0), (None, 5200.0)]
    m = informe.metricas(trades, curva)
    assert m["trades"] == 10 and m["acierto"] == 0.4
    assert m["expectativa_r"] == pytest.approx(0.2) and m["factor_beneficio"] == pytest.approx(8 / 6)
    assert m["drawdown_max"] == pytest.approx(150 / 5100)
    assert m["mfe_mediana_r"] == 0.5 and m["mae_mediana_r"] == 0.4
    assert m["salidas"] == {"stop": 6, "tiempo": 0, "objetivo": 4, "breakeven": 0, "cierre": 0}
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


# ------------------------------------------------- lectura de la IA y reintento


def test_interpretar_acepta_un_solo_bloque_de_codigo_y_clasifica_la_causa():
    ok = '{"nivel": 2, "tipo": "FDA fast track", "direccion": "alcista", "confianza": 0.7}'
    assert cat.interpretar(f"```json\n{ok}\n```").nivel == 2
    assert cat.interpretar(f"```\n{ok}\n```").nivel == 2
    assert cat.interpretar(f"Claro:\n```json\n{ok}\n```").motivo == "json_invalido"
    assert cat.interpretar(f"```json\n{ok}\n```\nEspero que sirva").motivo == "json_invalido"
    assert cat.interpretar("").motivo == "vacia"
    assert cat.interpretar('{"nivel": 0, "').motivo == "truncada"
    assert cat.interpretar('{"nivel": 3, "tipo": "x", "direccion": "alcista", "confianza": 0.5}').motivo == "nivel"
    assert cat.interpretar('{"nivel": 1, "tipo": "x", "direccion": "alcista", "confianza": 1.5}').motivo == "confianza"
    assert "sin bloque de código" in cat.prompts("t", "r", CFG.catalizador)[0]
    assert "truncada" in cat.prompt_correccion("Titular: x", "truncada")


def test_clasificador_reintenta_una_vez_y_registra_la_invalida(tmp_path):
    respuestas = ['```json\n{"nivel": 1, "tipo": "fda", "direccion": "alcista", "confianza": 0.9}', "",
                  '{"nivel": 1, "tipo": "fda", "direccion": "alcista", "confianza": 0.9}']
    pedidos = []

    def llamar(system, user):
        pedidos.append(user)
        return respuestas.pop(0)

    res = motor.Resultado()
    c = Clasificador(tmp_path, llamar)
    r = c.clasificar(NOTICIA, CFG, res)
    assert not r.valida and r.motivo == "vacia" and c.reintentos == 1 and c.invalidas == 2
    assert "no se pudo leer (json_invalido)" in pedidos[1]
    base = tmp_path / "v2" / "clasificaciones"
    inv = [json.loads(x) for x in (base / "invalidas.jsonl").read_text().splitlines()]
    assert [(i["intento"], i["causa"]) for i in inv] == [(1, "json_invalido"), (2, "vacia")]
    assert not (base / "cache.jsonl").exists()          # la inválida no se cachea
    assert res.clasificaciones["reintento"] == 1 and res.clasificaciones["invalida_tras_reintento:vacia"] == 1
    # Tercera llamada (nueva corrida): responde bien, se cachea.
    assert c.clasificar(NOTICIA, CFG, res).nivel == 1 and len(pedidos) == 3
    assert Clasificador(tmp_path, None).clasificar(NOTICIA, CFG).nivel == 1


def test_clasificador_reintento_corrige_y_cachea(tmp_path):
    respuestas = ['{"nivel": 1, "', '{"nivel": 2, "tipo": "x", "direccion": "alcista", "confianza": 0.6}']
    c = Clasificador(tmp_path, lambda s, u: respuestas.pop(0))
    res = motor.Resultado()
    assert c.clasificar(NOTICIA, CFG, res).nivel == 2 and c.llamadas == 2
    assert res.clasificaciones["invalida_intento_1:truncada"] == 1 and res.clasificaciones["nivel_2"] == 1


def test_cache_vieja_con_invalidas_se_ignora_al_cargar(tmp_path):
    base = tmp_path / "v2" / "clasificaciones"
    base.mkdir(parents=True)
    clave = f"{NOTICIA.id}|{CFG.catalizador.modelo}|{CFG.catalizador.prompt_version}"
    (base / "cache.jsonl").write_text(json.dumps({"clave": clave, "clasificacion": {"valida": False, "nivel": None,
                                                  "tipo": None, "direccion": None, "confianza": None, "motivo": "json_invalido"}}) + "\n")
    c = Clasificador(tmp_path, lambda s, u: '{"nivel": 1, "tipo": "x", "direccion": "alcista", "confianza": 0.6}')
    assert c.clasificar(NOTICIA, CFG).nivel == 1 and c.llamadas == 1


# -------------------------------------------------------- embudo etapa por etapa


def _dia_de_prueba():
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
    return velas, previos, spy, buena


def test_embudo_etapa_por_etapa_cuenta_en_orden():
    velas, previos, spy, buena = _dia_de_prueba()
    ok = lambda n: cat.Clasificacion(True, 1, "contrato", "alcista", 0.9)  # noqa: E731
    res = motor.Resultado()
    s = motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [buena], ok, lambda m: 0.001, CFG, res)
    assert s is not None
    assert {k: res.embudo_etapas[k] for k in ("gap", "rvol", "vwap", "ruptura_orb", "spread", "spy", "ventana", "catalizador")} == \
        {"gap": 1, "rvol": 1, "vwap": 1, "ruptura_orb": 1, "spread": 1, "spy": 1, "ventana": 1, "catalizador": 1}
    # Spread demasiado ancho: sobrevive hasta ruptura y no más; SPY no se cuenta sin spread.
    res2 = motor.Resultado()
    assert motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [buena], ok, lambda m: 0.05, CFG, res2) is None
    assert res2.embudo_etapas["ruptura_orb"] == 1 and res2.embudo_etapas["spread"] == 0 and res2.embudo_etapas["spy"] == 0
    # Con spread ancho en las 3 primeras velas pero fino en la de la señal, la vela que pasa todo
    # cuenta hasta catalizador (y las etapas previas), nunca catalizador sin ventana.
    llamadas = []

    def spread_tardio(m):
        llamadas.append(m)
        return 0.05 if len(llamadas) <= 3 else 0.001
    res4 = motor.Resultado()
    motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [buena], ok, spread_tardio, CFG, res4)
    assert res4.embudo_etapas["catalizador"] <= res4.embudo_etapas["ventana"]
    # Sin catalizador operable: llega a ventana, no a catalizador.
    res3 = motor.Resultado()
    nivel0 = lambda n: cat.Clasificacion(True, 0, "opinion", "neutral", 0.9)  # noqa: E731
    assert motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [buena], nivel0, lambda m: 0.001, CFG, res3) is None
    assert res3.embudo_etapas["ventana"] == 1 and res3.embudo_etapas["catalizador"] == 0


def test_stop_requerido_y_etapa_stop_en_simular():
    res = motor.Resultado()
    senales = [_senal(orb_bajo=9.7), _senal(orb_bajo=9.3, ticker="B"), _senal(orb_bajo=9.65, ticker="C")]
    motor.simular(senales, {}, CFG, P, res)
    assert res.embudo_etapas["stop"] == 2 and len(res.stop_requerido) == 3
    assert [round(d, 3) for d in res.stop_descartado] == [0.07]


def test_informe_trae_embudo_etapa_por_etapa_y_distribucion_del_stop():
    res = motor.Resultado()
    res.embudo.update({"universo": 10, "gap_oficial": 5, "sin_accion_corporativa": 5, "con_noticias": 4})
    res.embudo_etapas.update({"gap": 4, "rvol": 3, "vwap": 3, "ruptura_orb": 2, "spread": 2, "spy": 2, "ventana": 2,
                              "catalizador": 1, "stop": 1})
    res.stop_requerido = [0.03, 0.07, 0.12]
    res.stop_descartado = [0.07, 0.12]
    texto = informe.markdown(res, CFG, DIA, DIA, P, informe.Criterios(), [], sesiones=3)
    assert "## Embudo etapa por etapa" in texto
    assert "| universo (símbolos × sesiones) | 30 | — |" in texto
    assert "| RVOL ≥ mínimo | 3 | 75.0% |" in texto
    assert "| stop ≤ 4% | 1 | 100.0% |" in texto
    assert "| 6%–8% | 1 | 1 |" in texto and "| ≥ 15% | 0 | 0 |" in texto
    assert "mediana 12.0%" in texto


# ------------------------------------------------------------------- variantes


def test_variantes_cambian_una_sola_cosa():
    from dataclasses import asdict

    from shadow_alpaca.backtest_v2 import variantes
    base = asdict(CFG)
    esperado = {"v1": {("riesgo", "stop_origen"): "minimo_vela_ruptura"},
                "v2": {("senal", "spread_max_pct"): 0.006},
                "v3": {("senal", "rango_apertura_fin"): time(9, 45), ("senal", "ventana_inicio"): time(9, 46)},
                "v4": {("riesgo", "stop_tiempo_minutos"): 60.0}}
    assert asdict(variantes.aplicar(CFG, "base")) == base
    for nombre, cambios in esperado.items():
        v = asdict(variantes.aplicar(CFG, nombre))
        difs = {(sec, k): v[sec][k] for sec in ("senal", "riesgo", "universo", "catalizador")
                for k in v[sec] if v[sec][k] != base[sec][k]}
        assert difs == cambios, nombre
    with pytest.raises(KeyError):
        variantes.aplicar(CFG, "v9")
    assert CFG.riesgo.stop_max_pct == 0.04 and CFG.senal.spread_max_pct == 0.003   # la base no se toca


def test_v1_usa_el_minimo_de_la_vela_de_ruptura():
    from dataclasses import replace

    from shadow_alpaca.backtest_v2 import variantes
    cfg1 = variantes.aplicar(CFG, "v1")
    s = motor.Senal("ACME", DIA, T, 10.0, 9.4, 1, 0.06, 4.0, "t", vela_bajo=9.9)
    assert motor.origen_stop(s, CFG.riesgo) == 9.4 and motor.origen_stop(s, cfg1.riesgo) == 9.9
    res = motor.Resultado()
    motor.simular([s], _velas_por([s], [(10.0, 10.05, 9.95, 10.0)] * 3), cfg1, P, res)
    assert len(res.trades) == 1 and res.trades[0].stop_inicial == pytest.approx(9.85)   # 1 % < 1,5 %: se aleja al mínimo
    res2 = motor.Resultado()
    motor.simular([s], _velas_por([s], [(10.0, 10.05, 9.95, 10.0)] * 3), CFG, P, res2)
    assert res2.trades == [] and res2.no_entradas["stop_mayor_al_maximo"] == 1   # con el ORB (6 %) no entra
    sin_vela = replace(s, vela_bajo=None)
    assert motor.origen_stop(sin_vela, cfg1.riesgo) == 9.4   # sin dato de la vela, el ORB (no se inventa)


def test_la_senal_guarda_el_minimo_de_la_vela_de_ruptura():
    velas, previos, spy, buena = _dia_de_prueba()
    res = motor.Resultado()
    s = motor.senal_del_dia("ACME", DIA, velas, previos, spy, 0.06, [buena],
                            lambda n: cat.Clasificacion(True, 1, "c", "alcista", 0.9), lambda m: 0.001, CFG, res)
    assert s.vela_bajo == 10.3 and s.orb_bajo == 10.0


def test_informe_mfe_de_salidas_por_tiempo_y_rangos_de_precio():
    res = motor.Resultado()
    for k, (motivo, mfe, precio) in enumerate([("tiempo", 0.1, 5.0), ("tiempo", 0.6, 15.0), ("tiempo", 1.2, 25.0),
                                              ("objetivo", 2.1, 8.0), ("stop", 0.2, 30.0)]):
        s = _senal(precio=precio, orb_bajo=precio * 0.97, ticker=f"T{k}")
        res.trades.append(motor.Trade(s, 10, precio, s.orb_bajo, precio * 1.06, T, precio, T, precio, motivo,
                                      2.0 if motivo == "objetivo" else -0.3, 0.0, mfe, 0.3))
    texto = informe.markdown(res, CFG, DIA, DIA, P, informe.Criterios(), [], sesiones=1, variante="v4", descripcion="stop 60")
    assert "variante v4" in texto and "**Variante `v4`:** stop 60" in texto
    assert "### MFE de los trades que salieron por tiempo (3)" in texto
    assert "| < 0.00 R | 0 |" in texto and "| 0.00–0.25 R | 1 |" in texto and "| 0.50–1.00 R | 1 |" in texto and "| 1.00–1.50 R | 1 |" in texto
    assert "| $2–10 | 2 |" in texto and "| $10–20 | 1 |" in texto and "| $20–50 | 2 |" in texto
    assert "MFE promedio / mediana" in texto
    j = informe.metricas_json(res, "v4", "stop 60")
    assert j["metricas"]["salidas"]["tiempo"] == 3 and j["mfe_tiempo"]["0.50–1.00 R"] == 1
    assert set(j["por_precio"]) == {"$2–10", "$10–20", "$20–50"} and j["por_nivel"]["nivel 1"]["trades"] == 5


def test_comparativo_de_variantes(tmp_path):
    from shadow_alpaca.backtest_v2 import comparar
    res = motor.Resultado()
    s = _senal()
    res.trades.append(motor.Trade(s, 10, 10.0, 9.7, 10.6, T, 10.0, T, 10.3, "tiempo", 1.0, 3.0, 1.2, 0.2))
    res.senales.append(s)
    base = tmp_path / "base.json"
    base.write_text(json.dumps(informe.metricas_json(res, "base", "config")), encoding="utf-8")
    v4 = tmp_path / "v4.json"
    v4.write_text(json.dumps(informe.metricas_json(motor.Resultado(), "v4", "stop 60")), encoding="utf-8")
    salida = tmp_path / "comp.md"
    assert comparar.main([str(v4), str(base), "--salida", str(salida), "--desde", "2025-09-26", "--hasta", "2026-09-25"]) == 0
    texto = salida.read_text(encoding="utf-8")
    assert texto.index("| base |") < texto.index("| v4 |")      # la base primero
    assert "| base | 1 | 100.0% | +1.00 R | ∞ | — | 0 / 1 / 0 / 0 / 0 | +1.20 R / +1.20 R | +0.20 R / +0.20 R |" in texto
    assert "| v4 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |" in texto
    assert "## MFE de los trades que salieron por tiempo" in texto and "| base | 0 | 0 | 0 | 0 | 1 | 0 | 1 |" in texto
    assert "| base $10–20 | 1 |" in texto and "| base $2–10 | 0 |" in texto and "| v4 nivel 1 | 0 |" in texto
    ajeno = tmp_path / "ajeno.json"
    ajeno.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        comparar._cargar([ajeno])
