"""Frescura al comprar (2026-10-01).

CTAS se compró 86 min después de su ruptura: esperó cupo y, al liberarse
un lugar, se compró sin volver a preguntar si la señal seguía siendo
"temprana". Estas pruebas fijan la regla: las mismas velas y la misma
extensión con las que el hunter llama "tarde" a una señal se aplican
también en el momento de la orden.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from momentum_hunter import run as run_mod
from momentum_hunter.config import CONFIG as CONFIG_HUNTER
from momentum_paper_trader import archivo, bloqueos, estado, executor
from momentum_paper_trader.config import PaperTraderConfig
from momentum_paper_trader.tests.test_executor import (
    AHORA,
    CFG,
    _entrada_triggered,
    _eventos,
    _FakeAlpacaClient,
    _parchear,
)


def _con_ruptura(ticker: str, velas_al_disparar: int, confirmada_hace_min: float):
    """TRIGGERED con niveles frescos (calculados en AHORA) cuya ruptura fue
    hace `velas_al_disparar + confirmada_hace_min` velas."""
    e = _entrada_triggered(ticker)
    e.velas_desde_ruptura = velas_al_disparar
    e.market_event_ts = (AHORA - timedelta(minutes=confirmada_hace_min)).isoformat()
    return e


def _ruta_eventos(monkeypatch, tmp_path):
    ruta = tmp_path / "ev" / "events.jsonl"
    monkeypatch.setenv("DASH_EVENTOS", str(ruta))
    return ruta


def test_los_topes_son_los_mismos_que_usa_el_hunter_para_decir_tarde():
    cfg = PaperTraderConfig()
    assert cfg.velas_maximas_desde_ruptura == CONFIG_HUNTER.velas_maximas_desde_patron
    assert cfg.extension_maxima_pct == CONFIG_HUNTER.extension_maxima_pct


def test_en_el_tope_se_compra_y_una_vela_despues_caduca(monkeypatch, tmp_path):
    tope = CFG.velas_maximas_desde_ruptura
    justo = _con_ruptura("JUSTO", velas_al_disparar=2, confirmada_hace_min=tope - 2)
    _, _, _, contextos = _parchear(monkeypatch, tmp_path, [justo])
    assert len(executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=AHORA)) == 1
    assert len(contextos) == 1

    (tmp_path / "b").mkdir()
    tarde = _con_ruptura("TARDE", velas_al_disparar=2, confirmada_hace_min=tope - 1)
    _, rev_path, _, contextos2 = _parchear(monkeypatch, tmp_path / "b", [tarde])
    client = _FakeAlpacaClient(cash=40_000.0)
    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == [] and contextos2 == []
    (r,) = estado.cargar(rev_path)
    assert r.motivo_no_operada == estado.MOTIVO_SENAL_CADUCADA
    assert r.entro is False and r.ia_entraria is None
    assert "hace 9 velas" in r.razonamiento


def test_caso_ctas_caduca_mientras_espera_cupo_sin_consultar_a_la_ia(monkeypatch, tmp_path):
    """Cupo lleno y una ruptura de hace 20 min: se registra en esta misma
    corrida, sin esperar a que se libere un lugar."""
    ruta = _ruta_eventos(monkeypatch, tmp_path)
    e = _con_ruptura("CTAS", velas_al_disparar=3, confirmada_hace_min=17)
    _, rev_path, enviados, contextos = _parchear(monkeypatch, tmp_path, [e])
    ocupadas = [{"symbol": s} for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    client = _FakeAlpacaClient(cash=10_000.0, posiciones=ocupadas)

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == [] and contextos == []
    (r,) = estado.cargar(rev_path)
    assert r.motivo_no_operada == estado.MOTIVO_SENAL_CADUCADA
    (b,) = [x for x in _eventos(ruta) if x["tipo"] == "bloqueo_riesgo"]
    assert b["codigo"] == bloqueos.SENAL_CADUCADA and b["velas"] == 20.0 and b["tope"] == 8
    # El tope de posiciones se sigue anunciando, ya sin la señal caducada.
    (lleno,) = [x for x in _eventos(ruta) if x["tipo"] == "capacidad_llena"]
    assert lleno["codigo"] == bloqueos.MAXIMO_POSICIONES and lleno["n_pendientes"] == 0


def test_la_caducada_es_terminal_y_se_archiva_con_su_motivo():
    r = estado.RevisionIA(ticker="CTAS", creado_en="x", entro=False, timestamp="y",
                          confianza=0, razonamiento="r",
                          motivo_no_operada=estado.MOTIVO_SENAL_CADUCADA)
    assert archivo.revision_es_terminal(r)
    assert archivo.desenlace_paper(r) == estado.MOTIVO_SENAL_CADUCADA


def test_con_un_solo_lugar_entra_la_senal_mas_fresca(monkeypatch, tmp_path):
    vieja = _con_ruptura("VIEJA", velas_al_disparar=4, confirmada_hace_min=3)   # 7 velas
    nueva = _con_ruptura("NUEVA", velas_al_disparar=1, confirmada_hace_min=0)   # 1 vela
    _parchear(monkeypatch, tmp_path, [vieja, nueva])
    ocupadas = [{"symbol": s} for s in ("AAA", "BBB", "CCC", "DDD")]
    client = _FakeAlpacaClient(cash=40_000.0, posiciones=ocupadas)

    executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)
    assert [o[0] for o in client.ordenes_colocadas] == ["NUEVA"]


def test_precio_extendido_no_se_persigue_pero_no_quema_la_senal(monkeypatch, tmp_path):
    ruta = _ruta_eventos(monkeypatch, tmp_path)
    e = _con_ruptura("EXT", velas_al_disparar=2, confirmada_hace_min=1)
    e.ultima_extension_pct = CFG.extension_maxima_pct + 0.03
    _, rev_path, _, contextos = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=40_000.0)

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == [] and contextos == []
    assert estado.cargar(rev_path) == []        # no es terminal: el precio puede volver
    (b,) = [x for x in _eventos(ruta) if x["tipo"] == "bloqueo_riesgo"]
    assert b["codigo"] == bloqueos.SENAL_EXTENDIDA


def test_sin_dato_de_extension_no_se_bloquea(monkeypatch, tmp_path):
    e = _con_ruptura("SINEXT", velas_al_disparar=2, confirmada_hace_min=1)
    assert e.ultima_extension_pct is None
    _parchear(monkeypatch, tmp_path, [e])
    assert len(executor.ejecutar(_FakeAlpacaClient(cash=40_000.0), CFG, dry_run=False, ahora=AHORA)) == 1


def test_marca_de_confirmacion_ilegible_es_sin_dato():
    e = SimpleNamespace(velas_desde_ruptura=2, market_event_ts="no-es-fecha")
    assert executor._velas_desde_ruptura_ahora(e, AHORA) is None
    e = SimpleNamespace(velas_desde_ruptura=True, market_event_ts=AHORA.isoformat())
    assert executor._velas_desde_ruptura_ahora(e, AHORA) is None


def test_el_hunter_guarda_la_extension_con_los_niveles():
    factores = SimpleNamespace(precio_actual=11.0, vwap=10.0, ema9=10.5)
    ctx = run_mod._contexto_niveles(SimpleNamespace(resultado=None, factores=factores))
    assert ctx["extension_pct"] == 0.1          # la mayor de las dos distancias
    sin_precio = SimpleNamespace(precio_actual=None, vwap=10.0, ema9=10.5)
    assert run_mod._contexto_niveles(SimpleNamespace(factores=sin_precio))["extension_pct"] is None
