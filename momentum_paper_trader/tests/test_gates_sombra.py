"""Gates en sombra (PR-F): registran, nunca deciden, nunca lanzan."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from momentum_paper_trader import autoajuste, gates_sombra as gs, regimen

AHORA = datetime(2026, 10, 1, 17, 0, tzinfo=UTC)  # 13:00 ET


def _e(**kw):
    d = dict(ticker="ABC", creado_en="c1", ultima_entrada=3.0, ultimo_patron="orb", catalizador_tipo="fda",
             es_large_cap=False, transiciones=[{"estado": "triggered", "timestamp": "2026-10-01T15:00:00+00:00"}])
    d.update(kw)
    return NS(**d)


def test_regimen_ausente_es_cautela(tmp_path):
    r = gs.regimen_para_gate(AHORA, tmp_path)
    assert r["nivel"] == regimen.CAUTELA and "fail-closed" in r["motivos"][0]


def test_regimen_viejo_es_cautela(tmp_path):
    regimen.guardar(regimen.ruta(tmp_path), {"nivel": "NORMAL", "calculado_en": (AHORA - timedelta(hours=1)).isoformat(),
                                             "acciones_sombra": regimen.ACCIONES["NORMAL"]})
    assert gs.regimen_para_gate(AHORA, tmp_path)["nivel"] == regimen.CAUTELA


def test_evento_trae_ticker_knob_y_candidato(tmp_path):
    eventos = []
    gs.registrar(lambda t, **c: eventos.append((t, c)), _e(), NS(confianza=7), 4, AHORA, tmp_path)
    tipo, c = eventos[0]
    assert tipo == "gate_sombra" and c["modo"] == "sombra" and c["ticker"] == "ABC"
    knobs = {b["knob"] for b in c["bloquearia"]}
    assert autoajuste.K3 in knobs and autoajuste.K5 in knobs  # CAUTELA por régimen sin dato: máx 3, sin small caps
    assert c["candidato"]["espera_desde_disparo_min"] == 120.0
    assert c["candidato"]["banda_precio"] == "<5"


def test_excepcion_no_tumba_y_queda_registrada(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "evaluar", lambda *a, **k: 1 / 0)
    eventos = []
    assert gs.registrar(lambda t, **c: eventos.append((t, c)), _e(), NS(confianza=7), 1, AHORA, tmp_path) is None
    assert eventos == [("gate_sombra_error", {"ticker": "ABC", "error": "ZeroDivisionError"})]

    def evento_roto(t, **c):
        raise OSError("disco")
    gs.registrar(evento_roto, _e(), NS(confianza=7), 1, AHORA, tmp_path)  # tampoco lanza


def test_refrescar_fuera_de_horario_no_llama_red(tmp_path):
    noche = datetime(2026, 10, 1, 23, 0, tzinfo=UTC)
    assert gs.refrescar_regimen(noche, tmp_path) is None


def test_refrescar_sin_alpaca_no_llama_red(tmp_path, monkeypatch):
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    assert gs.refrescar_regimen(AHORA, tmp_path) is None


def test_refrescar_con_proveedor_guarda_y_nunca_lanza(tmp_path):
    class Rompe:
        def barras(self, *a, **k):
            raise RuntimeError("red")

        def barras_intradia(self, *a, **k):
            raise RuntimeError("red")
    r = gs.refrescar_regimen(AHORA, tmp_path, provider=Rompe())
    assert r["nivel"] == regimen.CAUTELA
    assert json.loads(regimen.ruta(tmp_path).read_text())["modo"] == "sombra"
