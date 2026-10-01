"""Stop de pérdida diaria (pedido del dueño, 2026-10-01). Sin red: Alpaca,
IA y Telegram mockeados. El estado del día va a un directorio temporal."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from momentum_paper_trader import bloqueos, estado, executor, stop_diario, telemetria
from momentum_paper_trader.tests.test_executor import (
    AHORA,
    CFG,
    _entrada_triggered,
    _FakeAlpacaClient,
    _parchear,
)

LAST = 5000.0          # umbral 1 % = 50.00 USD


@pytest.fixture(autouse=True)
def _entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path / "estado"))
    for var in (stop_diario.ENV_MODO, stop_diario.ENV_PCT, stop_diario.ENV_LIQUIDAR):
        monkeypatch.delenv(var, raising=False)


def _correr(monkeypatch, tmp_path, *, equity, last_equity=LAST, ahora=AHORA, ordenes=None,
            posiciones=None, sub="a", dry_run=False, metricas=None):
    d = tmp_path / sub
    d.mkdir(parents=True, exist_ok=True)
    e = _entrada_triggered(ahora=ahora - timedelta(minutes=2))
    _, rev_path, enviados, contextos = _parchear(monkeypatch, d, [e])
    client = _FakeAlpacaClient(cash=40_000.0, equity=equity, last_equity=last_equity,
                               ordenes=ordenes, posiciones=posiciones)
    nuevas = executor.ejecutar(client, CFG, dry_run=dry_run, ahora=ahora, metricas=metricas)
    return client, nuevas, enviados, contextos


def _avisos_stop(enviados):
    return [t for t in enviados if "STOP DIARIO" in t]


# 1. Por debajo, justo en, y más allá del umbral.
@pytest.mark.parametrize("equity,bloquea", [
    (LAST - 49.99, False),     # P&L −49.99 > −50.00
    (LAST - 50.00, True),      # P&L == −umbral: cruza (≤)
    (LAST - 80.00, True),
    (LAST + 30.00, False),
])
def test_umbral_por_debajo_igual_y_por_encima(monkeypatch, tmp_path, equity, bloquea):
    client, nuevas, enviados, contextos = _correr(monkeypatch, tmp_path, equity=equity)
    if bloquea:
        assert nuevas == [] and client.ordenes_colocadas == [] and contextos == []
        assert len(_avisos_stop(enviados)) == 1
    else:
        assert len(client.ordenes_colocadas) == 1
        assert _avisos_stop(enviados) == []


# 2. last_equity ausente, nulo, no numérico o 0: bloquea, nunca se toma como 0.
@pytest.mark.parametrize("last", [None, "abc", "", "0", 0.0, "-5", "nan"])
def test_last_equity_faltante_bloquea_nunca_es_cero(monkeypatch, tmp_path, last):
    client, nuevas, enviados, _ = _correr(monkeypatch, tmp_path, equity=LAST + 100, last_equity=last)
    assert nuevas == [] and client.ordenes_colocadas == []
    ev = stop_diario.evaluar({"equity": str(LAST), "last_equity": last}, AHORA)
    assert ev.bloquea and ev.codigo == bloqueos.DATO_FALTANTE_LAST_EQUITY
    assert ev.pnl is None and ev.umbral_usd is None      # nada calculado contra un 0 inventado
    assert any("sin datos" in t for t in enviados)


# 3. equity ausente: bloquea (en el ejecutor ya era cuenta ilegible).
def test_equity_faltante_bloquea(monkeypatch, tmp_path):
    ev = stop_diario.evaluar({"last_equity": str(LAST)}, AHORA)
    assert ev.bloquea and ev.codigo == bloqueos.DATO_FALTANTE_CUENTA and ev.pnl is None
    ev2 = stop_diario.evaluar({"equity": "x", "last_equity": str(LAST)}, AHORA)
    assert ev2.bloquea and ev2.pnl is None
    ev3 = stop_diario.evaluar(None, AHORA)
    assert ev3.bloquea


# 4. Pegado: si el P&L se recupera, sigue bloqueado en la sesión.
def test_queda_pegado_aunque_el_pnl_se_recupere(monkeypatch, tmp_path):
    _correr(monkeypatch, tmp_path, equity=LAST - 60, sub="a")
    client, nuevas, _, _ = _correr(monkeypatch, tmp_path, equity=LAST + 10,
                                   ahora=AHORA + timedelta(minutes=30), sub="b")
    assert nuevas == [] and client.ordenes_colocadas == []
    ev = stop_diario.evaluar({"equity": str(LAST + 10), "last_equity": str(LAST)}, AHORA + timedelta(hours=1))
    assert ev.activo and not ev.cruzado_ahora and ev.codigo == bloqueos.PERDIDA_DIARIA


# 5. Reset en la sesión siguiente.
def test_se_resetea_en_la_sesion_siguiente(monkeypatch, tmp_path):
    _correr(monkeypatch, tmp_path, equity=LAST - 60, sub="a")
    manana = AHORA + timedelta(days=1)
    ev = stop_diario.evaluar({"equity": str(LAST - 5), "last_equity": str(LAST - 60)}, manana)
    assert not ev.activo and not ev.bloquea and ev.fecha_sesion != stop_diario.dedupe_avisos.fecha_sesion(AHORA)


# 6. Un solo Telegram por sesión.
def test_un_solo_telegram_por_sesion(monkeypatch, tmp_path):
    enviados_total = []
    for i in range(3):
        _, _, enviados, _ = _correr(monkeypatch, tmp_path, equity=LAST - 70 - i,
                                    ahora=AHORA + timedelta(minutes=i), sub=f"s{i}")
        enviados_total += _avisos_stop(enviados)
    assert len(enviados_total) == 1
    assert "−50.00 USD" in enviados_total[0] and "siguen con sus stops" in enviados_total[0]


# 7. No cierra posiciones ni toca sus patas; liquidar solo con la variable.
def _patas_de_posicion():
    # Lo que devuelve `status=open&nested=true` con una posición llena:
    # el take-profit (venta) vivo; el stop cuelga como pata.
    return [{"id": "tp-1", "symbol": "MGLD", "side": "sell", "status": "new", "filled_qty": "0",
             "type": "limit", "legs": [{"id": "sl-1", "side": "sell", "status": "held", "filled_qty": "0"}]}]


def test_no_cierra_posiciones_ni_cancela_stops_ni_tp(monkeypatch, tmp_path):
    client, *_ = _correr(monkeypatch, tmp_path, equity=LAST - 60, ordenes=_patas_de_posicion(),
                         posiciones=[{"symbol": "MGLD"}])
    assert client.ordenes_canceladas == [] and client.liquidaciones == 0


def test_liquidar_existe_pero_esta_apagado_y_con_1_liquida_una_vez(monkeypatch, tmp_path):
    client, *_ = _correr(monkeypatch, tmp_path, equity=LAST - 60, sub="off")
    assert client.liquidaciones == 0
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path / "estado2"))
    monkeypatch.setenv(stop_diario.ENV_LIQUIDAR, "1")
    c1, *_ = _correr(monkeypatch, tmp_path, equity=LAST - 60, sub="on1")
    c2, *_ = _correr(monkeypatch, tmp_path, equity=LAST - 60, sub="on2", ahora=AHORA + timedelta(minutes=1))
    assert c1.liquidaciones == 1 and c2.liquidaciones == 0


# 8. Cancela solo entradas sin llenar.
def test_cancela_solo_entradas_sin_llenar(monkeypatch, tmp_path):
    ordenes = _patas_de_posicion() + [
        {"id": "ent-1", "symbol": "AAA", "side": "buy", "status": "new", "filled_qty": "0",
         "legs": [{"id": "ent-1-sl", "side": "sell", "status": "held"}]},
        {"id": "ent-2", "symbol": "BBB", "side": "buy", "status": "accepted", "filled_qty": "0"},
        {"id": "ent-3", "symbol": "CCC", "side": "buy", "status": "partially_filled", "filled_qty": "5"},
        {"id": "ent-4", "symbol": "DDD", "side": "buy", "status": "new"},               # sin filled_qty
        {"id": "ent-5", "symbol": "EEE", "side": "buy", "status": "new", "filled_qty": "x"},
    ]
    client, _, enviados, _ = _correr(monkeypatch, tmp_path, equity=LAST - 60, ordenes=ordenes)
    assert sorted(client.ordenes_canceladas) == ["ent-1", "ent-2"]
    assert "AAA" in _avisos_stop(enviados)[0] and "BBB" in _avisos_stop(enviados)[0]
    # Sin cruzar no se cancela nada.
    client2, *_ = _correr(monkeypatch, tmp_path, equity=LAST, ordenes=ordenes, sub="b",
                          ahora=AHORA + timedelta(days=1))
    assert client2.ordenes_canceladas == []


# 9. Variables de entorno.
@pytest.mark.parametrize("raw,pct,problema", [
    (None, 1.0, False), ("1.0", 1.0, False), ("0.5", 0.5, False), ("0,75", 0.75, False),
    ("0.1", 0.1, False), ("5", 5.0, False),
    ("0.05", 1.0, True), ("6", 1.0, True), ("abc", 1.0, True), ("-1", 1.0, True), ("nan", 1.0, True),
])
def test_pct_por_entorno(monkeypatch, raw, pct, problema):
    if raw is not None:
        monkeypatch.setenv(stop_diario.ENV_PCT, raw)
    valor, p = stop_diario.leer_pct()
    assert valor == pct and (p is not None) == problema


def test_pct_invalido_usa_1_y_avisa_una_vez(monkeypatch, tmp_path):
    monkeypatch.setenv(stop_diario.ENV_PCT, "9")
    _, _, env1, _ = _correr(monkeypatch, tmp_path, equity=LAST - 10, sub="a")
    _, _, env2, _ = _correr(monkeypatch, tmp_path, equity=LAST - 10, sub="b", ahora=AHORA + timedelta(minutes=1))
    avisos = [t for t in env1 + env2 if "MOMENTUM_STOP_DIARIO_PCT" in t]
    assert len(avisos) == 1 and "1.0 %" in avisos[0]
    ev = stop_diario.evaluar({"equity": str(LAST - 50), "last_equity": str(LAST)}, AHORA)
    assert ev.pct == 1.0 and ev.bloquea


@pytest.mark.parametrize("raw,esperado", [
    (None, "enforce"), ("enforce", "enforce"), ("OBSERVAR", "observar"), ("off", "off"), ("raro", "enforce"),
])
def test_modo_por_entorno(monkeypatch, raw, esperado):
    if raw is not None:
        monkeypatch.setenv(stop_diario.ENV_MODO, raw)
    assert stop_diario.modo() == esperado


@pytest.mark.parametrize("raw,esperado", [(None, False), ("0", False), ("", False), ("1", True), ("true", True)])
def test_liquidar_por_entorno(monkeypatch, raw, esperado):
    if raw is not None:
        monkeypatch.setenv(stop_diario.ENV_LIQUIDAR, raw)
    assert stop_diario.liquidar_activado() is esperado


# 10. Modos observar y off.
def test_modo_observar_no_bloquea_ni_cancela_pero_avisa(monkeypatch, tmp_path):
    monkeypatch.setenv(stop_diario.ENV_MODO, "observar")
    ordenes = [{"id": "ent-1", "symbol": "AAA", "side": "buy", "status": "new", "filled_qty": "0"}]
    client, _, enviados, _ = _correr(monkeypatch, tmp_path, equity=LAST - 60, ordenes=ordenes)
    assert len(client.ordenes_colocadas) == 1 and client.ordenes_canceladas == []
    assert len(_avisos_stop(enviados)) == 1 and "observar" in _avisos_stop(enviados)[0]


def test_modo_off_no_mide_ni_bloquea(monkeypatch, tmp_path):
    monkeypatch.setenv(stop_diario.ENV_MODO, "off")
    client, _, enviados, _ = _correr(monkeypatch, tmp_path, equity=LAST - 60, last_equity=None)
    assert len(client.ordenes_colocadas) == 1 and _avisos_stop(enviados) == []
    assert not (tmp_path / "estado" / "stop_diario").exists()


# 11. Código conocido y global (el panel no lo marca como motivo nuevo).
def test_codigos_en_el_catalogo_global():
    for c in (bloqueos.PERDIDA_DIARIA, bloqueos.DATO_FALTANTE_LAST_EQUITY, bloqueos.DATO_FALTANTE_STOP_DIARIO):
        assert c in bloqueos.CODIGOS_GLOBALES and c in bloqueos.CODIGOS_CONOCIDOS
        assert c not in bloqueos.CODIGOS_POR_SENAL
    assert bloqueos.codigo_de_evento({"limite": "perdida_diaria"}) == bloqueos.PERDIDA_DIARIA


def test_evento_capacidad_llena_con_pnl_y_umbral(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    _correr(monkeypatch, tmp_path, equity=LAST - 60)
    ev = [json.loads(l) for l in ruta.read_text().splitlines()]
    cap = [e for e in ev if e["tipo"] == "capacidad_llena"]
    assert len(cap) == 1 and cap[0]["codigo"] == bloqueos.PERDIDA_DIARIA
    assert cap[0]["pnl"] == -60.0 and cap[0]["umbral_usd"] == 50.0
    medidas = [e for e in ev if e["tipo"] == "stop_diario"]
    assert medidas and medidas[-1]["activo"] is True and medidas[-1]["activado_en"]


# 12. Estado del día roto o imposible de escribir.
def test_estado_del_dia_ilegible_bloquea(monkeypatch, tmp_path):
    fecha = stop_diario.dedupe_avisos.fecha_sesion(AHORA)
    p = stop_diario.ruta_estado(fecha)
    p.parent.mkdir(parents=True)
    p.write_text("{no es json", encoding="utf-8")
    client, nuevas, _, _ = _correr(monkeypatch, tmp_path, equity=LAST + 10)
    assert nuevas == [] and client.ordenes_colocadas == []
    ev = stop_diario.evaluar({"equity": str(LAST), "last_equity": str(LAST)}, AHORA)
    assert ev.bloquea and ev.codigo == bloqueos.DATO_FALTANTE_STOP_DIARIO
    p.write_text(json.dumps({"activo": "si"}), encoding="utf-8")       # forma inesperada
    assert stop_diario.evaluar({"equity": str(LAST), "last_equity": str(LAST)}, AHORA).bloquea


def test_estado_imposible_de_escribir_igual_bloquea_la_corrida(monkeypatch, tmp_path):
    archivo = tmp_path / "no_es_dir"
    archivo.write_text("x")
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(archivo))
    ev = stop_diario.evaluar({"equity": str(LAST - 60), "last_equity": str(LAST)}, AHORA)
    assert ev.bloquea and ev.codigo == bloqueos.PERDIDA_DIARIA


# 13. Una posición que viene de la noche cuenta contra last_equity.
def test_posicion_overnight_cuenta_contra_last_equity(monkeypatch, tmp_path):
    # Sin ningún trade hoy: el equity ya refleja la caída de lo que se
    # arrastró (caso 24-sep, DBX). El stop corta antes de la primera entrada.
    client, nuevas, _, _ = _correr(monkeypatch, tmp_path, equity=LAST - 55,
                                   posiciones=[{"symbol": "DBX", "unrealized_intraday_pl": "-55"}])
    assert nuevas == [] and client.ordenes_colocadas == []


# 14. Dry-run: no lee la cuenta, no avisa, no escribe estado.
def test_dry_run_no_mide_no_avisa_no_escribe(monkeypatch, tmp_path):
    client, _, enviados, _ = _correr(monkeypatch, tmp_path, equity=LAST - 60, dry_run=True)
    assert client.ordenes_colocadas == [] and client.ordenes_canceladas == []
    assert _avisos_stop(enviados) == []
    assert not (tmp_path / "estado" / "stop_diario").exists()


# 15. Regresión con las sesiones reales (portfolio history 1Min de Alpaca).
def test_regresion_30_sep_corta_tem_y_deja_brx(monkeypatch, tmp_path):
    base = 4933.94          # last_equity del 30-sep; umbral 49.34
    brx = datetime(2026, 9, 30, 16, 35, tzinfo=UTC)     # P&L −37.74
    c1, n1, _, _ = _correr(monkeypatch, tmp_path, equity=4896.20, last_equity=base, ahora=brx, sub="brx")
    assert len(c1.ordenes_colocadas) == 1
    cruce = datetime(2026, 9, 30, 17, 56, tzinfo=UTC)   # P&L −49.90
    m = telemetria.Metricas()
    _correr(monkeypatch, tmp_path, equity=4884.04, last_equity=base, ahora=cruce, sub="cruce", metricas=m)
    assert m.stop_diario["activo"] and m.stop_diario["umbral_usd"] == 49.34
    assert m.como_dict()["stop_diario"]["pnl"] == -49.9
    tem = datetime(2026, 9, 30, 18, 28, tzinfo=UTC)     # P&L −51.88
    c3, n3, _, _ = _correr(monkeypatch, tmp_path, equity=4882.06, last_equity=base, ahora=tem, sub="tem")
    assert n3 == [] and c3.ordenes_colocadas == []


def test_regresion_28_sep_sigue_cortado_aunque_se_recupere(monkeypatch, tmp_path):
    base = 4950.39          # umbral 49.50
    _correr(monkeypatch, tmp_path, equity=4897.23, last_equity=base,
            ahora=datetime(2026, 9, 28, 15, 0, tzinfo=UTC), sub="cruce")       # −53.16
    # MNST 16:16: el P&L volvió a −36.09, pero el stop es de la sesión.
    c, n, _, _ = _correr(monkeypatch, tmp_path, equity=4914.30, last_equity=base,
                         ahora=datetime(2026, 9, 28, 16, 16, tzinfo=UTC), sub="mnst")
    assert n == [] and c.ordenes_colocadas == []
    # Al día siguiente (29-sep, día ganador) opera normal.
    c2, n2, _, _ = _correr(monkeypatch, tmp_path, equity=4906.0, last_equity=4907.80,
                           ahora=datetime(2026, 9, 29, 16, 18, tzinfo=UTC), sub="d29")
    assert len(c2.ordenes_colocadas) == 1


# Sin señales: se mide igual en sesión (aviso y cancelación no esperan).
def test_sin_senales_en_sesion_mide_avisa_y_cancela(monkeypatch, tmp_path):
    _, _, enviados, _ = _parchear(monkeypatch, tmp_path, [])
    ordenes = [{"id": "ent-1", "symbol": "AAA", "side": "buy", "status": "new", "filled_qty": "0"}]
    client = _FakeAlpacaClient(cash=40_000.0, equity=LAST - 60, last_equity=LAST, ordenes=ordenes)
    m = telemetria.Metricas()
    executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA, metricas=m)
    assert client.ordenes_canceladas == ["ent-1"] and len(_avisos_stop(enviados)) == 1
    assert m.stop_diario["activo"] is True
