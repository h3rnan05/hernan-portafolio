import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dashboard import build_dashboard as bd

AHORA = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)  # viernes, sesión abierta


def cfg(tmp_path, **extra):
    base = {
        "watchlist": tmp_path / "watchlist.json",
        "eventos": tmp_path / "events.jsonl",
        "salida": tmp_path / "site",
        "vela_min": 5.0,
        "presupuesto_velas": 8.0,
        "hunter_max_min": 45.0,
        "rechequeo_max_min": 12.0,
        "tz": ZoneInfo("UTC"),
    }
    base.update(extra)
    return base


def sin_alpaca(ruta, params=None):
    return None, "sin conexión (prueba)"


def alpaca_falso(cuenta):
    def get(ruta, params=None):
        return {"/v2/account": cuenta, "/v2/positions": [], "/v2/orders": []}[ruta], None
    return get


def eventos(tmp_path, *lineas):
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(l) for l in lineas), encoding="utf-8")


def test_sin_fuentes_muestra_guion_nunca_cero(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["equity"] is None and ctx["pnl"] is None and ctx["n_pos"] is None
    assert len(ctx["problemas"]) >= 3
    html = bd.render(ctx)
    assert "$0.00" not in html
    assert "Datos incompletos" in html


def test_pnl_none_si_falta_last_equity(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "100000"}))
    assert ctx["equity"] == 100000
    assert ctx["pnl"] is None


def test_pnl_del_dia(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "101000", "last_equity": "100000"}))
    assert ctx["pnl"] == 1000
    assert round(ctx["pnl_pct"], 2) == 1.0


def test_latencia_por_timestamps_y_por_campo_velas(tmp_path):
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00Z", "tipo": "deteccion", "ticker": "AAA"},
            {"ts": "2026-09-18T14:30:00Z", "tipo": "orden", "ticker": "AAA", "estado": "enviada"},
            {"ts": "2026-09-18T14:40:00Z", "tipo": "orden", "ticker": "BBB", "estado": "enviada", "velas": 3})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["lat"] == [("AAA", 6.0), ("BBB", 3.0)]


def test_sin_vela_min_no_inventa_latencia(tmp_path):
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00Z", "tipo": "deteccion", "ticker": "AAA"},
            {"ts": "2026-09-18T14:30:00Z", "tipo": "orden", "ticker": "AAA", "estado": "enviada"})
    ctx = bd.construir(AHORA, cfg(tmp_path, vela_min=None), get=sin_alpaca)
    assert ctx["lat"] == [] and ctx["lat_mediana"] is None


def test_rechequeo_viejo_en_sesion_es_alerta(tmp_path):
    eventos(tmp_path, {"ts": "2026-09-18T14:00:00Z", "tipo": "rechequeo"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    rechequeo = next(e for e in ctx["etapas"] if e["nombre"] == "Rechequeo")
    assert rechequeo["estado"] == "alerta"


def test_motivo_del_llm_se_escapa(tmp_path):
    eventos(tmp_path, {"ts": "2026-09-18T14:00:00Z", "tipo": "decision", "ticker": "AAA",
                       "entra": False, "motivo": "<script>x</script>"})
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    assert "<script>x" not in html and "&lt;script&gt;" in html


def test_watchlist_en_varios_formatos(tmp_path):
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"tickers": [{"symbol": "AAA", "keywords": ["fda", "merger"]}, "BBB"]}))
    items, _, err = bd.leer_watchlist(ruta)
    assert err is None
    assert [i["ticker"] for i in items] == ["AAA", "BBB"]
    assert items[0]["catalizador"] == "fda, merger"


def test_lineas_corruptas_se_reportan(tmp_path):
    (tmp_path / "events.jsonl").write_text('{"ts":"2026-09-18T14:00:00Z","tipo":"rechequeo"}\nbasura\n')
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert any("1 líneas" in p for p in ctx["problemas"])


def test_escritura_atomica(tmp_path):
    destino = bd.escribir("<html></html>", tmp_path / "site")
    assert destino.read_text() == "<html></html>"
    assert not (tmp_path / "site" / ".index.html.tmp").exists()


def _entrada(ticker, estado, **extra):
    base = {"ticker": ticker, "estado": estado, "creado_en": "2026-09-18T13:40:00+00:00",
            "actualizado_en": "2026-09-18T13:40:00+00:00", "catalizador_tipo": "fda",
            "catalizador_titular": "FDA aprueba X", "es_large_cap": False, "transiciones": []}
    base.update(extra)
    return base


def test_watchlist_formato_real_del_hunter(tmp_path):
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [
        _entrada("AAA", "watching"),
        _entrada("BBB", "expired", es_large_cap=True),
        {"ticker": "CCC", "estado": "watching"},
    ]}))
    items, _, err = bd.leer_watchlist(ruta)
    assert err is None
    aaa, bbb, ccc = items
    assert aaa["cap"] == "small" and bbb["cap"] == "large"
    assert ccc["cap"] is None and ccc["catalizador"] is None  # falta el dato: no se inventa
    assert aaa["catalizador"] == "fda · FDA aprueba X"
    assert aaa["detectado"] == datetime(2026, 9, 18, 13, 40, tzinfo=timezone.utc)


def test_overlay_vps_manda_sobre_el_canonico(tmp_path):
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [_entrada("VRA", "watching")]}))
    estado = tmp_path / "state.json"
    estado.write_text(json.dumps({"schema": 1, "entries": {
        "VRA": {"estado": "expired", "actualizado_en": "2026-09-18T14:30:00+00:00"}}}))
    items, _, err = bd.leer_watchlist(ruta, estado)
    assert err is None
    assert items[0]["estado"] == "expired"
    assert items[0]["actualizado"] == datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc)


def test_overlay_ausente_no_es_error_y_corrupto_si(tmp_path):
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [_entrada("AAA", "watching")]}))
    _, _, err = bd.leer_watchlist(ruta, tmp_path / "no_existe.json")
    assert err is None
    malo = tmp_path / "state.json"
    malo.write_text("{basura")
    items, _, err = bd.leer_watchlist(ruta, malo)
    assert err and items[0]["estado"] == "watching"


def test_panel_muestra_activas_y_terminales_de_hoy(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [
        _entrada("VIEJA", "expired", actualizado_en="2026-09-10T15:00:00+00:00"),
        _entrada("HOY", "invalidated", actualizado_en="2026-09-18T14:50:00+00:00"),
        _entrada("ACTIVA", "watching", actualizado_en="2026-09-15T15:00:00+00:00"),
    ]}))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert [w["ticker"] for w in ctx["watch"]] == ["ACTIVA", "HOY"]


def test_llaves_con_nombre_del_ejecutor(monkeypatch):
    for k in ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    _, err = bd.alpaca_get("/v2/account")
    assert "ALPACA_PAPER_API_KEY" in err
