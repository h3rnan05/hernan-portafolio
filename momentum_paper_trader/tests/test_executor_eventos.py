"""Eventos del panel (`dashboard.events.log_event`) dentro del ejecutor.

Son solo observabilidad. Estas pruebas fijan dos cosas:
  1. Si `log_event` falla, el ejecutor hace EXACTAMENTE lo mismo
     (mismas órdenes, mismas revisiones, mismo return).
  2. Cuando funciona, escribe lo que el panel necesita, y en dry-run no
     escribe nada.

Reutiliza los dobles de `test_executor.py` sin modificarlo."""

from __future__ import annotations

import json

import pytest

from momentum_paper_trader import estado, executor
from momentum_paper_trader.tests.test_executor import (
    AHORA,
    CFG,
    _DECISION_ENTRA,
    _DECISION_NO_ENTRA,
    _FakeAlpacaClient,
    _entrada_triggered,
    _parchear,
)

ESCENARIOS = {
    "orden_colocada": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0)),
    "ia_rechaza": dict(decision=_DECISION_NO_ENTRA, cliente=dict(cash=10_000.0)),
    "alpaca_rechaza": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0, falla_para={"RKLB"})),
    "ticker_comprometido": dict(decision=_DECISION_ENTRA,
                                cliente=dict(cash=40_000.0, posiciones=[{"symbol": "RKLB"}])),
    "mercado_cerrado": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0, mercado_abierto=False)),
    "cuenta_rota": dict(decision=_DECISION_ENTRA, cliente=dict(cash=40_000.0, cuenta_rota=True)),
}


def _correr(monkeypatch, tmp_path, escenario, dry_run=False):
    esc = ESCENARIOS[escenario]
    tmp_path.mkdir(parents=True, exist_ok=True)
    _, rev_path, enviados, _ = _parchear(monkeypatch, tmp_path, [_entrada_triggered()],
                                         decision=esc["decision"])
    client = _FakeAlpacaClient(**esc["cliente"])
    nuevas = executor.ejecutar(client, CFG, dry_run=dry_run, ahora=AHORA)
    revisiones = [(r.ticker, r.entro, r.order_id, r.cantidad, r.motivo_no_operada)
                  for r in estado.cargar(rev_path)]
    return (
        [(r.ticker, r.order_id, r.cantidad) for r in nuevas],
        list(client.ordenes_colocadas),
        revisiones,
        list(enviados),
    )


def _log_event_que_explota(*args, **kwargs):
    raise RuntimeError("disco lleno, permisos, lo que sea")


@pytest.mark.parametrize("escenario", sorted(ESCENARIOS))
def test_el_ejecutor_hace_lo_mismo_si_log_event_falla(monkeypatch, tmp_path, escenario):
    monkeypatch.setenv("DASH_EVENTOS", str(tmp_path / "normal" / "events.jsonl"))
    normal = _correr(monkeypatch, tmp_path / "a", escenario)

    monkeypatch.setattr(executor, "log_event", _log_event_que_explota)
    con_falla = _correr(monkeypatch, tmp_path / "b", escenario)

    assert con_falla == normal


