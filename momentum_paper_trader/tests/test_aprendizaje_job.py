"""Job nocturno (PR-C): reporte, sombra y Telegram apagado por defecto."""
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from momentum_paper_trader import aprendizaje as ap

FIX = Path(__file__).parent / "fixtures" / "memoria_34_trades.jsonl"
AHORA = datetime(2026, 10, 1, 20, 35, tzinfo=UTC)


def _base(tmp_path):
    d = tmp_path / "apr"
    d.mkdir()
    shutil.copy(FIX, d / "memoria_trades.jsonl")
    return d


def test_fixture_de_hoy_cero_propuestas_y_observaciones(tmp_path):
    d = _base(tmp_path)
    rep = ap.correr("2026-10-01", d, None, ahora=AHORA)
    assert rep["modo"] == "sombra"
    assert rep["estadisticas"]["global"]["n"] == 21
    # con 4 sesiones válidas no hay muestra para ningún K1/K2/K4
    assert all(a["knob"] == "K3_max_posiciones" for a in rep["propuestas_hoy"])
    obs = {(s["dimension"], s["valor"]) for s in rep["estadisticas"]["segmentos"] if s["estado"] == "observacion"}
    assert ("patron", "opening_range_breakout") in obs
    assert not any(s["estado"] == "propuesta" for s in rep["estadisticas"]["segmentos"])
    assert (d / "reportes" / "2026-10-01.json").exists() and (d / "reportes" / "2026-10-01.txt").exists()
    assert "SOMBRA" in rep["texto"] and "Muestra insuficiente" in rep["texto"]
    assert "memoria no actualizada" in " ".join(rep["problemas"])


def test_sin_trades_reporte_sin_muestra(tmp_path):
    rep = ap.correr("2026-10-01", tmp_path / "vacio", None, ahora=AHORA)
    assert rep["estadisticas"]["global"]["n"] == 0
    assert "sin dato" in rep["texto"]
    assert rep["sombra_en_vivo"]["combinado"]["bloqueados"] == 0


def test_sombra_en_vivo_resta_solo_bloqueados(tmp_path):
    trades = [{"ticker": "A", "creado_en": "c1", "pnl": -10.0}, {"ticker": "B", "creado_en": "c2", "pnl": 4.0},
              {"ticker": "C", "creado_en": "c3", "pnl": 2.0}]
    gates = [{"ticker": "A", "creado_en": "c1", "bloquearia": [{"knob": "K3_max_posiciones"}]},
             {"ticker": "B", "creado_en": "c2", "bloquearia": [{"knob": "K5_sin_small_caps"}, {"knob": "K3_max_posiciones"}]},
             {"ticker": "C", "creado_en": "c3", "bloquearia": []}]
    sv = ap.sombra_en_vivo(trades, gates)
    assert sv["pnl_real"] == -4.0 and sv["pnl_sombra"] == 2.0 and sv["delta"] == 6.0
    assert sv["por_knob"]["K3_max_posiciones"]["bloqueados"] == 2
    assert sv["combinado"]["ganadores_bloqueados"] == 1


def test_leer_gates_filtra_fecha_y_tolera_basura(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join([
        json.dumps({"ts": "2026-10-01T15:00:00+00:00", "tipo": "gate_sombra", "ticker": "A"}),
        json.dumps({"ts": "2026-09-30T15:00:00+00:00", "tipo": "gate_sombra", "ticker": "B"}),
        '{"tipo": "gate_sombra", roto', json.dumps({"ts": "2026-10-01T15:00:00+00:00", "tipo": "orden"})]))
    assert [g["ticker"] for g in ap.leer_gates(p, "2026-10-01")] == ["A"]
    assert ap.leer_gates(tmp_path / "no.jsonl", "2026-10-01") == []


def test_retro_k3_y_sombra_diaria_idempotente(tmp_path):
    d = _base(tmp_path)
    ap.correr("2026-10-01", d, None, ahora=AHORA)
    rep = ap.correr("2026-10-01", d, None, ahora=AHORA)
    filas = (d / "sombra_diaria.jsonl").read_text().splitlines()
    assert len(filas) == 1
    retro = rep["sombra_retro_k3"]
    assert retro and all(r["pnl_sombra"] == round(r["pnl_real"] - r["pnl_bloqueado"], 2) for r in retro)


def test_cotas_corruptas_no_proponen_y_avisan(tmp_path):
    c = tmp_path / "c.yaml"
    c.write_text("[")
    rep = ap.correr("2026-10-01", _base(tmp_path), None, ruta_cotas=c, ahora=AHORA)
    assert rep["propuestas_hoy"] == [] and any("cotas ilegibles" in p for p in rep["problemas"])


def test_telegram_apagado_por_defecto(tmp_path, monkeypatch):
    enviados = []
    from momentum_paper_trader import notify
    monkeypatch.setattr(notify, "enviar", lambda t: enviados.append(t))
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.setenv("DASH_EVENTOS", str(tmp_path / "ev.jsonl"))
    assert ap.main(["--fecha", "2026-10-01", "--salida", str(tmp_path / "x")]) == 0
    assert enviados == []
    assert ap.main(["--fecha", "2026-10-01", "--salida", str(tmp_path / "x"), "--telegram"]) == 0
    assert len(enviados) == 1


def test_no_importa_el_camino_de_ordenes():
    src = Path(ap.__file__).read_text()
    assert "colocar_orden" not in src and "cerrar_posicion" not in src and "cancelar" not in src
