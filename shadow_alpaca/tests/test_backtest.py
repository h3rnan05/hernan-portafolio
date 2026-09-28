"""Backtest P3: sin red, sin claves, sin escribir fuera de `tmp_path`.

Lo que más importa probar es que no se mire el futuro (velas en
formación, noticias posteriores al minuto) y que la ejecución simulada
use los mismos números que el ejecutor."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter.catalysts.detector import Titular
from momentum_hunter.models import Metadata
from momentum_hunter.tests.test_run_watchlist import _candidato_intradia
from shadow_alpaca import backtest as bt

DIA = date(2024, 3, 12)            # martes, ya en horario de verano de EE. UU.


def _ny(h, m, dia=DIA):
    return bt._en_ny(dia, time(h, m))


def _vela(t, o=10.0, h=None, low=None, c=None, v=1000.0):
    c = o if c is None else c
    return bt.Vela(t, o, h if h is not None else max(o, c), low if low is not None else min(o, c), c, v)


def _datos(minutos=None, diarias=None, titulares=None, meta=Metadata("ACME", nombre="Acme Corp")):
    if diarias is None:
        diarias = [_vela(datetime(2024, 2, 1, 5, tzinfo=UTC) + timedelta(days=i), o=50.0, v=3e6)
                   for i in range(30)]
    return bt.DatosSimbolo("ACME", minutos or [], diarias, titulares or [], meta)


# ----------------------------------------------------------- sin mirar el futuro


def test_bi_hasta_no_incluye_la_vela_en_formacion_ni_las_futuras():
    velas = [_vela(_ny(9, 30) + timedelta(minutes=i), o=10 + i) for i in range(10)]
    d = _datos(minutos=velas)
    bi = bt._bi_hasta(d, DIA, _ny(9, 35))
    # A las 9:35:00 cerraron las de 9:30..9:34; la de 9:35 recién empieza.
    assert bi.timestamps[-1] == _ny(9, 34).isoformat(timespec="seconds")
    assert len(bi.timestamps) == 5


def test_bi_hasta_sin_velas_de_hoy_no_evalua():
    ayer = [_vela(_ny(15, 0, date(2024, 3, 11)))]
    assert bt._bi_hasta(_datos(minutos=ayer), DIA, _ny(9, 35)) is None


def test_noticia_posterior_al_minuto_no_cuenta_como_catalizador():
    titular = Titular("Acme receives FDA approval for its drug", "Benzinga",
                      _ny(11, 0).isoformat(timespec="seconds"))
    d = _datos(titulares=[titular])
    c, motivo = bt._candidato_diario(d, DIA, _ny(10, 1), bt.CONFIG)
    assert c is None and motivo == "sin_catalizador"
    c, motivo = bt._candidato_diario(d, DIA, _ny(11, 1), bt.CONFIG)
    assert c is not None and c.catalizador.tipo == "fda"


def test_titular_sin_hora_no_se_ubica_en_el_dia():
    assert not bt._antes_de("2024-03-12", _ny(15, 0))


def test_sin_metadata_se_descarta_como_en_produccion():
    c, motivo = bt._candidato_diario(_datos(meta=None), DIA, _ny(10, 1), bt.CONFIG)
    assert c is None and motivo == "sin_metadata"


def test_la_vela_diaria_de_hoy_solo_suma_minutos_regulares_ya_cerrados():
    velas = [_vela(_ny(9, 0), o=9.0, v=5)] + [
        _vela(_ny(9, 30) + timedelta(minutes=i), o=10 + i, v=100) for i in range(5)]
    b = bt._barras_diarias(_datos(minutos=velas), DIA, _ny(9, 33))
    # Premarket fuera; 9:30, 9:31 y 9:32 cerradas; 9:33 en formación.
    assert b.open[-1] == 10 and b.close[-1] == 12 and b.volume[-1] == 300
    assert run_mod._cierre_anterior(b, DIA.isoformat()) == 50.0   # contrato epoch intacto


def test_reloj_simulado_se_usa_y_se_restaura():
    original = run_mod.minutos_desde_catalizador
    cat = type("C", (), {"fecha": _ny(9, 0).isoformat()})()
    reloj = {"ahora": _ny(9, 30)}
    with bt._reloj_simulado(reloj):
        assert run_mod.minutos_desde_catalizador(cat) == pytest.approx(30.0)
    assert run_mod.minutos_desde_catalizador is original


# ------------------------------------------------------------- ejecución


def _senal(momento=None, entrada=10.0, stop=9.8, objetivo=10.4):
    return bt.Senal("ACME", (momento or _ny(10, 0)).isoformat(timespec="seconds"),
                    "gap_and_go", entrada, stop, objetivo, 70.0, "fda")


def _dia_de_velas(camino):
    """camino = [(minuto_desde_10:00, o, h, l, c)]"""
    return [bt.Vela(_ny(10, 0) + timedelta(minutes=m), o, h, low, c, 1000.0) for m, o, h, low, c in camino]


def _ejecutar(velas, senal=None, p=None):
    d = _datos(minutos=velas)
    return bt.simular_ejecucion([senal or _senal()], {"ACME": d}, DIA, p or bt.ParametrosEjecucion())[0]


def test_llena_con_la_latencia_y_sale_por_objetivo():
    velas = _dia_de_velas([(1, 10.1, 10.1, 9.99, 10.05),    # antes de la orden (10:02): no cuenta
                           (2, 10.05, 10.1, 9.99, 10.0),    # llena a 10.0
                           (3, 10.0, 10.45, 9.95, 10.4)])   # toca el objetivo
    op = _ejecutar(velas)
    assert op.estado == "cerrada" and op.salida_por == "objetivo"
    assert op.llenado_ts == _ny(10, 2).isoformat(timespec="seconds")
    # 100 / 0,20 = 500 acciones, pero el tope de 15 % de $5.000 deja 75.
    assert op.cantidad == 75
    assert op.pnl == pytest.approx((10.4 - 10.0) * 75)
    assert op.r == pytest.approx(2.0)


def test_stop_y_objetivo_en_la_misma_vela_cuenta_el_stop():
    velas = _dia_de_velas([(2, 10.0, 10.0, 9.99, 10.0), (3, 10.0, 10.5, 9.7, 10.2)])
    op = _ejecutar(velas)
    assert op.salida_por == "stop" and op.precio_salida == pytest.approx(9.8)


def test_gap_por_debajo_del_stop_sale_a_la_apertura_no_al_stop():
    velas = _dia_de_velas([(2, 10.0, 10.0, 9.99, 10.0), (3, 9.5, 9.6, 9.4, 9.5)])
    assert _ejecutar(velas).precio_salida == pytest.approx(9.5)


def test_sin_tocar_el_precio_en_15_min_no_llena():
    velas = _dia_de_velas([(m, 10.3, 10.4, 10.2, 10.3) for m in range(2, 30)])
    op = _ejecutar(velas)
    assert op.estado == "sin_llenar"


def test_sin_salida_se_liquida_10_min_antes_del_cierre():
    velas = _dia_de_velas([(2, 10.0, 10.0, 9.99, 10.0)]) + [
        bt.Vela(_ny(15, 50), 10.1, 10.1, 10.1, 10.1, 1.0)]
    op = _ejecutar(velas)
    assert op.salida_por == "cierre" and op.precio_salida == pytest.approx(10.1)


def test_con_menos_de_30_min_de_sesion_no_entra():
    op = _ejecutar([], senal=_senal(momento=_ny(15, 35)))
    assert op.estado == "no_entra" and op.motivo == "cierre_cercano"


def test_precio_fuera_de_alcance_no_entra():
    op = _ejecutar([], senal=_senal(entrada=900.0, stop=890.0, objetivo=920.0))
    assert op.motivo == "tamano_cero"   # 15 % de $5.000 no compra una acción de $900


def test_parametros_iguales_al_ejecutor():
    # Única conexión permitida con el ejecutor: esta prueba (los tests no
    # cuentan para la regla de aislamiento). Si alguien cambia un límite
    # allá, el backtest deja de representarlo y esto falla.
    from momentum_paper_trader.config import CONFIG as PAPER

    p = bt.ParametrosEjecucion()
    assert p.riesgo_dolares == PAPER.riesgo_dolares_por_operacion
    assert p.pct_efectivo_por_posicion == PAPER.maximo_pct_efectivo_por_posicion
    assert p.maximo_posiciones == PAPER.maximo_posiciones_abiertas
    assert p.minutos_para_llenar == PAPER.minutos_maximos_entrada_sin_llenar
    assert p.minutos_antes_del_cierre == PAPER.minutos_antes_del_cierre
    assert p.minutos_minimos_para_entrar == PAPER.minutos_minimos_para_entrar
    assert p.minimo_acciones == PAPER.minimo_acciones


# ------------------------------------------------------ día completo y CLI


def test_simular_dia_recorre_la_maquina_de_estados_real(monkeypatch):
    """Cableado: ranura -> WATCHING -> evaluación minuto a minuto ->
    TRIGGERED una sola vez -> operación simulada."""
    velas = [_vela(_ny(9, 0) + timedelta(minutes=i), o=10.0, h=10.0, low=9.9, c=10.0) for i in range(420)]
    titular = Titular("Acme receives FDA approval for its drug", "Benzinga",
                      _ny(8, 30).isoformat(timespec="seconds"))
    d = _datos(minutos=velas, titulares=[titular])
    monkeypatch.setattr(run_mod, "_clasificar_banda_de_universo", lambda b, cfg: ("large", None))
    llamadas = []

    def _construir(ticker, *a, **kw):
        llamadas.append(ticker)
        c = _candidato_intradia(ticker, accionable=True)
        return c

    monkeypatch.setattr(run_mod, "_construir_candidato_intradia", _construir)
    inf = bt.simular_dia(DIA, [d])
    assert inf.candidatos == {"ACME": titular.texto}
    assert len(inf.senales) == 1                     # TRIGGERED es terminal
    assert inf.transiciones["ACME"] == bt.watchlist.ESTADO_TRIGGERED
    assert len(inf.operaciones) == 1


def test_correr_escribe_un_informe_por_fecha_fuera_del_hunter(tmp_path, monkeypatch):
    almacen = tmp_path / "almacen" / "ACME"
    almacen.mkdir(parents=True)
    minutos = [{"t": (_ny(9, 30) + timedelta(minutes=i)).isoformat(), "o": 10, "h": 10, "l": 10, "c": 10, "v": 1}
               for i in range(3)]
    (almacen / "1Min.jsonl").write_text("".join(json.dumps(m) + "\n" for m in minutos))
    (almacen / "1Day.jsonl").write_text("")
    salida = tmp_path / "salida"
    resumen = bt.correr(["ACME"], DIA, DIA, tmp_path / "almacen", salida)
    assert (salida / f"{DIA}.md").exists() and (salida / f"{DIA}.json").exists()
    assert resumen["dias"] == 1 and resumen["senales"] == 0
    assert "Limitaciones" in (salida / f"{DIA}.md").read_text()
    with pytest.raises(ValueError):
        bt.correr(["ACME"], DIA, DIA, tmp_path / "almacen",
                  bt.Path(bt.__file__).resolve().parents[1] / "momentum_hunter" / "x")


def test_meses_parte_el_rango_sin_huecos():
    partes = bt._meses(date(2024, 1, 15), date(2024, 3, 2))
    assert partes == [(date(2024, 1, 15), date(2024, 1, 31)), (date(2024, 2, 1), date(2024, 2, 29)),
                      (date(2024, 3, 1), date(2024, 3, 2))]