def _eventos(ruta):
    if not ruta.exists():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_orden_colocada_deja_la_cinta_completa(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    _correr(monkeypatch, tmp_path, "orden_colocada")

    ev = _eventos(ruta)
    assert [e["tipo"] for e in ev] == ["rechequeo", "deteccion", "decision", "orden"]
    orden = ev[-1]
    assert orden["ticker"] == "RKLB" and orden["estado"] == "enviada" and orden["lado"] == "buy"
    assert ev[2]["entra"] is True
    # Las velas salen de la latencia e2e que telemetría ya midió; si no
    # se midió, None -- nunca un 0 inventado.
    assert orden["velas"] is None or orden["velas"] >= 0


def test_bloqueo_y_rechazo_quedan_registrados(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    _correr(monkeypatch, tmp_path, "ticker_comprometido")
    _correr(monkeypatch, tmp_path / "y", "alpaca_rechaza")

    ev = _eventos(ruta)
    bloqueos = [e for e in ev if e["tipo"] == "bloqueo_riesgo"]
    assert bloqueos and bloqueos[0]["limite"] == "ticker_comprometido"
    rechazos = [e for e in ev if e["tipo"] == "orden" and e["estado"] == "rechazada"]
    assert len(rechazos) == 1 and rechazos[0]["ticker"] == "RKLB"


def test_dry_run_no_escribe_eventos(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    _correr(monkeypatch, tmp_path, "orden_colocada", dry_run=True)
    assert not ruta.exists()


def test_log_event_nunca_lanza(monkeypatch, tmp_path):
    from dashboard.events import log_event

    # Ruta imposible (un archivo usado como carpeta) y un campo que json no sabe serializar.
    bloqueo = tmp_path / "soy_un_archivo"
    bloqueo.write_text("x")
    monkeypatch.setenv("DASH_EVENTOS", str(bloqueo / "events.jsonl"))
    log_event("orden", ticker="RKLB", raro=object())

    class _Str:
        def __str__(self):
            raise ValueError("ni siquiera str()")

    monkeypatch.setenv("DASH_EVENTOS", str(tmp_path / "ok" / "events.jsonl"))
    log_event("orden", ticker="RKLB", raro=_Str())


def test_orden_trae_latencia_completa_cuando_el_hunter_guardo_las_velas(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    e = _entrada_triggered()
    e.market_event_ts = "2026-08-11T13:57:00+00:00"   # 3 min antes de AHORA
    e.velas_desde_ruptura = 6
    _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_ENTRA)
    executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=AHORA)

    (orden,) = [x for x in _eventos(ruta) if x["tipo"] == "orden"]
    assert orden["medida"] == "ruptura_a_orden"
    assert orden["velas_desde_ruptura"] == 6
    # velas_desde_disparo sale del reloj real de la corrida, así que solo
    # se verifica la suma, no un valor fijo.
    assert orden["velas"] == round(6 + orden["velas_desde_disparo"], 1)


def test_sin_velas_desde_ruptura_la_latencia_no_se_calcula(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    e = _entrada_triggered()
    e.market_event_ts = "2026-08-11T13:57:00+00:00"
    assert e.velas_desde_ruptura is None
    _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_ENTRA)
    executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=AHORA)

    (orden,) = [x for x in _eventos(ruta) if x["tipo"] == "orden"]
    assert orden["velas"] is None


# ───────── códigos de bloqueo y capacidad llena (2026-09-23) ─────────

def _eventos_de(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    return ruta


def test_tope_de_posiciones_lleno_registra_una_capacidad_llena_y_no_evalua_candidatas(monkeypatch, tmp_path):
    """El 23/9: 5 posiciones abiertas y 8 señales disparadas produjeron 942
    bloqueos (uno por señal por tick). Ahora es UN evento por corrida, con
    motivo y hora, y ninguna candidata se evalúa (ni se consulta a la IA)."""
    from momentum_paper_trader import bloqueos
    ruta = _eventos_de(monkeypatch, tmp_path)
    entradas = [_entrada_triggered(t) for t in ("AAA", "BBB", "CCC")]
    _, rev_path, enviados, contextos = _parchear(monkeypatch, tmp_path, entradas, decision=_DECISION_ENTRA)
    client = _FakeAlpacaClient(cash=40_000.0, posiciones=[{"symbol": s} for s in ("P1", "P2", "P3", "P4", "P5")])

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []

    ev = _eventos(ruta)
    llenas = [e for e in ev if e["tipo"] == "capacidad_llena"]
    assert len(llenas) == 1
    assert llenas[0]["codigo"] == bloqueos.MAXIMO_POSICIONES and llenas[0]["limite"] == "maximo_posiciones"
    assert llenas[0]["n_pendientes"] == 3 and llenas[0]["tope"] == CFG.maximo_posiciones_abiertas
    assert "ts" in llenas[0] and "máximo de 5" in llenas[0]["motivo"]
    assert [e for e in ev if e["tipo"] == "bloqueo_riesgo"] == []
    assert contextos == [], "con la capacidad llena no se gasta ni una llamada a la IA"
    assert estado.cargar(rev_path) == [] and enviados == []   # las señales no se queman ni se avisan


def test_mercado_cerrado_y_cuenta_ilegible_son_capacidad_llena_con_codigo(monkeypatch, tmp_path):
    from momentum_paper_trader import bloqueos
    ruta = _eventos_de(monkeypatch, tmp_path)
    _correr(monkeypatch, tmp_path / "a", "mercado_cerrado")
    _correr(monkeypatch, tmp_path / "b", "cuenta_rota")
    llenas = [e for e in _eventos(ruta) if e["tipo"] == "capacidad_llena"]
    assert [e["codigo"] for e in llenas] == [bloqueos.MERCADO_CERRADO, bloqueos.DATO_FALTANTE_CUENTA]
    assert llenas[1]["codigo"] == "DATO_FALTANTE:cuenta"


def test_reloj_ilegible_es_dato_faltante_no_mercado_cerrado(monkeypatch, tmp_path):
    from momentum_paper_trader import bloqueos
    ruta = _eventos_de(monkeypatch, tmp_path)
    _parchear(monkeypatch, tmp_path, [_entrada_triggered()])
    client = _FakeAlpacaClient(cash=40_000.0, reloj_roto=True)
    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    llenas = [e for e in _eventos(ruta) if e["tipo"] == "capacidad_llena"]
    assert len(llenas) == 1 and llenas[0]["codigo"] == bloqueos.DATO_FALTANTE_RELOJ == "DATO_FALTANTE:reloj_mercado"


def test_cada_bloqueo_por_senal_trae_su_codigo(monkeypatch, tmp_path):
    from momentum_paper_trader import bloqueos
    ruta = _eventos_de(monkeypatch, tmp_path)
    _correr(monkeypatch, tmp_path / "a", "ticker_comprometido")
    b = [e for e in _eventos(ruta) if e["tipo"] == "bloqueo_riesgo"]
    assert len(b) == 1 and b[0]["codigo"] == bloqueos.TICKER_COMPROMETIDO and b[0]["limite"] == "ticker_comprometido"
    assert b[0]["codigo"] in bloqueos.CODIGOS_CONOCIDOS


def test_triggered_sin_niveles_deja_rastro_como_dato_faltante(monkeypatch, tmp_path):
    from momentum_hunter import watchlist
    from momentum_paper_trader import bloqueos
    ruta = _eventos_de(monkeypatch, tmp_path)
    e = _entrada_triggered()
    e.ultima_entrada = e.ultimo_stop = e.ultimo_objetivo = None   # nunca se cachearon niveles
    _, _, _, contextos = _parchear(monkeypatch, tmp_path, [e])
    assert executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=AHORA) == []
    b = [x for x in _eventos(ruta) if x["tipo"] == "bloqueo_riesgo"]
    assert len(b) == 1 and b[0]["codigo"] == bloqueos.DATO_FALTANTE_NIVELES == "DATO_FALTANTE:niveles"
    assert b[0]["ticker"] == "RKLB" and contextos == []
    assert e.estado == watchlist.ESTADO_TRIGGERED   # la señal no se quema: el dato puede llegar


def test_niveles_viejos_llevan_codigo_de_dato_viejo(monkeypatch, tmp_path):
    from datetime import timedelta
    from momentum_paper_trader import bloqueos
    ruta = _eventos_de(monkeypatch, tmp_path)
    _parchear(monkeypatch, tmp_path, [_entrada_triggered()])
    tarde = AHORA + timedelta(minutes=CFG.minutos_maximos_niveles + 5)
    assert executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=tarde) == []
    b = [x for x in _eventos(ruta) if x["tipo"] == "bloqueo_riesgo"]
    assert len(b) == 1 and b[0]["codigo"] == bloqueos.DATO_FALTANTE_NIVELES_VIEJOS == "DATO_FALTANTE:ultimos_niveles_ts"


def test_los_bloqueos_se_cuentan_en_la_telemetria_de_la_corrida(monkeypatch, tmp_path):
    from momentum_paper_trader import bloqueos, telemetria
    _eventos_de(monkeypatch, tmp_path)
    m = telemetria.Metricas()
    _parchear(monkeypatch, tmp_path, [_entrada_triggered("AAA"), _entrada_triggered("BBB")])
    client = _FakeAlpacaClient(cash=40_000.0, posiciones=[{"symbol": s} for s in ("P1", "P2", "P3", "P4", "P5")])
    executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA, metricas=m)
    d = m.como_dict()
    assert d["capacidad_llena"] == bloqueos.MAXIMO_POSICIONES and d["bloqueos"] == {bloqueos.MAXIMO_POSICIONES: 1}
    # Dry-run no cuenta ni escribe: no hay bloqueo real que registrar.
    m2 = telemetria.Metricas()
    executor.ejecutar(client, CFG, dry_run=True, ahora=AHORA, metricas=m2)
    assert m2.como_dict()["bloqueos"] == {} and m2.como_dict()["capacidad_llena"] is None


def test_catalogo_de_codigos_es_estable():
    from momentum_paper_trader import bloqueos
    assert bloqueos.es_dato_faltante("DATO_FALTANTE:cuenta") and not bloqueos.es_dato_faltante("MERCADO_CERRADO")
    assert bloqueos.codigo_de_evento({"codigo": "X"}) == "X"
    assert bloqueos.codigo_de_evento({"limite": "cuenta_ilegible"}) == "DATO_FALTANTE:cuenta"
    assert bloqueos.codigo_de_evento({"limite": "raro"}) == "RARO"
    assert bloqueos.codigo_de_evento({}) == "SIN_CODIGO"
    assert bloqueos.CODIGOS_GLOBALES.isdisjoint(bloqueos.CODIGOS_POR_SENAL)
