import re
import json
import pytest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard import build_dashboard as bd

AHORA = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)  # viernes, sesión abierta


def cfg(tmp_path, **extra):
    base = {
        "watchlist": tmp_path / "watchlist.json",
        "eventos": tmp_path / "events.jsonl",
        "salida": tmp_path / "site",
        "presupuesto_velas": 8.0,
        "hunter_max_min": 45.0,
        "rechequeo_max_min": 12.0,
        "tz": ZoneInfo("UTC"),
        "telem_hunter": tmp_path / "telem",   # sin archivo: sin escaneo del VPS
    }
    base.update(extra)
    return base


def escaneo_vps(tmp_path, fin, slot=3, n_slots=8, evaluadas=(9, 3), modo="escaneo", inicio=None, extra_lineas=()):
    """Un registro de telemetría del hunter en el VPS, como lo escribe
    momentum_hunter.telemetria (archivo por fecha UTC y fuente)."""
    ruta = tmp_path / "telem" / fin.astimezone(timezone.utc).date().isoformat() / "vps" / "events.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    registro = {"timestamp": fin.isoformat(), "modo": modo, "inicio_ts": (inicio or fin).isoformat(),
                "slot": slot, "n_slots": n_slots, "universo_escaneado": 1000,
                "embudo": {"evaluadas": {"small": evaluadas[0], "large": evaluadas[1]}}, "fuente": "vps"}
    with ruta.open("a", encoding="utf-8") as f:
        for linea in extra_lineas:
            f.write(linea + "\n")
        f.write(json.dumps(registro) + "\n")
    return ruta


def sin_alpaca(ruta, params=None):
    return None, "sin conexión (prueba)"


def alpaca_falso(cuenta, **rutas_extra):
    rutas = {"/v2/account": cuenta, "/v2/positions": [], "/v2/orders": [], **rutas_extra}

    def get(ruta, params=None):
        if ruta in rutas:
            return rutas[ruta], None
        return None, f"sin datos en {ruta} (prueba)"
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


def test_latencia_solo_cuenta_la_medida_ruptura_a_orden(tmp_path):
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00Z", "tipo": "deteccion", "ticker": "AAA"},
            {"ts": "2026-09-18T14:30:00Z", "tipo": "orden", "ticker": "AAA", "estado": "enviada",
             "velas": 9.5, "medida": "ruptura_a_orden"},
            # v1 (disparo -> orden): otra medida, no entra al gráfico
            {"ts": "2026-09-18T14:40:00Z", "tipo": "orden", "ticker": "BBB", "estado": "enviada", "velas": 3},
            # falta velas_desde_ruptura: el ejecutor manda None, no 0
            {"ts": "2026-09-18T14:45:00Z", "tipo": "orden", "ticker": "CCC", "estado": "enviada",
             "velas": None, "medida": "ruptura_a_orden"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["lat"] == [("AAA", 9.5)]
    assert ctx["lat_fuera"] == 1  # 9.5 > 8


def test_sin_velas_no_se_reconstruye_por_tiempo(tmp_path):
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00Z", "tipo": "deteccion", "ticker": "AAA"},
            {"ts": "2026-09-18T14:30:00Z", "tipo": "orden", "ticker": "AAA", "estado": "enviada"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
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


def gha_ok(momento, numero=218, origen="github", error=None):
    """Actions falso: una corrida exitosa terminada en `momento`."""
    def gha():
        return {"corrida": {"numero": numero, "terminada": momento.isoformat(), "evento": "schedule",
                            "url": f"https://github.com/x/y/actions/runs/{numero}"},
                "obtenido": AHORA, "origen": origen, "error": error}
    return gha


def gha_caido(error="GitHub Actions no respondió (prueba)"):
    def gha():
        return {"corrida": None, "obtenido": None, "origen": None, "error": error}
    return gha


def _hunter(ctx):
    return next(e for e in ctx["etapas"] if e["nombre"] == "Hunter")


def test_hunter_no_usa_mtime_sin_marca_es_sin_datos(tmp_path, monkeypatch):
    # Archivo recién escrito (mtime = ahora), como tras un git pull, pero sin
    # ninguna marca de tiempo dentro ni commit conocido: no hay dato.
    monkeypatch.setattr(bd, "fecha_ultimo_commit", lambda ruta: None)
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [{"ticker": "AAA", "estado": "watching"}]}))
    _, generado, _ = bd.leer_watchlist(tmp_path / "watchlist.json")
    assert generado is None
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert _hunter(ctx)["estado"] == "sin-datos"


def test_hunter_usa_la_entrada_mas_reciente(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "fecha_ultimo_commit", lambda ruta: None)
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [
        {"ticker": "AAA", "estado": "expired", "actualizado_en": "2026-09-18T13:00:00+00:00"},
        {"ticker": "BBB", "estado": "watching", "actualizado_en": "2026-09-18T14:40:00+00:00"},
    ]}))
    _, generado, _ = bd.leer_watchlist(tmp_path / "watchlist.json")
    assert generado == datetime(2026, 9, 18, 14, 40, tzinfo=timezone.utc)
    # La watchlist es fresca, pero el estado del Hunter ya no sale de ahí:
    # sin escaneo del VPS hoy no hay dato; con uno reciente, OK.
    assert _hunter(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido()))["estado"] == "sin-datos"
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 50, tzinfo=timezone.utc))
    assert _hunter(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido()))["estado"] == "ok"


def test_hunter_usa_el_ultimo_commit_si_el_json_no_trae_hora(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "fecha_ultimo_commit",
                        lambda ruta: datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [{"ticker": "AAA"}]}))
    _, generado, _ = bd.leer_watchlist(tmp_path / "watchlist.json")
    assert generado == datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    # Último escaneo del VPS hace 3 h en plena sesión: "Revisar", no "OK".
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    assert _hunter(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido()))["estado"] == "alerta"


def test_marca_de_nivel_superior_manda(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "fecha_ultimo_commit",
                        lambda ruta: datetime(2026, 9, 18, 14, 59, tzinfo=timezone.utc))
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-18T10:00:00+00:00",
        "entradas": [{"ticker": "AAA", "actualizado_en": "2026-09-18T14:58:00+00:00"}]}))
    _, generado, _ = bd.leer_watchlist(tmp_path / "watchlist.json")
    assert generado == datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)


def test_fecha_ultimo_commit_fuera_de_git_es_none(tmp_path):
    (tmp_path / "watchlist.json").write_text("{}")
    assert bd.fecha_ultimo_commit(tmp_path / "watchlist.json") is None


def test_fecha_ultimo_commit_es_la_del_commit_que_toco_el_archivo(tmp_path):
    import os
    import subprocess

    def git(*args, fecha):
        env = {**os.environ, "GIT_AUTHOR_DATE": fecha, "GIT_COMMITTER_DATE": fecha}
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=tmp_path, env=env, check=True, capture_output=True)

    git("init", "-q", fecha="2026-09-18T12:00:00+00:00")
    (tmp_path / "watchlist.json").write_text("{}")
    git("add", "watchlist.json", fecha="2026-09-18T12:00:00+00:00")
    git("commit", "-qm", "hunter", fecha="2026-09-18T12:00:00+00:00")
    (tmp_path / "otro.txt").write_text("x")
    git("add", "otro.txt", fecha="2026-09-18T14:59:00+00:00")
    git("commit", "-qm", "otro", fecha="2026-09-18T14:59:00+00:00")
    # El commit más nuevo no tocó la watchlist: cuenta el de las 12:00.
    assert bd.fecha_ultimo_commit(tmp_path / "watchlist.json") == datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


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


def test_overlay_viejo_no_resucita_una_entrada_ya_expirada(tmp_path):
    # SUNB, 2026-09-11: el rechequeo del VPS se apagó con SUNB en
    # "watching" y GHA la expiró al día siguiente. El panel mostraba el
    # overlay congelado. Mismas reglas que el ejecutor: el canónico
    # terminal gana.
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [_entrada(
        "SUNB", "expired", creado_en="2026-09-11T20:40:49+00:00",
        actualizado_en="2026-09-12T20:33:12+00:00")]}))
    estado = tmp_path / "state.json"
    estado.write_text(json.dumps({"schema": 1, "entries": {"SUNB": {
        "estado": "watching", "actualizado_en": "2026-09-11T20:40:49+00:00",
        "overlay_ts": "2026-09-11T20:45:00+00:00"}}}))
    items, _, err = bd.leer_watchlist(ruta, estado)
    assert err is None
    assert items[0]["estado"] == "expired"
    assert items[0]["actualizado"] == datetime(2026, 9, 12, 20, 33, 12, tzinfo=timezone.utc)


def test_canonico_terminal_gana_aunque_el_overlay_sea_mas_nuevo(tmp_path):
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [_entrada("AAA", "invalidated")]}))
    estado = tmp_path / "state.json"
    estado.write_text(json.dumps({"schema": 1, "entries": {"AAA": {
        "estado": "watching", "actualizado_en": "2026-09-18T14:30:00+00:00"}}}))
    items, _, _ = bd.leer_watchlist(ruta, estado)
    assert items[0]["estado"] == "invalidated"


def test_overlay_mas_viejo_que_el_canonico_no_pisa(tmp_path):
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [_entrada(
        "AAA", "watching", actualizado_en="2026-09-18T14:00:00+00:00")]}))
    estado = tmp_path / "state.json"
    estado.write_text(json.dumps({"schema": 1, "entries": {"AAA": {
        "estado": "missed", "actualizado_en": "2026-09-18T13:00:00+00:00",
        "overlay_ts": "2026-09-18T13:00:00+00:00"}}}))
    items, _, _ = bd.leer_watchlist(ruta, estado)
    assert items[0]["estado"] == "watching"


def test_ticker_repetido_cada_entrada_con_su_estado(tmp_path):
    # La EXPIRED vieja y el intento nuevo del mismo ticker conviven en el
    # canónico: el overlay no convierte la vieja en "watching".
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [
        _entrada("AAA", "expired", creado_en="2026-09-17T14:00:00+00:00",
                 actualizado_en="2026-09-17T16:10:00+00:00"),
        _entrada("AAA", "watching", creado_en="2026-09-18T13:40:00+00:00"),
    ]}))
    estado = tmp_path / "state.json"
    estado.write_text(json.dumps({"schema": 1, "entries": {"AAA": {
        "estado": "watching", "actualizado_en": "2026-09-18T13:40:00+00:00",
        "overlay_ts": "2026-09-18T14:45:00+00:00"}}}))
    items, _, _ = bd.leer_watchlist(ruta, estado)
    assert [i["estado"] for i in items] == ["expired", "watching"]


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


def test_sin_log_de_eventos_ejecutor_y_riesgo_son_sin_datos_no_cero(tmp_path):
    # No existe events.jsonl: no se sabe cuántas decisiones o bloqueos hubo.
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    etapas = {e["nombre"]: e for e in ctx["etapas"]}
    for nombre in ("Ejecutor", "Riesgo"):
        assert etapas[nombre]["estado"] == "sin-datos", nombre
        assert "—" in etapas[nombre]["detalle"] and "0 " not in etapas[nombre]["detalle"], nombre
    html = bd.render(ctx)
    assert "0 decisiones" not in html and "0 bloqueos" not in html
    assert "Ningún límite ha bloqueado" not in html
    assert "no ha rechazado entradas" not in html


def test_con_rechequeo_reciente_cero_es_real(tmp_path):
    # Log presente y el bot corrió hace 5 min sin bloquear nada: 0 es un dato.
    eventos(tmp_path, {"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = next(e for e in ctx["etapas"] if e["nombre"] == "Riesgo")
    assert riesgo["estado"] == "ok" and riesgo["detalle"] == "0 bloqueos únicos (0 eventos) hoy"
    assert "Ningún límite ha bloqueado" in bd.render(ctx)


def _etapas(ctx):
    return {e["nombre"]: e for e in ctx["etapas"]}


def test_log_sin_rechequeo_ejecutor_y_riesgo_sin_datos(tmp_path):
    # El archivo existe pero no hay ningún rechequeo hoy: Rechequeo "Sin datos".
    (tmp_path / "events.jsonl").write_text("")
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    et = _etapas(ctx)
    assert et["Rechequeo"]["estado"] == "sin-datos"
    for nombre in ("Ejecutor", "Riesgo"):
        assert et[nombre]["estado"] == "sin-datos", nombre
        assert "—" in et[nombre]["detalle"] and "0 " not in et[nombre]["detalle"], nombre
    html = bd.render(ctx)
    assert "Ningún límite ha bloqueado" not in html and "no ha rechazado entradas" not in html
    assert "no hay un rechequeo reciente" in html


def test_rechequeo_viejo_en_sesion_ejecutor_y_riesgo_sin_datos(tmp_path):
    # Último rechequeo hace 60 min en plena sesión: Rechequeo "Revisar".
    eventos(tmp_path, {"ts": "2026-09-18T14:00:00Z", "tipo": "rechequeo"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    et = _etapas(ctx)
    assert et["Rechequeo"]["estado"] == "alerta"
    assert et["Ejecutor"]["estado"] == "sin-datos"
    assert et["Riesgo"]["estado"] == "sin-datos"
    assert et["Riesgo"]["detalle"] == "— sin datos recientes"


def test_persist_fallido_se_ve_en_rojo_en_cabecera_y_en_rechequeo(tmp_path):
    # El VPS corrió hace 5 min (rechequeo fresco) pero no pudo subir su
    # estado a main dos veces: alerta aunque la corrida sea reciente.
    eventos(tmp_path,
            {"ts": "2026-09-18T14:40:00Z", "tipo": "persist_fallido", "motivo": "git persist failed", "intentos": 5},
            {"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:56:00Z", "tipo": "persist_fallido", "motivo": "flock <timeout>", "intentos": 0})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert [p["motivo"] for p in ctx["persist_fallidos"]] == ["git persist failed", "flock <timeout>"]
    rechequeo = _etapas(ctx)["Rechequeo"]
    assert rechequeo["estado"] == "alerta"
    assert "2 persist fallidos, último 14:56" in rechequeo["detalle"]
    html = bd.render(ctx)
    assert '<span class="pildora mal">Persist fallido ×2 · último 14:56 (flock &lt;timeout&gt;)</span>' in html
    assert "flock <timeout>" not in html   # el motivo viene de fuera: escapado


def test_sin_persist_fallido_no_hay_pildora_ni_alerta(tmp_path):
    eventos(tmp_path, {"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["persist_fallidos"] == []
    assert _etapas(ctx)["Rechequeo"]["estado"] == "ok"
    assert "Persist fallido" not in bd.render(ctx)


def test_persist_fallido_de_ayer_no_cuenta_hoy(tmp_path):
    # El panel es del día: un fallo de ayer ya lo vio (o lo vio el Telegram).
    eventos(tmp_path, {"ts": "2026-09-17T14:40:00Z", "tipo": "persist_fallido", "motivo": "git persist failed"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["persist_fallidos"] == [] and "Persist fallido" not in bd.render(ctx)


def test_ia_sin_credito_se_ve_en_rojo_sin_pisar_el_persist(tmp_path):
    # Rechequeo fresco: el bot corrió. El saldo de la IA es otra alerta,
    # en el ejecutor, y convive con un persist fallido si los dos pasan.
    eventos(tmp_path,
            {"ts": "2026-09-18T14:50:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:51:00Z", "tipo": "persist_fallido",
             "motivo": "git persist failed", "intentos": 5},
            {"ts": "2026-09-18T14:52:00Z", "tipo": "ia_fallo_tecnico",
             "codigo": "credito", "motivo": "saldo <Anthropic>", "consecutivos": 1},
            {"ts": "2026-09-18T14:57:00Z", "tipo": "ia_fallo_tecnico",
             "codigo": "credito", "motivo": "saldo <Anthropic>", "consecutivos": 2})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert [f["codigo"] for f in ctx["ia_fallos"]] == ["credito", "credito"]
    assert len(ctx["persist_fallidos"]) == 1
    ejecutor = _etapas(ctx)["Ejecutor"]
    assert ejecutor["estado"] == "alerta"
    assert "2 fallos de IA, último 14:57" in ejecutor["detalle"]
    assert _etapas(ctx)["Rechequeo"]["estado"] == "alerta"
    html = bd.render(ctx)
    assert '<span class="pildora mal">IA sin crédito ×2 · último 14:57 (saldo &lt;Anthropic&gt;)</span>' in html
    assert "saldo <Anthropic>" not in html
    assert "Persist fallido ×1" in html


def test_ia_fallo_tecnico_sin_credito_usa_la_otra_pildora(tmp_path):
    eventos(tmp_path,
            {"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:56:00Z", "tipo": "ia_fallo_tecnico",
             "codigo": "api", "motivo": "consulta a la IA falló", "consecutivos": 3})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    html = bd.render(ctx)
    assert "IA fallo técnico ×1" in html
    assert "IA sin crédito" not in html
    assert _etapas(ctx)["Ejecutor"]["estado"] == "alerta"
    assert _etapas(ctx)["Rechequeo"]["estado"] == "ok"


def test_ia_fallo_de_ayer_no_cuenta_hoy(tmp_path):
    eventos(tmp_path, {"ts": "2026-09-17T14:40:00Z", "tipo": "ia_fallo_tecnico",
                       "codigo": "credito", "motivo": "saldo Anthropic insuficiente"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["ia_fallos"] == [] and "IA sin crédito" not in bd.render(ctx)


def test_rechequeo_viejo_no_oculta_un_bloqueo_real(tmp_path):
    # Un bloqueo registrado es un hecho: sigue en el historial. Sin un
    # ciclo reciente la tarjeta no se pone verde (no sabemos si sigue)
    # ni roja (el límite conocido no pide Revisar).
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:01:00Z", "tipo": "bloqueo_riesgo", "ticker": "AAA",
             "limite": "maximo_posiciones"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "sin-datos"
    assert riesgo["detalle"] == "— sin datos recientes"
    assert ctx["riesgo"]["revisar"] is False
    html = bd.render(ctx)
    assert "MAXIMO_POSICIONES" in html and "historial" in html and "resuelto" in html
    assert '<div class="nota">' not in html


def test_con_rechequeo_reciente_ejecutor_con_cero_decisiones_es_ok(tmp_path):
    # Mismo criterio que Riesgo con 0 bloqueos: log presente y rechequeo
    # hace 5 min sin ninguna decisión es un dato, no "Sin datos".
    eventos(tmp_path, {"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    et = _etapas(ctx)
    assert et["Ejecutor"]["estado"] == "ok"
    assert et["Ejecutor"]["detalle"].startswith("0 decisiones hoy")
    assert et["Riesgo"]["estado"] == "ok"  # los dos con el mismo criterio
    html = bd.render(ctx)
    assert "0 decisiones hoy" in html
    assert "no ha rechazado entradas" in html


def test_hora_muestra_el_dia_si_no_es_de_hoy():
    utc = ZoneInfo("UTC")
    assert bd._hora(datetime(2026, 9, 18, 14, 40, tzinfo=timezone.utc), utc, ahora=AHORA) == "14:40"
    assert bd._hora(datetime(2026, 9, 17, 22, 33, tzinfo=timezone.utc), utc, ahora=AHORA) == "jue 22:33"
    assert bd._hora(datetime(2026, 9, 12, 9, 5, tzinfo=timezone.utc), utc, ahora=AHORA) == "sáb 09:05"
    assert bd._hora(datetime(2026, 9, 1, 22, 33, tzinfo=timezone.utc), utc, ahora=AHORA) == "1 sep 22:33"
    assert bd._hora(None, utc, ahora=AHORA) == "—"


def test_el_dia_se_calcula_en_la_zona_del_panel():
    # 02:00 UTC del 18 = 20:00 del 17 en Monterrey: para el panel es "ayer".
    mty = ZoneInfo("America/Monterrey")
    ahora = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
    assert bd._hora(datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc), mty, ahora=ahora) == "jue 20:00"


def test_hunter_y_generada_muestran_el_dia_de_una_watchlist_vieja(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-17T22:33:00+00:00",
        "entradas": [_entrada("AAA", "watching", creado_en="2026-09-16T14:00:00+00:00")]}))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido())
    hunter = next(e for e in ctx["etapas"] if e["nombre"] == "Hunter")
    assert hunter["detalle"] == "sin escaneo del VPS hoy · watchlist del jue 22:33"
    html = bd.render(ctx)
    assert "generada jue 22:33" in html
    assert "mié 14:00" in html   # columna Detectado de la tabla


def test_hunter_de_hoy_sigue_sin_dia(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-18T14:40:00+00:00", "entradas": []}))
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 40, tzinfo=timezone.utc))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido())
    hunter = next(e for e in ctx["etapas"] if e["nombre"] == "Hunter")
    assert hunter["detalle"] == "escaneo VPS de las 14:40 · tanda 3 de 8 · 12 evaluadas · watchlist de las 14:40"


# ───────────────────────── Hunter: última corrida en GitHub Actions ─────────────────────────

from dashboard import gha as dg  # noqa: E402


def test_hunter_corrio_bien_sin_cambiar_la_watchlist_es_ok(tmp_path):
    # El caso real: watchlist vieja, escaneo del VPS fresco con 0 candidatos.
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-17T22:33:00+00:00", "entradas": []}))
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 52, tzinfo=timezone.utc), evaluadas=(0, 0))
    # GitHub corrió hace 14 min: es un dato al lado, no una alerta (la
    # alerta de "más de 45 min sin correr en sesión" se prueba aparte).
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca,
                       gha=gha_ok(datetime(2026, 9, 18, 14, 46, tzinfo=timezone.utc)))
    hunter = _hunter(ctx)
    assert hunter["estado"] == "ok" and hunter["donde"] == "VPS"
    assert hunter["detalle"] == "escaneo VPS de las 14:52 · tanda 3 de 8 · 0 evaluadas · watchlist del jue 22:33"
    assert hunter["nota"] == "respaldo en GitHub (no se usa): corrida #218 de las 14:46"
    assert ctx["hunter_momento"] == datetime(2026, 9, 18, 14, 52, tzinfo=timezone.utc)


def test_detalle_del_hunter_muestra_dia_y_hora_en_dash_tz(tmp_path):
    # Caso real: watchlist del vie 18 sep 22:33 UTC, escaneo del lun 21 sep
    # 13:46 UTC, panel en Monterrey (UTC-6) el lunes a las 07:55.
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-18T22:33:00+00:00", "entradas": []}))
    lunes = datetime(2026, 9, 21, 13, 55, tzinfo=timezone.utc)
    escaneo_vps(tmp_path, datetime(2026, 9, 21, 13, 46, 8, tzinfo=timezone.utc))
    ctx = bd.construir(lunes, cfg(tmp_path, tz=ZoneInfo("America/Monterrey")), get=sin_alpaca, gha=gha_caido())
    assert _hunter(ctx)["detalle"] == "escaneo VPS de las 07:46 · tanda 3 de 8 · 12 evaluadas · watchlist del vie 16:33"
    assert "generada vie 16:33" in bd.render(ctx)


def test_sin_escaneo_del_vps_es_sin_datos_aunque_github_y_la_watchlist_sean_frescos(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-18T14:55:00+00:00", "entradas": []}))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca,
                       gha=gha_ok(datetime(2026, 9, 18, 14, 56, tzinfo=timezone.utc)))
    assert _hunter(ctx)["estado"] == "sin-datos" and ctx["hunter_momento"] is None
    assert _hunter(ctx)["detalle"] == "sin escaneo del VPS hoy · watchlist de las 14:55"
    assert _hunter(ctx)["nota"] == "respaldo en GitHub (no se usa): corrida #218 de las 14:56"
    assert "Sin datos" in bd.render(ctx)


def test_un_escaneo_de_ayer_no_cuenta_hoy(tmp_path):
    escaneo_vps(tmp_path, datetime(2026, 9, 17, 19, 50, tzinfo=timezone.utc))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido())
    assert ctx["hunter_escaneo"] is None and _hunter(ctx)["estado"] == "sin-datos"


def test_ultimo_escaneo_ignora_rechequeos_y_lineas_rotas_y_toma_el_mas_reciente(tmp_path):
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 10, tzinfo=timezone.utc), slot=1,
                extra_lineas=("no es json", json.dumps({"timestamp": "2026-09-18T14:58:00+00:00", "modo": "watchlist"})))
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 40, tzinfo=timezone.utc), slot=2, evaluadas=(4, 1))
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc), slot=9)   # más viejo, escrito después
    ultimo = bd.ultimo_escaneo_vps(tmp_path / "telem", AHORA)
    assert ultimo["fin"] == datetime(2026, 9, 18, 14, 40, tzinfo=timezone.utc)
    assert ultimo["slot"] == 2 and ultimo["n_slots"] == 8 and ultimo["evaluadas"] == 5
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 45, tzinfo=timezone.utc), modo="watchlist")
    assert bd.ultimo_escaneo_vps(tmp_path / "telem", AHORA)["fin"] == datetime(2026, 9, 18, 14, 40, tzinfo=timezone.utc)
    assert bd.ultimo_escaneo_vps(None, AHORA) is None


def test_actions_caido_no_cambia_el_estado_del_hunter_ni_es_un_problema(tmp_path):
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 52, tzinfo=timezone.utc))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido())
    assert _hunter(ctx)["estado"] == "ok"
    assert not any("GitHub" in p for p in ctx["problemas"])
    assert "GitHub #" not in _hunter(ctx)["detalle"]


def test_sin_repo_configurado_no_se_pregunta_a_github(tmp_path, monkeypatch):
    def explota(*a, **k):
        raise AssertionError("no debía haber petición")
    monkeypatch.setattr(dg.requests, "get", explota)
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)   # cfg de prueba: sin gha_repo
    assert ctx["hunter_gha"]["corrida"] is None and _hunter(ctx)["estado"] == "sin-datos"


def test_el_panel_respeta_la_pausa_de_yahoo_del_bot_sin_escribirla(tmp_path):
    pausa_bot = tmp_path / "yahoo_pausa_bot.json"
    pausa_bot.write_text(json.dumps({"hasta": (AHORA + timedelta(minutes=10)).isoformat(), "motivo": "429", "origen": "bot"}))
    def explota(ticker):
        raise AssertionError("con la pausa del bot activa no se pide a Yahoo")
    r = dv.obtener("AAA", AHORA, tmp_path / "cache", ttl_seg=120, fuente=explota, pausa_bot=pausa_bot)
    assert r["velas"] is None and "el bot está en pausa con Yahoo" in r["error"] and "15:10 UTC" in r["error"]
    assert not (tmp_path / "cache" / dv.ARCHIVO_PAUSA).exists()   # el panel no copia la pausa a su archivo
    # Vencida: se pide normalmente. Y sin archivo, igual.
    pausa_bot.write_text(json.dumps({"hasta": (AHORA - timedelta(minutes=1)).isoformat()}))
    r = dv.obtener("AAA", AHORA, tmp_path / "cache", ttl_seg=120, fuente=lambda t: _velas(), pausa_bot=pausa_bot)
    assert r["origen"] == "fuente"
    r = dv.obtener("AAA", AHORA, tmp_path / "cache2", ttl_seg=120, fuente=lambda t: _velas(), pausa_bot=tmp_path / "no-existe.json")
    assert r["origen"] == "fuente"


def _cuerpo_runs(*runs):
    return {"total_count": len(runs), "workflow_runs": list(runs)}


def _run(numero, updated_at, conclusion="success", event="schedule"):
    return {"run_number": numero, "updated_at": updated_at, "conclusion": conclusion,
            "event": event, "html_url": f"https://github.com/x/y/actions/runs/{numero}"}


def test_parsear_corrida_toma_la_exitosa_y_exige_los_campos():
    cuerpo = _cuerpo_runs(_run(219, "2026-09-18T15:00:00Z", conclusion="failure"),
                          _run(218, "2026-09-18T14:52:00Z"))
    assert dg.parsear_corrida(cuerpo) == {"numero": 218, "terminada": "2026-09-18T14:52:00Z",
                                          "evento": "schedule", "url": "https://github.com/x/y/actions/runs/218"}
    assert dg.parsear_corrida(_cuerpo_runs()) is None
    assert dg.parsear_corrida(_cuerpo_runs({"conclusion": "success", "updated_at": "2026-09-18T14:52:00Z"})) is None
    assert dg.parsear_corrida("basura") is None and dg.parsear_corrida({"workflow_runs": "x"}) is None


def test_fuente_github_es_un_get_publico_sin_token_y_una_sola_vez(monkeypatch):
    llamadas = []

    def get(url, params=None, headers=None, timeout=None):
        llamadas.append((url, params, headers, timeout))
        return _Respuesta(200, _cuerpo_runs(_run(218, "2026-09-18T14:52:00Z")))
    monkeypatch.setattr(dg.requests, "get", get)
    corrida = dg.fuente_github("h3rnan05/hernan-portafolio", "momentum_hunter.yml")
    assert corrida["numero"] == 218
    assert len(llamadas) == 1
    url, params, headers, timeout = llamadas[0]
    assert url == "https://api.github.com/repos/h3rnan05/hernan-portafolio/actions/workflows/momentum_hunter.yml/runs"
    assert params == {"status": "success", "per_page": 1, "exclude_pull_requests": "true"}
    assert "Authorization" not in headers and timeout is not None


def test_fuente_github_403_o_429_es_limite(monkeypatch):
    for codigo in (403, 429):
        monkeypatch.setattr(dg.requests, "get", lambda *a, **k: _Respuesta(codigo, {}))
        with pytest.raises(dg.LimiteDePeticiones):
            dg.fuente_github("x/y", "w.yml")


def test_obtener_cache_vigente_no_pregunta(tmp_path):
    corrida = {"numero": 218, "terminada": "2026-09-18T14:52:00Z", "evento": "schedule", "url": "u"}
    (tmp_path / dg.ARCHIVO).write_text(json.dumps({"obtenido": (AHORA - timedelta(seconds=60)).isoformat(),
                                                    "corrida": corrida}))
    def explota(*a):
        raise AssertionError("no debía preguntar")
    res = dg.obtener("x/y", "w.yml", AHORA, tmp_path, ttl_seg=300, fuente=explota)
    assert res["corrida"] == corrida and res["origen"] == "cache" and res["error"] is None


def test_obtener_api_caida_sirve_la_copia_vencida_con_el_error(tmp_path):
    corrida = {"numero": 218, "terminada": "2026-09-18T14:52:00Z", "evento": "schedule", "url": "u"}
    (tmp_path / dg.ARCHIVO).write_text(json.dumps({"obtenido": (AHORA - timedelta(seconds=900)).isoformat(),
                                                    "corrida": corrida}))
    def caida(*a):
        raise ConnectionError("sin red")
    res = dg.obtener("x/y", "w.yml", AHORA, tmp_path, ttl_seg=300, fuente=caida)
    assert res["corrida"] == corrida and res["origen"] == "cache vencida"
    assert res["error"] == "GitHub Actions no respondió (ConnectionError)"
    # Sin copia: nada, con el error.
    res = dg.obtener("x/y", "w.yml", AHORA, tmp_path / "vacia", ttl_seg=300, fuente=caida)
    assert res["corrida"] is None and res["origen"] is None and "no respondió" in res["error"]


def test_obtener_limite_pausa_y_no_vuelve_a_preguntar(tmp_path):
    llamadas = []

    def limitada(*a):
        llamadas.append(1)
        raise dg.LimiteDePeticiones("403")
    res = dg.obtener("x/y", "w.yml", AHORA, tmp_path, ttl_seg=300, fuente=limitada, pausa_seg=900)
    assert res["corrida"] is None and "limitó" in res["error"] and "15:15 UTC" in res["error"]
    assert dg.pausa_hasta(tmp_path) == AHORA + timedelta(seconds=900)
    # Dentro de la pausa no se pregunta, aunque la fuente esté viva.
    res = dg.obtener("x/y", "w.yml", AHORA + timedelta(seconds=120), tmp_path, ttl_seg=300, fuente=limitada)
    assert len(llamadas) == 1 and "limitó" in res["error"]


def test_obtener_respuesta_valida_se_cachea(tmp_path):
    corrida = {"numero": 218, "terminada": "2026-09-18T14:52:00Z", "evento": "schedule", "url": "u"}
    res = dg.obtener("x/y", "w.yml", AHORA, tmp_path, ttl_seg=300, fuente=lambda r, w: corrida)
    assert res["origen"] == "github" and res["corrida"] == corrida
    guardado = json.loads((tmp_path / dg.ARCHIVO).read_text())
    assert guardado["corrida"] == corrida and guardado["obtenido"] == AHORA.isoformat()
    # Una respuesta sin corrida no pisa la copia buena.
    res = dg.obtener("x/y", "w.yml", AHORA + timedelta(seconds=600), tmp_path, ttl_seg=300, fuente=lambda r, w: None)
    assert res["corrida"] == corrida and res["origen"] == "cache vencida" and "ninguna corrida" in res["error"]


# ───────────────────────── curva de equity ─────────────────────────

HIST = bd.RUTA_HISTORIAL


def _historial(equity, timestamps=None, base=None, inicio=None):
    # Por defecto desde las 10:00 de Nueva York del mismo viernes de AHORA
    # (sesión abierta, puntos ya ocurridos).
    inicio = inicio or datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    ts = timestamps or [int(inicio.timestamp()) + 300 * i for i in range(len(equity))]  # cada 5 min
    return {"timestamp": ts, "equity": equity, "base_value": base, "timeframe": "5Min"}


def _svgs_equity(html):
    import re
    return re.findall(r'<svg viewBox="0 0 480 230" role="img" aria-label="Equity[^"]*">.*?</svg>', html)


def test_equity_alpaca_caido_muestra_sin_datos_y_ninguna_linea(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["equity_dia"]["puntos"] == [] and ctx["equity_mes"]["puntos"] == []
    svgs = _svgs_equity(bd.render(ctx))
    assert len(svgs) == 2
    for svg in svgs:
        assert "Sin datos" in svg and "Alpaca no respondió" in svg
        assert "<polyline" not in svg


def test_equity_historial_vacio_es_sin_datos_no_cero(tmp_path):
    get = alpaca_falso({"equity": "5000"}, **{HIST: {"timestamp": [], "equity": [], "base_value": 0}})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["equity_dia"]["puntos"] == [] and ctx["equity_dia"]["base"] is None
    svg = _svgs_equity(bd.render(ctx))[0]
    assert "Sin datos" in svg and "sin historial" in svg and "<polyline" not in svg
    assert "$0.00" not in svg


def test_equity_relleno_en_cero_no_se_dibuja(tmp_path):
    # Alpaca rellena con 0 los tramos sin cuenta: eso no es una caída a cero.
    get = alpaca_falso({"equity": "5000"}, **{HIST: _historial([0, 0, 0, None], base=0)})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["equity_dia"]["puntos"] == []
    svg = _svgs_equity(bd.render(ctx))[0]
    assert "Sin datos" in svg and "<polyline" not in svg


def test_equity_datos_reales_se_grafican_con_la_inicial(tmp_path):
    get = alpaca_falso({"equity": "5030"}, **{HIST: _historial([5000, 5010.5, 4995, 5030], base=5000)})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    puntos = ctx["equity_dia"]["puntos"]
    assert [v for _, v in puntos] == [5000, 5010.5, 4995, 5030]
    assert puntos[0][0] == datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    assert ctx["equity_dia"]["base"] == 5000
    svg = _svgs_equity(bd.render(ctx))[0]
    assert svg.count("<polyline") == 1
    coords = svg.split('points="')[1].split('"')[0].split(" ")
    assert len(coords) == 4
    assert "inicial $5,000.00" in svg and 'stroke-dasharray="5 4"' in svg
    assert "Sin datos" not in svg
    # Eje X en la zona del panel (UTC). El extremo derecho es el punto en
    # vivo (equity de la cuenta a las 15:00), no la última vela de 14:15.
    assert "14:00" in svg and "15:00 en vivo" in svg
    assert "<h2>Equity de hoy</h2>" in bd.render(ctx) and "vie 18 sep · velas de 5 min" in bd.render(ctx)
    assert "Sin sesión hoy todavía" not in bd.render(ctx)


def test_equity_plana_real_se_dibuja_plana(tmp_path):
    get = alpaca_falso({"equity": "5000"}, **{HIST: _historial([5000, 5000, 5000], base=5000)})
    svg = _svgs_equity(bd.render(bd.construir(AHORA, cfg(tmp_path), get=get)))[0]
    coords = svg.split('points="')[1].split('"')[0].split(" ")
    ys = {c.split(",")[1] for c in coords}
    assert len(coords) == 3 and len(ys) == 1   # misma y en los tres puntos


def test_equity_mezcla_solo_conserva_los_puntos_reales(tmp_path):
    get = alpaca_falso({"equity": "5000"}, **{HIST: _historial([0, 5000, None, 5020, "basura"], base=5000)})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert [v for _, v in ctx["equity_dia"]["puntos"]] == [5000, 5020]


def test_equity_sin_base_value_no_inventa_la_inicial(tmp_path):
    get = alpaca_falso({"equity": "5000"}, **{HIST: _historial([5000, 5020])})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["equity_dia"]["base"] is None
    html = bd.render(ctx)
    svg = _svgs_equity(html)[0]
    assert "inicial" not in svg and "<polyline" in svg
    assert "<span class=\"mono\">Inicial</span><b>sin dato</b>" in html


def test_equity_antes_de_la_apertura_dice_de_que_sesion_es(tmp_path):
    # Madrugada del lunes: Alpaca devuelve con period=1D la sesión completa
    # del viernes. Se dibuja, pero el título dice de qué día es y se avisa
    # que hoy todavía no hay sesión.
    lunes_madrugada = datetime(2026, 9, 21, 7, 41, tzinfo=timezone.utc)
    viernes = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)
    get = alpaca_falso({"equity": "4993.57"}, **{HIST: _historial([5000, 4993.57, 4993.57], base=5000, inicio=viernes)})
    ctx = bd.construir(lunes_madrugada, cfg(tmp_path), get=get)
    assert ctx["equity_dia"]["sesion"] == date(2026, 9, 18) and ctx["equity_dia"]["es_hoy"] is False
    html = bd.render(ctx)
    assert "<h2>Equity de hoy</h2>" not in html
    assert "<h2>Equity de la última sesión</h2>" in html and "vie 18 sep · velas de 5 min" in html
    assert "Sin sesión hoy todavía" in html and "la del vie 18 sep" in html
    assert "<polyline" in _svgs_equity(html)[0]


def test_equity_nunca_dibuja_puntos_futuros(tmp_path):
    # Tres puntos ya ocurridos y dos posteriores a AHORA (15:00 UTC): los
    # futuros no existen como dato medido y no entran ni en la serie ni en
    # el eje X.
    inicio = datetime(2026, 9, 18, 14, 50, tzinfo=timezone.utc)
    get = alpaca_falso({"equity": "5000"}, **{HIST: _historial([5000, 5001, 5002, 5003, 5004], base=5000, inicio=inicio)})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert [v for _, v in ctx["equity_dia"]["puntos"]] == [5000, 5001, 5002]
    assert ctx["equity_dia"]["es_hoy"] is True
    svg = _svgs_equity(bd.render(ctx))[0]
    assert len(svg.split('points="')[1].split('"')[0].split(" ")) == 3
    assert "15:00" in svg and "15:05" not in svg and "15:10" not in svg


def test_equity_sin_puntos_no_inventa_sesion(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["equity_dia"]["sesion"] is None and ctx["equity_dia"]["es_hoy"] is False
    html = bd.render(ctx)
    assert "<h2>Equity de hoy</h2>" in html and "Sin sesión hoy todavía" not in html

def test_numero_de_cuenta_paper_se_muestra_para_poder_compararlo(tmp_path):
    get = alpaca_falso({"equity": "4993.57", "account_number": "PA3AEXOQN3ID"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["cuenta_numero"] == "PA3AEXOQN3ID"
    assert "cuenta paper PA3AEXOQN3ID" in bd.render(ctx)


def test_sin_numero_de_cuenta_no_se_inventa(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "4993.57"}))
    assert ctx["cuenta_numero"] is None
    assert "cuenta de práctica Alpaca" in bd.render(ctx) and "cuenta paper " not in bd.render(ctx)


def _mes(equity):
    inicio = datetime(2026, 8, 20, 21, 0, tzinfo=timezone.utc)
    return _historial(equity, timestamps=[int(inicio.timestamp()) + 86400 * i for i in range(len(equity))], base=equity[0])


def test_historial_que_no_cuadra_con_la_cuenta_se_avisa_con_los_dos_numeros(tmp_path):
    # Historial plano en $1.000 y cuenta en $4.993,57: no puede ser la misma
    # cuenta (o el historial está roto). Se avisa, no se corrige.
    get = alpaca_falso({"equity": "4993.57", "last_equity": "4993.57"}, **{HIST: _mes([1000, 1000, 1000])})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["desajuste_equity"] is not None
    html = bd.render(ctx)
    assert "El historial no cuadra con la cuenta" in html
    assert "$1,000.00" in ctx["desajuste_equity"] and "$4,993.57" in ctx["desajuste_equity"]
    assert "misma cuenta paper" in html


def test_historial_que_cuadra_con_el_cierre_anterior_no_avisa(tmp_path):
    # Último cierre diario = last_equity de la cuenta (hoy ya se movió): coherente.
    get = alpaca_falso({"equity": "5030", "last_equity": "4993.57"}, **{HIST: _mes([5000, 4993.62, 4993.57])})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["desajuste_equity"] is None and "no cuadra" not in bd.render(ctx)


def test_historial_que_cuadra_con_la_equity_actual_no_avisa(tmp_path):
    get = alpaca_falso({"equity": "5030", "last_equity": "4993.57"}, **{HIST: _mes([5000, 4993.57, 5030.10])})
    assert bd.construir(AHORA, cfg(tmp_path), get=get)["desajuste_equity"] is None


def test_desajuste_no_se_evalua_sin_cuenta_o_sin_historial(tmp_path):
    assert bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)["desajuste_equity"] is None
    get = alpaca_falso({}, **{HIST: _mes([1000, 1000])})   # cuenta sin equity legible
    assert bd.construir(AHORA, cfg(tmp_path), get=get)["desajuste_equity"] is None


def test_equity_pide_las_dos_vistas_solo_con_get(tmp_path):
    llamadas = []

    def espia(ruta, params=None):
        llamadas.append((ruta, params))
        return None, "sin datos (prueba)"

    bd.construir(AHORA, cfg(tmp_path), get=espia)
    historial = [p for r, p in llamadas if r == HIST]
    assert {"period": "1D", "timeframe": "5Min"} in historial
    assert {"period": "1M", "timeframe": "1D"} in historial
    assert all(r.startswith("/v2/") for r, _ in llamadas)


def test_equity_error_de_alpaca_queda_en_problemas_una_vez(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["problemas"].count("sin conexión (prueba)") == 1


# ───────────────────────── velas del ticker en operación ─────────────────────────

from dashboard import velas as dv  # noqa: E402

# Las pruebas históricas inyectan `fuente` (Yahoo) y cuentan esas
# llamadas. `obtener` ahora pide el feed primero: sin este doble, un
# entorno con claves saldría a data.alpaca.markets y esas cuentas
# dejarían de cerrar. Las pruebas nuevas pasan `alpaca=` o llaman
# `_FUENTE_ALPACA_REAL`.
_FUENTE_ALPACA_REAL = dv.fuente_alpaca


@pytest.fixture(autouse=True)
def _el_panel_no_sale_al_feed_si_el_test_no_lo_pide(monkeypatch):
    monkeypatch.setattr(dv, "fuente_alpaca", lambda ticker: None)


def _velas(n=5, inicio=datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc), base=5.0):
    from datetime import timedelta
    ts = [(inicio + timedelta(minutes=i)).isoformat(timespec="seconds") for i in range(n)]
    opens = [base + 0.01 * i for i in range(n)]
    closes = [o + (0.02 if i % 2 == 0 else -0.01) for i, o in enumerate(opens)]
    return {"timestamps": ts, "open": opens, "close": closes,
            "high": [max(o, c) + 0.01 for o, c in zip(opens, closes)],
            "low": [min(o, c) - 0.01 for o, c in zip(opens, closes)],
            "volume": [1000.0] * n}


def velas_ok(ticker):
    return {"velas": _velas(), "obtenido": AHORA, "origen": "fuente", "error": None}


def velas_caidas(ticker):
    return {"velas": None, "obtenido": None, "origen": None, "error": "la fuente de velas falló (Timeout)"}


def _posicion(ticker="AAA", precio="5.10"):
    return {"symbol": ticker, "avg_entry_price": precio, "qty": "58", "side": "long"}


def _compra(ticker="AAA", precio="5.12", hora="2026-09-18T14:32:10Z", stop="4.90", stop_estado="new"):
    legs = [] if stop is None else [{"id": "leg-stop", "symbol": ticker, "side": "sell", "type": "stop",
                                    "status": stop_estado, "stop_price": stop,
                                    "submitted_at": "2026-09-18T14:32:00Z"}]
    return {"id": "ord-1", "symbol": ticker, "side": "buy", "type": "limit", "status": "filled",
            "filled_avg_price": precio, "filled_at": hora, "submitted_at": "2026-09-18T14:31:50Z",
            "legs": legs}


def _watchlist_con_ruptura(tmp_path, ticker="AAA", ruptura=5.05):
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [
        _entrada(ticker, "triggered", ultima_zona_entrada_baja=ruptura)]}))


def _svg_velas(html, ticker):
    import re
    m = re.search(rf'<h2>{ticker}</h2>.*?(<svg viewBox="0 0 650 230" role="img" aria-label="Velas[^"]*">.*?</svg>)', html, re.S)
    return m.group(1) if m else None


def test_sin_posiciones_ni_ordenes_lo_dice(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "5000"}), velas=velas_ok)
    assert ctx["operaciones"] == []
    assert "Sin posiciones abiertas ni órdenes pendientes." in bd.render(ctx)


def test_alpaca_caido_operaciones_es_sin_datos_no_vacio(tmp_path):
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, velas=velas_ok))
    assert "Sin datos: Alpaca no respondió posiciones u órdenes." in html
    assert "Sin posiciones abiertas" not in html


def test_fuente_de_velas_caida_muestra_sin_datos_y_ninguna_vela(tmp_path):
    _watchlist_con_ruptura(tmp_path)
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": [_compra()]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_caidas)
    assert [op["ticker"] for op in ctx["operaciones"]] == ["AAA"]
    html = bd.render(ctx)
    svg = _svg_velas(html, "AAA")
    assert "Sin datos" in svg and "Timeout" in svg
    assert 'class="vela ' not in svg and "marca-" not in svg
    # Las marcas siguen en el pie, porque salen de Alpaca y la watchlist, no de Yahoo.
    assert "$5.05" in html and "$5.12" in html and "$4.90" in html


def test_velas_reales_se_grafican_con_las_tres_marcas_y_la_hora_del_fill(tmp_path):
    _watchlist_con_ruptura(tmp_path)
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": [_compra()]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    m = ctx["operaciones"][0]["marcas"]
    # Sin evento `orden` no se sabe la ruptura al decidir; la de la
    # watchlist es la ACTUAL y se rotula así.
    assert m == {"ruptura": None, "ruptura_actual": 5.05, "patron": None, "vwap_al_decidir": None,
                 "entrada_precio": 5.12,
                 "entrada_hora": datetime(2026, 9, 18, 14, 32, 10, tzinfo=timezone.utc), "stop": 4.90,
                 "objetivo": None}
    html = bd.render(ctx)
    svg = _svg_velas(html, "AAA")
    assert svg.count('class="vela ') == 5
    for marca in ("marca-ruptura-actual", "marca-stop", "marca-entrada", "marca-entrada-hora"):
        assert marca in svg, marca
    assert "ruptura actual $5.05" in svg and "stop $4.90" in svg and "entrada $5.12" in svg
    assert "5 velas · obtenidas 15:00" in html
    assert '<b>$5.12</b><span class="mono sub-nivel">a las 14:32</span>' in html


def test_marcas_que_faltan_no_se_dibujan_y_dicen_sin_dato(tmp_path):
    # Posición abierta ayer: no hay compra de hoy ni entrada en la watchlist.
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": []})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    m = ctx["operaciones"][0]["marcas"]
    assert m["ruptura"] is None and m["stop"] is None and m["entrada_hora"] is None
    assert m["entrada_precio"] == 5.10   # precio medio real de la posición, sin hora
    html = bd.render(ctx)
    svg = _svg_velas(html, "AAA")
    assert "marca-ruptura" not in svg and "marca-stop" not in svg and "marca-entrada-hora" not in svg
    assert "marca-entrada" in svg
    # ruptura, stop y objetivo sin dato.
    velas_html = html.split("Velas de posiciones abiertas", 1)[1].split("Watchlist", 1)[0]
    assert velas_html.count("<b>sin dato</b>") == 3 and "<b>$5.10</b><span class=\"mono sub-nivel\">hora sin dato</span>" in html
    assert 'class="vela ' in svg


def test_stop_cancelado_no_cuenta_como_stop(tmp_path):
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()],
                                             "/v2/orders": [_compra(stop_estado="canceled")]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    assert ctx["operaciones"][0]["marcas"]["stop"] is None


def test_stop_de_una_orden_abierta_de_otro_dia_si_cuenta(tmp_path):
    # La compra fue ayer (no está en las órdenes de hoy) pero el stop sigue abierto.
    abierta = {"id": "stop-viejo", "symbol": "AAA", "side": "sell", "type": "stop", "status": "new",
               "stop_price": "4.80", "submitted_at": "2026-09-17T15:00:00Z"}
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": []})
    llamadas = []

    def get_con_abiertas(ruta, params=None):
        llamadas.append((ruta, params))
        # La columna Stop no mira `status=open`: pide el listado de
        # `ordenes_de_simbolos`. Un stop `new` de ayer sí está ahí.
        if ruta == "/v2/orders" and params and params.get("symbols"):
            return [abierta], None
        if ruta == "/v2/orders" and params and params.get("status") == "open":
            return [abierta], None
        return get(ruta, params)

    ctx = bd.construir(AHORA, cfg(tmp_path), get=get_con_abiertas, velas=velas_ok)
    assert ctx["operaciones"][0]["marcas"]["stop"] == 4.80
    assert ctx["posiciones_broker"][0]["stop"]["precio"] == 4.80
    assert ctx["posiciones_broker"][0]["stop"]["estado"] == "new"
    pedidos = [p for r, p in llamadas if r == "/v2/orders" and p]
    assert any(p.get("nested") == "true" and p.get("status") == "all" for p in pedidos)
    assert bd._parametros_ordenes_de_simbolos(["AAA"]) in pedidos
    # `status=open` sigue pidiéndose para pendientes y el gráfico.
    assert any(p.get("nested") == "true" and p.get("status") == "open" for p in pedidos)
    assert any(p.get("nested") == "true" and p.get("status") == "closed" for p in pedidos)


def test_marca_fuera_de_rango_se_anota_en_el_borde_sin_aplastar_las_velas(tmp_path):
    _watchlist_con_ruptura(tmp_path, ruptura=50.0)   # lejísimos de velas de $5
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": []})
    svg = _svg_velas(bd.render(bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)), "AAA")
    assert "marca-ruptura-actual-fuera" in svg and "(fuera)" in svg
    assert '<line class="marca-ruptura-actual' not in svg


def test_tope_de_tickers_por_corrida(tmp_path):
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion("AAA"), _posicion("BBB")], "/v2/orders": []})
    pedidos = []

    def velas_contando(ticker):
        pedidos.append(ticker)
        return velas_ok(ticker)

    ctx = bd.construir(AHORA, cfg(tmp_path, velas_max_tickers=1), get=get, velas=velas_contando)
    assert pedidos == ["AAA"] and ctx["operaciones_omitidas"] == ["BBB"]
    assert "Sin graficar por el tope" in bd.render(ctx)


def test_tickers_en_operacion_abiertas_luego_pendientes_sin_cerradas():
    # Una compra ya llena no es "en operación": el 28/9 DLB/NBIS/TWST
    # seguían en el gráfico porque cualquier orden de hoy contaba.
    pendiente = _compra("ACN", stop=None)
    pendiente["status"] = "accepted"
    pendiente["filled_avg_price"] = None
    pendiente["filled_at"] = None
    assert bd.tickers_en_operacion(
        [_posicion("MNST")], [pendiente, _compra("DLB"), _compra("MNST")],
    ) == [("MNST", "abierta"), ("ACN", "pendiente")]


def _posicion_llena(ticker="MNST", qty="20", entrada="40.10", actual="41.00", pnl="18.00", plpc="0.0224"):
    return {"symbol": ticker, "qty": qty, "avg_entry_price": entrada, "current_price": actual,
            "unrealized_pl": pnl, "unrealized_plpc": plpc, "side": "long"}


def _bracket_pendiente(ticker="ACN", limite="250.00", stop="245.00", objetivo="260.00"):
    """Entrada sin llenar. Las patas siguen `held` y Alpaca no las manda sueltas."""
    return {
        "id": f"buy-{ticker}", "symbol": ticker, "side": "buy", "type": "limit", "status": "accepted",
        "qty": "4", "limit_price": limite, "submitted_at": "2026-09-18T14:40:00Z",
        "order_class": "bracket",
        "legs": [
            {"id": f"tp-{ticker}", "side": "sell", "type": "limit", "status": "held",
             "limit_price": objetivo, "submitted_at": "2026-09-18T14:40:01Z"},
            {"id": f"sl-{ticker}", "side": "sell", "type": "stop", "status": "held",
             "stop_price": stop, "submitted_at": "2026-09-18T14:40:01Z"},
        ],
    }


def _tp_con_stop_held(ticker="MNST", stop="41.62", objetivo="48.00"):
    """Take-profit con el stop en `legs`. No es lo que `status=open`
    devolvió el 28/9 (ahí `legs` venía null): cubre una fila viva cuyas
    patas sí vienen anidadas, a veces sin `symbol` en la pata."""
    return {
        "id": f"tp-{ticker}", "symbol": ticker, "side": "sell", "type": "limit", "status": "new",
        "qty": "20", "limit_price": objetivo, "submitted_at": "2026-09-18T14:10:00Z",
        "order_class": "oco",
        "legs": [{
            "id": "f2d920f1-stop", "side": "sell", "type": "stop", "status": "held",
            "stop_price": stop, "submitted_at": "2026-09-18T14:10:00Z",
        }],
    }


def _revision_viva(ticker, resultado="abierta"):
    return {
        "ticker": ticker, "creado_en": "2026-09-18T14:00:00+00:00", "entro": True, "confianza": 7,
        "razonamiento": "x", "timestamp": "2026-09-18T14:01:00+00:00", "order_id": f"ord-{ticker}",
        "resultado": resultado, "cantidad": 20, "precio_entrada": 40.1, "stop": 41.62, "objetivo": 48.0,
    }


def _libro(tmp_path, *filas):
    ruta = tmp_path / "revisiones.json"
    ruta.write_text(json.dumps({"revisiones": list(filas)}), encoding="utf-8")
    return ruta


def test_el_panel_sigue_al_broker_y_no_grafica_lo_cerrado_hoy(tmp_path):
    # MNST llena, ACN/NTAP brackets sin llenar, DLB cerrada hoy (entrada de ayer).
    # El contador de posiciones ya era 1; los gráficos tienen que coincidir.
    dlb_compra = {"id": "buy-dlb", "symbol": "DLB", "side": "buy", "type": "limit", "status": "filled",
                  "qty": "10", "filled_qty": "10", "filled_avg_price": "12.00",
                  "filled_at": "2026-09-17T15:00:00Z", "submitted_at": "2026-09-17T14:50:00Z"}
    dlb_venta = {"id": "sell-dlb", "symbol": "DLB", "side": "sell", "type": "market", "status": "filled",
                 "qty": "10", "filled_qty": "10", "filled_avg_price": "11.50",
                 "filled_at": "2026-09-18T14:53:00Z", "submitted_at": "2026-09-18T14:53:00Z"}
    ordenes = [_tp_con_stop_held(), _bracket_pendiente("ACN"), _bracket_pendiente("NTAP", "115", "110", "125"),
               dlb_compra, dlb_venta]
    get = alpaca_falso(
        {"equity": "5000"},
        **{"/v2/positions": [_posicion_llena()], "/v2/orders": ordenes},
    )
    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=_libro(tmp_path, _revision_viva("MNST"))),
                       get=get, velas=velas_ok)
    assert ctx["n_pos"] == 1
    assert [(op["ticker"], op["rol"]) for op in ctx["operaciones"]] == [
        ("MNST", "abierta"), ("ACN", "pendiente"), ("NTAP", "pendiente")]
    fila = ctx["posiciones_broker"][0]
    assert fila["qty"] == 20 and fila["entrada"] == 40.10 and fila["actual"] == 41.00
    assert fila["pnl"] == 18.00 and fila["pnl_pct"] == pytest.approx(2.24)
    assert fila["stop"]["precio"] == 41.62 and fila["stop"]["estado"] == "held"
    assert fila["tp"]["precio"] == 48.00
    assert [p["ticker"] for p in ctx["pendientes_broker"]] == ["ACN", "NTAP"]
    assert ctx["pendientes_broker"][0]["limite"] == 250.00
    assert ctx["pendientes_broker"][0]["stop"]["precio"] == 245.00
    assert ctx["pendientes_broker"][0]["stop"]["estado"] == "held"
    cierre = ctx["cerradas_hoy"]
    assert [c["ticker"] for c in cierre] == ["DLB"]
    assert cierre[0]["qty"] == 10 and cierre[0]["entrada"] == 12.0 and cierre[0]["salida"] == 11.5
    assert cierre[0]["pnl"] == -5.0
    assert ctx["avisos_broker"] == []
    html = bd.render(ctx)
    assert 'class="badge">pendiente</span>' in html
    assert "Cerradas hoy" in html and "DLB" in html and "−$5.00" in html
    assert '$41.62 <span class="mono" title="Alpaca: held · OCO (stop y objetivo enlazados: si se toca uno, se cancela el otro)">· activo (con objetivo)</span>' in html
    assert "no tiene stop" not in html and "no hay una revisión viva" not in html
    # El gráfico de la cerrada no está. El de la pendiente sí, marcado.
    assert _svg_velas(html, "DLB") is None
    assert _svg_velas(html, "MNST") is not None
    assert "Orden de entrada sin llenar" in html


def test_cierre_por_la_pata_filled_del_bracket_tiene_pnl(tmp_path):
    # Con nested=true la venta no es una fila propia: es la pata del padre.
    padre = {
        "id": "buy-twst", "symbol": "TWST", "side": "buy", "type": "limit", "status": "filled",
        "qty": "5", "filled_qty": "5", "filled_avg_price": "10.00",
        "filled_at": "2026-09-18T14:00:00Z", "submitted_at": "2026-09-18T13:50:00Z",
        "legs": [
            {"id": "sl-twst", "side": "sell", "type": "stop", "status": "filled",
             "filled_qty": "5", "filled_avg_price": "9.50", "filled_at": "2026-09-18T14:53:00Z"},
            {"id": "tp-twst", "side": "sell", "type": "limit", "status": "canceled", "limit_price": "12"},
        ],
    }
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [], "/v2/orders": [padre]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    assert ctx["operaciones"] == []
    cierre = ctx["cerradas_hoy"]
    assert len(cierre) == 1 and cierre[0]["ticker"] == "TWST"
    assert cierre[0]["qty"] == 5 and cierre[0]["entrada"] == 10.0 and cierre[0]["salida"] == 9.5
    assert cierre[0]["pnl"] == -2.5


def test_cierre_sin_entrada_en_el_historial_no_inventa_el_pnl(tmp_path):
    venta = {"id": "sell-nbis", "symbol": "NBIS", "side": "sell", "type": "market", "status": "filled",
             "qty": "8", "filled_qty": "8", "filled_avg_price": "9.40",
             "filled_at": "2026-09-18T14:53:00Z", "submitted_at": "2026-09-18T14:53:00Z"}
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [], "/v2/orders": [venta]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    assert ctx["operaciones"] == []
    cierre = ctx["cerradas_hoy"][0]
    assert cierre["ticker"] == "NBIS" and cierre["salida"] == 9.40
    assert cierre["entrada"] is None and cierre["pnl"] is None
    html = bd.render(ctx)
    assert "NBIS" in html and "$9.40" in html
    # El P&L que no se pudo emparejar no se rellena con cero.
    fila = html.split("NBIS", 1)[1]
    assert "+$0.00" not in fila and "−$0.00" not in fila


def test_stop_held_anidado_cuenta_como_proteccion(tmp_path):
    # El stop `held` en `legs` (aunque cuelgue del take-profit) es
    # protección para el aviso y para la columna. No es "sin stop".
    get = alpaca_falso(
        {"equity": "5000"},
        **{"/v2/positions": [_posicion_llena()], "/v2/orders": [_tp_con_stop_held()]},
    )
    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=_libro(tmp_path, _revision_viva("MNST"))),
                       get=get, velas=velas_ok)
    assert ctx["avisos_broker"] == []
    assert "El broker no cuadra" not in bd.render(ctx)


def test_posicion_sin_revision_viva_avisa_aunque_tenga_stop_held(tmp_path):
    get = alpaca_falso(
        {"equity": "5000"},
        **{"/v2/positions": [_posicion_llena("CTAS")], "/v2/orders": [_tp_con_stop_held("CTAS")]},
    )
    # El libro la da por cerrada: el broker todavía la tiene.
    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=_libro(tmp_path, _revision_viva("CTAS", "cerrada"))),
                       get=get, velas=velas_ok)
    assert ctx["avisos_broker"] == [{"ticker": "CTAS", "sin_seguimiento": True, "sin_stop": False}]
    html = bd.render(ctx)
    assert "no hay una revisión viva que la siga" in html
    assert "no tiene stop" not in html
    assert 'role="alert"' in html and "El broker no cuadra" in html


def test_posicion_seguida_sin_stop_vivo_avisa(tmp_path):
    # Solo el take-profit, y la pata de stop ya cancelada: no es protección.
    orden = _tp_con_stop_held("MNST")
    orden["legs"][0]["status"] = "canceled"
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion_llena()], "/v2/orders": [orden]})
    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=_libro(tmp_path, _revision_viva("MNST"))),
                       get=get, velas=velas_ok)
    assert ctx["avisos_broker"] == [{"ticker": "MNST", "sin_seguimiento": False, "sin_stop": True}]
    assert "no tiene stop de venta abierto" in bd.render(ctx)
    assert "no hay una revisión viva" not in bd.render(ctx)


def test_sin_ordenes_legibles_no_afirma_que_falta_el_stop(tmp_path):
    def get(ruta, params=None):
        if ruta == "/v2/positions":
            return [_posicion_llena()], None
        if ruta == "/v2/account":
            return {"equity": "5000"}, None
        if ruta == bd.RUTA_HISTORIAL:
            return None, "sin historial (prueba)"
        return None, "sin órdenes (prueba)"

    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=_libro(tmp_path, _revision_viva("MNST"))),
                       get=get, velas=velas_ok)
    assert ctx["avisos_broker"] == []
    assert ctx["posiciones_broker"][0]["salidas_conocidas"] is False
    html = bd.render(ctx)
    assert "no tiene stop" not in html
    assert "sin datos" in html  # stop y objetivo de la fila, no un "—" que parece "no hay"


def test_venta_a_mercado_en_curso_no_se_trata_como_desprotegida(tmp_path):
    mercado = {"id": "mkt", "symbol": "MNST", "side": "sell", "type": "market", "status": "accepted",
               "qty": "20", "submitted_at": "2026-09-18T14:55:00Z"}
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion_llena()], "/v2/orders": [mercado]})
    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=_libro(tmp_path, _revision_viva("MNST"))),
                       get=get, velas=velas_ok)
    assert ctx["avisos_broker"] == []
    assert "venta a mercado" in bd.render(ctx)


def _posicion_mnst_viva():
    """MNST el 2026-09-28: 17 acciones a $41.87, bracket ya lleno."""
    return {
        "symbol": "MNST", "qty": "17", "avg_entry_price": "41.87", "current_price": "42.10",
        "unrealized_pl": "3.91", "unrealized_plpc": "0.0055", "side": "long",
    }


def _tp_abierto_mnst():
    """Lo que devolvió `status=open&nested=true`: solo bb5baab2, legs null."""
    return {
        "id": "bb5baab2", "symbol": "MNST", "side": "sell", "type": "limit",
        "order_class": "bracket", "time_in_force": "day", "qty": "17",
        "limit_price": "42.36", "stop_price": None, "status": "new", "legs": None,
        "submitted_at": "2026-09-28T14:30:22Z",
    }


def _pata_stop_mnst(tipo="stop", status="held"):
    return {
        "id": "f2d920f1", "symbol": "MNST", "side": "sell", "type": tipo,
        "order_class": "bracket", "time_in_force": "day", "limit_price": None,
        "stop_price": "41.62", "status": status, "legs": None,
        "submitted_at": "2026-09-28T14:30:23Z",
    }


def _padre_filled_mnst(legs=None):
    """Compra 265e093f ya filled. El stop held no está en `status=open`."""
    if legs is None:
        legs = [_tp_abierto_mnst(), _pata_stop_mnst()]
    return {
        "id": "265e093f", "symbol": "MNST", "side": "buy", "type": "limit",
        "order_class": "bracket", "time_in_force": "day", "qty": "17",
        "filled_qty": "17", "filled_avg_price": "41.87", "status": "filled",
        "submitted_at": "2026-09-28T14:30:22Z", "legs": legs,
    }


def _get_mnst(ordenes_simbolo, fallo_simbolos=None):
    """`status=open` siempre miente: solo el take-profit con legs null.
    La protección tiene que salir de la consulta por símbolo."""
    llamadas = []

    def get(ruta, params=None):
        llamadas.append((ruta, params))
        if ruta == "/v2/account":
            return {"equity": "5000", "last_equity": "5000"}, None
        if ruta == "/v2/positions":
            return [_posicion_mnst_viva()], None
        if ruta == bd.RUTA_HISTORIAL:
            return None, "sin historial (prueba)"
        if ruta == "/v2/orders" and params and params.get("symbols"):
            if fallo_simbolos is not None:
                return fallo_simbolos
            return list(ordenes_simbolo), None
        if ruta == "/v2/orders" and params and params.get("status") == "open":
            return [_tp_abierto_mnst()], None
        if ruta == "/v2/orders":
            return [], None
        return None, f"sin datos en {ruta} (prueba)"

    return get, llamadas


def _panel_mnst(tmp_path, ordenes_simbolo, fallo_simbolos=None):
    get, llamadas = _get_mnst(ordenes_simbolo, fallo_simbolos=fallo_simbolos)
    ctx = bd.construir(
        AHORA, cfg(tmp_path, revisiones=_libro(tmp_path, _revision_viva("MNST"))),
        get=get, velas=velas_ok)
    return ctx, llamadas


def test_stop_held_del_padre_filled_no_dispara_falso_sin_stop(tmp_path):
    # MNST 28/9. status=open es solo bb5baab2 (limit 42.36, new, legs
    # null). El stop f2d920f1 (41.62, held) cuelga de la compra filled
    # 265e093f. Ese listado es el de ordenes_de_simbolos.
    ctx, llamadas = _panel_mnst(tmp_path, [_padre_filled_mnst()])
    assert ctx["avisos_broker"] == []
    fila = ctx["posiciones_broker"][0]
    assert fila["qty"] == 17 and fila["entrada"] == 41.87
    assert fila["stop"] == {"precio": 41.62, "estado": "held"}
    assert fila["tp"] == {"precio": 42.36, "estado": "new"}
    html = bd.render(ctx)
    assert "no tiene stop de venta abierto" not in html
    assert '<td>$41.62 <span class="mono" title="Alpaca: held · OCO (stop y objetivo enlazados: si se toca uno, se cancela el otro)">· activo (con objetivo)</span></td><td>$42.36</td>' in html
    pedidos = [p for _r, p in llamadas if p and p.get("symbols")]
    assert pedidos == [bd._parametros_ordenes_de_simbolos(["MNST"])]


def test_stop_held_como_fila_propia_tambien_llena_la_columna(tmp_path):
    # La misma pata, suelta, como fila de status=all (sin anidar).
    ctx, _llamadas = _panel_mnst(tmp_path, [_pata_stop_mnst(), _tp_abierto_mnst()])
    assert ctx["avisos_broker"] == []
    assert ctx["posiciones_broker"][0]["stop"] == {"precio": 41.62, "estado": "held"}
    assert "no tiene stop de venta abierto" not in bd.render(ctx)


def test_el_take_profit_abierto_sin_patas_no_es_el_stop(tmp_path):
    # status=all tampoco trae el padre ni el stop. El limit 42.36 no
    # cuenta. Ahí el aviso sí es verdadero y la columna es "—".
    ctx, _llamadas = _panel_mnst(tmp_path, [_tp_abierto_mnst()])
    assert ctx["avisos_broker"] == [{"ticker": "MNST", "sin_seguimiento": False, "sin_stop": True}]
    assert ctx["posiciones_broker"][0]["stop"] is None
    assert ctx["posiciones_broker"][0]["tp"]["precio"] == 42.36
    html = bd.render(ctx)
    assert "MNST: no tiene stop de venta abierto." in html
    assert "<td>—</td><td>$42.36</td>" in html


def test_si_no_se_leen_las_ordenes_del_simbolo_no_se_afirma_que_falta_el_stop(tmp_path):
    # status=open sigue siendo solo el take-profit. Si la consulta de
    # símbolos falla o no es una lista, el panel recibe None, no []:
    # ni afirma que falte el stop ni pinta 41.62.
    libro = _libro(tmp_path, _revision_viva("MNST"))

    def get_roto(ruta, params=None):
        return None, "timeout"

    assert bd.leer_ordenes_de_simbolos(get_roto, ["MNST"])[0] is None

    fallos = (
        (None, "Alpaca no respondió en /v2/orders: timeout"),
        ({"message": "no"}, None),
    )
    for fallo in fallos:
        get, _llamadas = _get_mnst([], fallo_simbolos=fallo)
        ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=libro), get=get, velas=velas_ok)
        assert ctx["avisos_broker"] == [], fallo
        assert all(not a.get("sin_stop") for a in ctx["avisos_broker"])
        fila = ctx["posiciones_broker"][0]
        assert fila["salidas_conocidas"] is False and fila["stop"] is None
        html = bd.render(ctx)
        assert "no tiene stop" not in html
        bloque = html.split("Posiciones abiertas", 1)[1].split("Órdenes pendientes", 1)[0]
        assert "sin datos" in bloque and "$41.62" not in bloque and "<td>—</td>" not in bloque
        if fallo[1]:
            assert fallo[1] in ctx["problemas"]
        else:
            assert any("no se afirma que falte el stop" in p for p in ctx["problemas"])


def test_aviso_y_columna_siguen_la_regla_de_proteccion(tmp_path):
    # Misma forma del padre filled. La whitelist es held/new/accepted/
    # pending_new. pending_cancel, canceled y un status desconocido o
    # ausente no protegen y no llenan la columna con 41.62.
    libro = _libro(tmp_path, _revision_viva("MNST"))
    casos = [
        ("stop", "held", False),
        ("stop_limit", "new", False),
        ("trailing_stop", "accepted", False),
        ("stop", "pending_new", False),
        ("stop", "pending_cancel", True),
        ("stop", "canceled", True),
        ("stop", "foo", True),
        ("stop", None, True),
    ]
    for tipo, status, sin_stop in casos:
        get, _llamadas = _get_mnst([_padre_filled_mnst(legs=[
            _tp_abierto_mnst(), _pata_stop_mnst(tipo, status),
        ])])
        ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=libro), get=get, velas=velas_ok)
        stop = ctx["posiciones_broker"][0]["stop"]
        if sin_stop:
            assert ctx["avisos_broker"] == [{"ticker": "MNST", "sin_seguimiento": False, "sin_stop": True}], status
            assert stop is None
            bloque = bd.render(ctx).split("Posiciones abiertas", 1)[1].split("Órdenes pendientes", 1)[0]
            assert "$41.62" not in bloque
        else:
            assert ctx["avisos_broker"] == [], (tipo, status)
            assert stop == {"precio": 41.62, "estado": status}

    get, _llamadas = _get_mnst([_padre_filled_mnst(legs=[_tp_abierto_mnst()])])
    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=libro), get=get, velas=velas_ok)
    assert ctx["avisos_broker"] == [{"ticker": "MNST", "sin_seguimiento": False, "sin_stop": True}]
    assert ctx["posiciones_broker"][0]["stop"] is None
    assert ctx["posiciones_broker"][0]["tp"]["precio"] == 42.36

    mercado = {"id": "mkt", "symbol": "MNST", "side": "sell", "type": "market", "status": "accepted",
               "qty": "17", "submitted_at": "2026-09-28T15:00:00Z"}
    get, _llamadas = _get_mnst([_padre_filled_mnst(legs=[_tp_abierto_mnst()]), mercado])
    ctx = bd.construir(AHORA, cfg(tmp_path, revisiones=libro), get=get, velas=velas_ok)
    assert ctx["avisos_broker"] == []
    assert ctx["posiciones_broker"][0]["mercado"] is True
    assert ctx["posiciones_broker"][0]["stop"] is None
    assert "venta a mercado" in bd.render(ctx)


def test_sin_simbolos_no_pide_status_all_sin_filtro(tmp_path):
    llamadas = []

    def get(ruta, params=None):
        llamadas.append((ruta, params))
        if ruta == "/v2/account":
            return {"equity": "5000"}, None
        if ruta == "/v2/positions":
            return [], None
        if ruta == bd.RUTA_HISTORIAL:
            return None, "sin historial (prueba)"
        return [], None

    bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    assert [p for _r, p in llamadas if p and "symbols" in p] == []

    visto = []

    def get_directo(ruta, params=None):
        visto.append(params)
        return [{"id": "no-debio-pedirse"}], None

    assert bd.leer_ordenes_de_simbolos(get_directo, []) == ([], None)
    assert bd.leer_ordenes_de_simbolos(get_directo, ["", "  "]) == ([], None)
    assert visto == []


def test_la_consulta_junta_los_simbolos_en_posicion(tmp_path):
    llamadas = []

    def get(ruta, params=None):
        llamadas.append((ruta, params))
        if ruta == "/v2/account":
            return {"equity": "5000"}, None
        if ruta == "/v2/positions":
            return [_posicion_mnst_viva(),
                    {"symbol": "CTAS", "qty": "3", "avg_entry_price": "190", "side": "long"}], None
        if ruta == bd.RUTA_HISTORIAL:
            return None, "sin historial (prueba)"
        if ruta == "/v2/orders":
            return [], None
        return None, f"sin datos en {ruta} (prueba)"

    bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    pedidos = [p for _r, p in llamadas if p and p.get("symbols")]
    assert pedidos == [bd._parametros_ordenes_de_simbolos(["MNST", "CTAS"])]
    assert pedidos[0]["symbols"] == "MNST,CTAS"
    assert pedidos[0]["status"] == "all" and pedidos[0]["nested"] == "true"


def test_fuente_de_datos_sale_si_la_telemetria_la_trae_y_no_se_inventa(tmp_path):
    # Sin el bloque `datos` (corrida vieja): ninguna píldora, ni "Yahoo" por defecto.
    # `fuente: vps` es el escritor del JSONL, no el feed de precios.
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 50, tzinfo=timezone.utc))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["fuente_datos"] is None
    assert "Fuente de datos:" not in bd.render(ctx)

    ruta = tmp_path / "telem" / "2026-09-18" / "vps" / "events.jsonl"
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2026-09-18T14:40:00+00:00", "modo": "escaneo",
                            "datos": {"configurada": "yahoo", "fuente": "yahoo", "feed": None}}) + "\n")
        f.write(json.dumps({"timestamp": "2026-09-18T14:55:00+00:00", "modo": "watchlist",
                            "fuente": "vps", "datos": {"fuente": None, "feed": None}}) + "\n")
    # El tick de las 14:55 no pidió barras (`fuente` vacía). Sigue valiendo
    # la medición anterior, no un "Yahoo" inventado ni el escritor `vps`.
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["fuente_datos"] == "Yahoo"
    html_yahoo = bd.render(ctx)
    assert "Fuente de datos: Yahoo" in html_yahoo
    # El gráfico pide SIP aunque el hunter haya medido Yahoo. No se
    # afirma que sean la misma fuente.
    assert "1 min · Alpaca SIP (Yahoo solo de respaldo si el feed falla)" in html_yahoo
    assert "el hunter reporta Yahoo" in html_yahoo
    assert "misma fuente que el hunter" not in html_yahoo

    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2026-09-18T14:58:00+00:00", "modo": "escaneo",
                            "datos": {"configurada": "alpaca", "fuente": "alpaca", "feed": "sip"}}) + "\n")
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["fuente_datos"] == "Alpaca SIP"
    html = bd.render(ctx)
    assert "Fuente de datos: Alpaca SIP" in html
    assert "el hunter reporta Alpaca SIP" in html
    assert "misma fuente que el hunter" not in html

    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2026-09-18T14:58:30+00:00", "modo": "watchlist",
                            "datos": {"configurada": "alpaca", "fuente": "mixto", "feed": "sip",
                                      "fallbacks": 2}}) + "\n")
    assert bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)["fuente_datos"] == "Alpaca SIP + Yahoo"

    # IEX no se etiqueta como SIP.
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2026-09-18T14:58:40+00:00", "modo": "escaneo",
                            "datos": {"fuente": "alpaca", "feed": "iex"}}) + "\n")
    assert bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)["fuente_datos"] == "Alpaca IEX"


# ───────────────────────── caché de velas ─────────────────────────

def test_cache_vigente_no_toca_la_fuente(tmp_path):
    from datetime import timedelta
    llamadas = []

    def fuente(ticker):
        llamadas.append(ticker)
        return _velas()

    r1 = dv.obtener("AAA", AHORA, tmp_path / "cache", 120, fuente=fuente)
    r2 = dv.obtener("AAA", AHORA + timedelta(seconds=60), tmp_path / "cache", 120, fuente=fuente)
    assert llamadas == ["AAA"]
    assert r1["origen"] == "fuente" and r2["origen"] == "cache"
    assert r2["velas"] == r1["velas"] and r2["obtenido"] == AHORA
    r3 = dv.obtener("AAA", AHORA + timedelta(seconds=121), tmp_path / "cache", 120, fuente=fuente)
    assert llamadas == ["AAA", "AAA"] and r3["origen"] == "fuente"


def test_fuente_caida_sirve_la_cache_vencida_con_el_error(tmp_path):
    from datetime import timedelta
    dv.obtener("AAA", AHORA, tmp_path / "cache", 120, fuente=lambda t: _velas())

    def rota(ticker):
        raise TimeoutError("yahoo")

    r = dv.obtener("AAA", AHORA + timedelta(seconds=300), tmp_path / "cache", 120, fuente=rota)
    assert r["origen"] == "cache vencida" and r["obtenido"] == AHORA
    assert r["velas"]["close"] == _velas()["close"]
    assert "TimeoutError" in r["error"]


def test_fuente_caida_sin_cache_es_sin_datos(tmp_path):
    r = dv.obtener("AAA", AHORA, tmp_path / "cache", 120, fuente=lambda t: None)
    assert r["velas"] is None and r["origen"] is None and "no devolvió" in r["error"]
    assert not list((tmp_path / "cache").glob("*")) if (tmp_path / "cache").exists() else True


def test_respuesta_invalida_no_se_cachea(tmp_path):
    r = dv.obtener("AAA", AHORA, tmp_path / "cache", 120,
                   fuente=lambda t: {"timestamps": ["x"], "open": [1], "close": [], "high": [], "low": [], "volume": []})
    assert r["velas"] is None
    assert not (tmp_path / "cache" / "velas_AAA.json").exists()


class _Respuesta:
    def __init__(self, status, cuerpo=None):
        self.status_code, self._cuerpo = status, cuerpo

    def json(self):
        return self._cuerpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise dv.requests.HTTPError(f"HTTP {self.status_code}")


def _chart_yahoo(epochs, precios, volumenes=None):
    n = len(epochs)
    vol = volumenes or [100.0] * n
    return {"chart": {"result": [{"timestamp": epochs, "indicators": {"quote": [{
        "open": precios, "close": precios, "high": [p + 0.01 for p in precios],
        "low": [p - 0.01 for p in precios], "volume": vol}]}}]}}


def _epoch(*hms, dia=18):
    return int(datetime(2026, 9, dia, *hms, tzinfo=timezone.utc).timestamp())


def test_fuente_por_defecto_hace_la_misma_peticion_y_el_mismo_parseo_que_el_hunter(monkeypatch):
    from momentum_hunter import config as hcfg
    from momentum_hunter.data import provider
    llamadas = []
    # Ayer + 6 velas de hoy + una vela en formación con volumen 0 explícito.
    epochs = [_epoch(15, 0, dia=17)] + [_epoch(14, 30 + i) for i in range(7)]
    precios = [1.0] + [5.0 + 0.01 * i for i in range(7)]
    cuerpo = _chart_yahoo(epochs, precios, [100.0] * 7 + [0.0])

    def get(url, params=None, headers=None, timeout=None):
        llamadas.append((url, params, headers))
        return _Respuesta(200, cuerpo)

    monkeypatch.setattr(dv.requests, "get", get)
    velas = dv.fuente_hunter("AAA")
    prov = provider.YahooProvider()
    assert llamadas == [(prov.CHART.format(t="AAA"),
                         prov.params_intradia(hcfg.CONFIG.intervalo_intradia, hcfg.CONFIG.periodo_intradia),
                         prov.HEADERS)]
    # Mismo resultado que el hunter: parseo idéntico (descarta la vela en
    # formación) y recorte a hoy.
    esperado = provider.parsear_chart_intradia("AAA", cuerpo)
    assert velas["close"] == esperado.close[1:] == [5.0 + 0.01 * i for i in range(6)]
    assert velas["timestamps"][0] == "2026-09-18T14:30:00+00:00"


def test_fuente_429_lanza_limite_con_una_sola_peticion_sin_reintentos(monkeypatch):
    import pytest
    llamadas = []
    monkeypatch.setattr(dv.requests, "get", lambda *a, **k: (llamadas.append(1), _Respuesta(429))[1])
    with pytest.raises(dv.LimiteDePeticiones):
        dv.fuente_hunter("AAA")
    assert len(llamadas) == 1


def test_fuente_con_pocas_velas_es_none_como_en_el_hunter(monkeypatch):
    epochs = [_epoch(14, 30 + i) for i in range(3)]
    monkeypatch.setattr(dv.requests, "get", lambda *a, **k: _Respuesta(200, _chart_yahoo(epochs, [5.0] * 3)))
    assert dv.fuente_hunter("AAA") is None


def test_429_pausa_a_todos_los_tickers_y_sirve_la_copia_vieja_marcada(tmp_path):
    from datetime import timedelta
    cache = tmp_path / "cache"
    dv.obtener("AAA", AHORA, cache, 120, fuente=lambda t: _velas())   # copia buena de AAA
    llamadas = []

    def limitada(ticker):
        llamadas.append(ticker)
        raise dv.LimiteDePeticiones("429")

    t1 = AHORA + timedelta(seconds=200)   # TTL vencido: toca pedir
    r = dv.obtener("AAA", t1, cache, 120, fuente=limitada, pausa_seg=900)
    assert llamadas == ["AAA"]
    assert r["origen"] == "cache vencida" and r["velas"]["close"] == _velas()["close"]
    assert "429" in r["error"] and "15:18" in r["error"]   # 15:03:20 + 900 s
    assert dv.pausa_hasta(cache) == t1 + timedelta(seconds=900)
    # Otro ticker, sin copia, dentro de la pausa: NO se pide y es "Sin datos".
    r2 = dv.obtener("BBB", t1 + timedelta(seconds=60), cache, 120, fuente=limitada)
    assert llamadas == ["AAA"] and r2["velas"] is None and "429" in r2["error"]
    # AAA dentro de la pausa: tampoco se pide, sigue la copia vieja.
    r3 = dv.obtener("AAA", t1 + timedelta(seconds=120), cache, 120, fuente=limitada)
    assert llamadas == ["AAA"] and r3["origen"] == "cache vencida"
    # Pasada la pausa, se vuelve a pedir.
    r4 = dv.obtener("BBB", t1 + timedelta(seconds=901), cache, 120, fuente=lambda t: _velas())
    assert r4["origen"] == "fuente"


def test_pausa_por_429_se_ve_en_el_panel(tmp_path):
    from datetime import timedelta
    cache = tmp_path / "cache"
    dv.obtener("AAA", AHORA - timedelta(seconds=600), cache, 120, fuente=lambda t: _velas())

    def limitada(ticker):
        raise dv.LimiteDePeticiones("429")

    def velas(ticker):
        return dv.obtener(ticker, AHORA, cache, 120, fuente=limitada, pausa_seg=900)

    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": []})
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas))
    assert "5 velas · caché vencida 14:50" in html
    assert "Yahoo (respaldo)" in html
    assert "Yahoo limitó peticiones (429)" in html
    assert 'class="vela ' in html   # la copia vieja sí se dibuja


class _RespDatos:
    """Respuesta mínima del host de datos. Sin cuerpo de error: no hace
    falta, y un cuerpo no se debe colar al panel."""

    def __init__(self, status, cuerpo):
        self.status_code, self._cuerpo, self.headers = status, cuerpo, {}

    def json(self):
        return self._cuerpo


def _barras_sip(n=6, dia="2026-09-18", hora=14, minuto=30):
    barras = []
    for i in range(n):
        barras.append({
            "t": f"{dia}T{hora:02d}:{minuto + i:02d}:00Z",
            "o": 5.0, "h": 5.2, "l": 4.9, "c": 5.1 + 0.01 * i, "v": 100 + i,
        })
    return barras


def test_sip_ok_da_origen_alpaca_sip_guarda_la_fuente_y_no_toca_yahoo(monkeypatch, tmp_path, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    secreto = "SECRETO-PANEL-NO-LOG"
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "KEY-PANEL")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", secreto)
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    urls = []

    def get(url, params=None, headers=None, timeout=None):
        # `requests` es un solo módulo: el feed y Yahoo comparten el get.
        urls.append((url, params, headers))
        if "paper-api.alpaca.markets" in url or "/v2/orders" in url:
            raise AssertionError(url)
        if not str(url).startswith("https://data.alpaca.markets/"):
            raise AssertionError("SIP respondió: no se pide Yahoo")
        # La última vela no trae volumen: no puede convertirse en 0.
        barras = _barras_sip() + [{"t": "2026-09-18T14:36:00Z", "o": 5, "h": 5, "l": 5, "c": 5}]
        return _RespDatos(200, {"bars": {"AAA": barras}, "next_page_token": None})

    monkeypatch.setattr(dv.requests, "get", get)
    cache = tmp_path / "cache"
    r = dv.obtener("AAA", AHORA, cache, 120, alpaca=_FUENTE_ALPACA_REAL)
    assert r["origen"] == "fuente" and r["origen_fuente"] == "alpaca-sip" and r["error"] is None
    assert len(r["velas"]["close"]) == 6
    assert all(v not in (0, 0.0) for v in r["velas"]["volume"])
    assert None not in r["velas"]["volume"]
    url, params, headers = urls[0]
    assert url == "https://data.alpaca.markets/v2/stocks/bars"
    assert params["feed"] == "sip" and params["timeframe"] == "1Min"
    assert "paper-api" not in url and "/v2/orders" not in url
    assert secreto not in url and secreto not in str(params)
    assert "APCA-API-SECRET-KEY" in headers
    assert secreto not in caplog.text
    guardado = json.loads((cache / "velas_AAA.json").read_text(encoding="utf-8"))
    assert guardado["origen_fuente"] == "alpaca-sip"
    assert guardado["velas"]["close"] == r["velas"]["close"]
    sub = bd._subtitulo_velas(r, ZoneInfo("UTC"), AHORA)
    assert sub.startswith("6 velas · Alpaca SIP ")
    # Dentro del TTL no se vuelve a pedir.
    r2 = dv.obtener("AAA", AHORA + timedelta(seconds=30), cache, 120, alpaca=_FUENTE_ALPACA_REAL)
    assert len(urls) == 1 and r2["origen"] == "cache" and r2["origen_fuente"] == "alpaca-sip"


def test_hoy_con_menos_de_5_velas_no_es_usable(monkeypatch):
    from momentum_hunter.models import BarraIntradia

    class Fake:
        def __init__(self, feed="sip"):
            assert feed == "sip"

        def barras_intradia(self, tickers, intervalo, periodo):
            # 2 de ayer + 3 de hoy: el provider devolvería la serie (5),
            # pero hoy no llega al piso del gráfico.
            ts = ["2026-09-17T19:00:00+00:00", "2026-09-17T19:01:00+00:00",
                  "2026-09-18T14:30:00+00:00", "2026-09-18T14:31:00+00:00",
                  "2026-09-18T14:32:00+00:00"]
            n = len(ts)
            return {tickers[0]: BarraIntradia(tickers[0], ts, [1.0] * n, [1.0] * n,
                                               [1.1] * n, [0.9] * n, [10.0] * n)}

    monkeypatch.setattr("momentum_hunter.data.alpaca_datos.AlpacaProvider", Fake)
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    assert _FUENTE_ALPACA_REAL("AAA") is None


@pytest.mark.parametrize("status", (400, 503))
def test_sip_4xx_o_5xx_cae_a_yahoo_con_la_etiqueta(status, monkeypatch, tmp_path, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    secreto = "SECRETO-PANEL-NO-LOG"
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "KEY-PANEL")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", secreto)
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    # El default `dormir=time.sleep` ya quedó atado al importar la clase.
    # Cero de espera: un 5xx reintenta, pero el panel no tiene que dormir.
    from momentum_hunter.data.alpaca_datos import AlpacaProvider
    monkeypatch.setattr(AlpacaProvider, "_espera", lambda self, intento, respuesta: 0.0)
    hosts = []
    epochs = [_epoch(14, 30 + i) for i in range(6)]

    def get(url, params=None, headers=None, timeout=None):
        hosts.append(url)
        if "paper-api.alpaca.markets" in str(url):
            raise AssertionError(url)
        if str(url).startswith("https://data.alpaca.markets/"):
            return _RespDatos(status, {"message": secreto})
        return _Respuesta(200, _chart_yahoo(epochs, [5.0] * 6))

    monkeypatch.setattr(dv.requests, "get", get)
    r = dv.obtener("AAA", AHORA, tmp_path / "cache", 120, alpaca=_FUENTE_ALPACA_REAL)
    assert r["origen"] == "fuente" and r["origen_fuente"] == "yahoo (respaldo)"
    assert len(r["velas"]["close"]) == 6
    datos = [h for h in hosts if "data.alpaca.markets" in h]
    assert datos and all(h.startswith("https://data.alpaca.markets/") for h in datos)
    assert any("finance.yahoo" in h or "yahoo" in h for h in hosts)
    assert all("paper-api" not in h for h in hosts)
    guardado = json.loads((tmp_path / "cache" / "velas_AAA.json").read_text(encoding="utf-8"))
    assert guardado["origen_fuente"] == "yahoo (respaldo)"
    assert "Yahoo (respaldo)" in bd._subtitulo_velas(r, ZoneInfo("UTC"), AHORA)
    assert secreto not in json.dumps(r, default=str) and secreto not in caplog.text


def test_menos_de_5_velas_de_alpaca_cae_a_yahoo_y_la_cache_guarda_el_respaldo(tmp_path):
    llamadas = []

    def yahoo(ticker):
        llamadas.append(ticker)
        return _velas()

    r = dv.obtener("AAA", AHORA, tmp_path / "cache", 120, fuente=yahoo, alpaca=lambda t: _velas(n=4))
    assert llamadas == ["AAA"] and r["origen_fuente"] == "yahoo (respaldo)"
    guardado = json.loads((tmp_path / "cache" / "velas_AAA.json").read_text(encoding="utf-8"))
    assert guardado["origen_fuente"] == "yahoo (respaldo)"
    assert len(guardado["velas"]["close"]) == 5


def test_un_429_del_feed_no_enciende_la_pausa_de_yahoo(tmp_path):
    from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca

    def alpaca(ticker):
        raise ErrorDatosAlpaca("http_429")

    r = dv.obtener("AAA", AHORA, tmp_path / "cache", 120, fuente=lambda t: _velas(), alpaca=alpaca)
    assert r["origen_fuente"] == "yahoo (respaldo)"
    assert dv.pausa_hasta(tmp_path / "cache") is None


def test_la_pausa_de_yahoo_no_bloquea_el_feed(tmp_path):
    from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca
    cache = tmp_path / "cache"
    yahoo_llamadas = []

    def yahoo_429(ticker):
        yahoo_llamadas.append(ticker)
        raise dv.LimiteDePeticiones("429")

    def alpaca_cae(ticker):
        raise ErrorDatosAlpaca("http_500")

    r = dv.obtener("AAA", AHORA, cache, 120, fuente=yahoo_429, alpaca=alpaca_cae)
    assert yahoo_llamadas == ["AAA"] and r["velas"] is None and "429" in r["error"]
    assert dv.pausa_hasta(cache) is not None

    def yahoo_no(ticker):
        raise AssertionError("con SIP en pie no se pide Yahoo")

    r2 = dv.obtener("BBB", AHORA + timedelta(seconds=30), cache, 120,
                    fuente=yahoo_no, alpaca=lambda t: _velas())
    assert r2["origen_fuente"] == "alpaca-sip" and r2["error"] is None and r2["origen"] == "fuente"
    assert yahoo_llamadas == ["AAA"]
    assert "SIP" in bd._subtitulo_velas(r2, ZoneInfo("UTC"), AHORA)
    # La pausa que anotó el bot es el mismo freno, y tampoco tapa el feed.
    pausa_bot = tmp_path / "yahoo_pausa_bot.json"
    pausa_bot.write_text(json.dumps({"hasta": (AHORA + timedelta(minutes=10)).isoformat()}))
    r3 = dv.obtener("CCC", AHORA, tmp_path / "cache3", 120, fuente=yahoo_no,
                    alpaca=lambda t: _velas(), pausa_bot=pausa_bot)
    assert r3["origen_fuente"] == "alpaca-sip" and r3["error"] is None


def test_ambas_fuentes_caen_es_sin_datos_y_no_hay_ceros(tmp_path):
    from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca

    def alpaca(ticker):
        raise ErrorDatosAlpaca("http_500")

    def yahoo(ticker):
        raise dv.requests.HTTPError("HTTP 503 con cuerpo que no se registra")

    r = dv.obtener("AAA", AHORA, tmp_path / "vacio", 120, fuente=yahoo, alpaca=alpaca)
    assert r["velas"] is None and r["origen"] is None and r["origen_fuente"] is None
    assert "HTTPError" in r["error"]
    assert "cuerpo" not in r["error"]
    assert not (tmp_path / "vacio" / "velas_AAA.json").exists()
    _watchlist_con_ruptura(tmp_path)

    def velas(ticker):
        return dv.obtener(ticker, AHORA, tmp_path / "vacio2", 120, fuente=yahoo, alpaca=alpaca)

    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": [_compra()]})
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas))
    svg = _svg_velas(html, "AAA")
    assert "Sin datos" in svg and "Sin datos" in html
    assert 'class="vela ' not in svg
    # Copia vieja de SIP: se marca vencida y se dibujan esas velas, no ceros.
    bueno = _velas()
    cache = tmp_path / "stale"
    dv.obtener("AAA", AHORA - timedelta(seconds=300), cache, 120, fuente=yahoo, alpaca=lambda t: bueno)
    r_stale = dv.obtener("AAA", AHORA, cache, 120, fuente=yahoo, alpaca=alpaca)
    assert r_stale["origen"] == "cache vencida" and r_stale["origen_fuente"] == "alpaca-sip"
    assert r_stale["velas"]["close"] == bueno["close"]
    assert all(v != 0 for v in r_stale["velas"]["close"])
    sub = bd._subtitulo_velas(r_stale, ZoneInfo("UTC"), AHORA)
    assert "caché vencida" in sub and "SIP" in sub


def test_el_texto_de_un_fallo_no_incluye_el_secreto(tmp_path, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    secreto = "SECRETO-PANEL-NO-LOG"

    def alpaca(ticker):
        raise RuntimeError(f"https://data.alpaca.markets/v2/stocks/bars?token={secreto}")

    r = dv.obtener("AAA", AHORA, tmp_path / "cache", 120, fuente=lambda t: None, alpaca=alpaca)
    assert r["velas"] is None
    assert secreto not in (r["error"] or "") and secreto not in caplog.text
    assert "RuntimeError" not in (r["error"] or "")


def test_feed_iex_no_se_etiqueta_como_sip(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    r = dv.obtener("AAA", AHORA, tmp_path / "cache", 120, alpaca=lambda t: _velas())
    assert r["origen_fuente"] == "alpaca-iex"
    sub = bd._subtitulo_velas(r, ZoneInfo("UTC"), AHORA)
    assert "IEX" in sub and "SIP" not in sub


def test_cache_por_defecto_nunca_dentro_del_repo(monkeypatch):
    monkeypatch.delenv("DASH_CACHE_VELAS", raising=False)
    ruta = bd.cargar_config()["cache_velas"]
    assert not ruta.resolve().is_relative_to(bd.REPO)


def test_cache_configurada_dentro_del_repo_se_ignora_con_aviso():
    problemas = []
    ruta = bd.cache_velas_segura(bd.REPO / "dashboard_cache", problemas)
    assert ruta == bd.CACHE_VELAS_DEFECTO
    assert any("dentro del repo" in p for p in problemas)
    problemas = []
    assert bd.cache_velas_segura(Path("/var/lib/momentum/dashboard_cache"), problemas) == Path("/var/lib/momentum/dashboard_cache")
    assert problemas == []


def test_unidad_del_panel_fija_la_cache_fuera_del_repo():
    texto = (bd.REPO / "deploy" / "momentum-dashboard.service").read_text(encoding="utf-8")
    assert "Environment=DASH_CACHE_VELAS=/var/lib/momentum/dashboard_cache" in texto
    assert "DASH_CACHE_VELAS=/opt/hernan-portafolio" not in texto


# ───────── bloqueos únicos, capacidad llena y "Revisar" (2026-09-23) ─────────

def _bloqueo(ts, ticker, codigo, limite="x", motivo="m"):
    return {"ts": ts, "tipo": "bloqueo_riesgo", "ticker": ticker, "codigo": codigo, "limite": limite, "motivo": motivo}


def test_bloqueos_se_cuentan_unicos_por_ticker_y_codigo(tmp_path):
    # 8 señales × 3 ticks del mismo límite conocido: 8 únicos, 24 eventos, y OK.
    lineas = [{"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"}]
    for i in range(8):
        for m in range(3):
            lineas.append(_bloqueo(f"2026-09-18T14:{40 + m:02d}:00Z", f"T{i}", "CONCENTRACION", "concentracion"))
    eventos(tmp_path, *lineas)
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "ok"
    assert riesgo["detalle"] == "8 bloqueos únicos (24 eventos) hoy"
    r = ctx["riesgo"]
    assert len(r["unicos"]) == 8 and all(f["veces"] == 3 for f in r["unicos"])
    assert r["unicos"][0]["hora"] == "14:42"           # última hora, no la primera
    assert r["dato_faltante"] == [] and r["codigos_nuevos"] == [] and r["revisar"] is False
    html = bd.render(ctx)
    assert "8 bloqueos únicos" in html and "<th>Código</th>" in html and "CONCENTRACION" in html


def test_dato_faltante_pide_revisar(tmp_path):
    # Dentro de los últimos 3 ciclos: el rechequeo se anota antes que el
    # bloqueo, igual que el ejecutor.
    eventos(tmp_path,
            {"ts": "2026-09-18T14:57:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:58:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:59:00Z", "tipo": "rechequeo"},
            _bloqueo("2026-09-18T14:58:30Z", "AAA", "DATO_FALTANTE:niveles", "niveles_ausentes"),
            _bloqueo("2026-09-18T14:59:10Z", "BBB", "TICKER_COMPROMETIDO", "ticker_comprometido"))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "alerta"
    assert "revisar: DATO_FALTANTE:niveles" in riesgo["detalle"]
    assert ctx["riesgo"]["dato_faltante"] == ["DATO_FALTANTE:niveles"]
    html = bd.render(ctx)
    assert "<b>revisar:</b> DATO_FALTANTE:niveles" in html
    # Con algo que revisar, el resumen SÍ va en rojo de alarma.
    assert '<div class="nota">2 bloqueos únicos · 2 eventos' in html


def test_codigo_nuevo_pide_revisar_y_el_legado_se_mapea(tmp_path):
    # Un evento viejo solo con `limite` conocido se mapea al catálogo (no es
    # nuevo); un código que el catálogo no conoce sí pide revisar.
    eventos(tmp_path,
            {"ts": "2026-09-18T14:57:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:58:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:59:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:58:30Z", "tipo": "bloqueo_riesgo", "ticker": "AAA", "limite": "maximo_posiciones"},
            _bloqueo("2026-09-18T14:59:10Z", "BBB", "LIMITE_INVENTADO", "inventado"))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    r = ctx["riesgo"]
    assert {f["codigo"] for f in r["unicos"]} == {"MAXIMO_POSICIONES", "LIMITE_INVENTADO"}
    assert r["codigos_nuevos"] == ["LIMITE_INVENTADO"] and r["revisar"] is True
    assert _etapas(ctx)["Riesgo"]["estado"] == "alerta"
    assert "motivo nuevo: LIMITE_INVENTADO" in _etapas(ctx)["Riesgo"]["detalle"]


def test_capacidad_llena_se_resume_una_linea_con_desde_hasta_y_corridas(tmp_path):
    lineas = [{"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"}]
    for m in range(30, 55):
        lineas.append({"ts": f"2026-09-18T14:{m:02d}:05Z", "tipo": "capacidad_llena", "codigo": "MAXIMO_POSICIONES",
                       "limite": "maximo_posiciones", "motivo": "5 posiciones", "n_pendientes": 8})
    eventos(tmp_path, *lineas)
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "ok"
    # La última corrida es 14:54 y la ventana activa arranca después: no
    # sigue lleno. El hasta es lo que evita leer "desde 14:30" como vigente.
    assert riesgo["detalle"] == (
        "0 bloqueos únicos (0 eventos) hoy · "
        "historial: MAXIMO_POSICIONES desde 14:30 hasta 14:54 (25 corridas)"
    )
    cap = ctx["riesgo"]["capacidad"]
    assert len(cap) == 1 and cap[0]["desde"] == "14:30" and cap[0]["hasta"] == "14:54" and cap[0]["corridas"] == 25
    assert cap[0]["activo"] is False
    html = bd.render(ctx)
    assert "desde 14:30 hasta 14:54 (25 corridas)" in html
    assert "resuelto" in html and "nota-historial" in html
    assert "Capacidad llena: <b>MAXIMO_POSICIONES</b>" not in html
    assert '<div class="nota">' not in html
    assert '<div class="nota-info">0 bloqueos únicos · 0 eventos</div>' in html


def _rechequeos_recientes():
    """Tres ciclos del vigía pegados a AHORA (15:00). La ventana activa
    abre en el primero."""
    return [
        {"ts": "2026-09-18T14:57:00Z", "tipo": "rechequeo"},
        {"ts": "2026-09-18T14:58:00Z", "tipo": "rechequeo"},
        {"ts": "2026-09-18T14:59:00Z", "tipo": "rechequeo"},
    ]


def test_dato_faltante_de_horas_antes_no_pinta_revisar_y_queda_en_gris(tmp_path):
    # El caso del 28/9: miles de DATO_FALTANTE que pararon por la mañana
    # y el vigía sigue ciclando. La tarjeta no se queda en rojo.
    eventos(tmp_path,
            *_rechequeos_recientes(),
            _bloqueo("2026-09-18T14:10:00Z", "AAA", "DATO_FALTANTE:ultimos_niveles_ts", "niveles_rancios"),
            _bloqueo("2026-09-18T14:20:00Z", "AAA", "DATO_FALTANTE:ultimos_niveles_ts", "niveles_rancios"))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "ok"
    assert riesgo["estado"] != "alerta"
    assert "revisar" not in riesgo["detalle"]
    assert ctx["riesgo"]["revisar"] is False
    assert ctx["riesgo"]["dato_faltante"] == []
    assert len(ctx["riesgo"]["unicos"]) == 1
    html = bd.render(ctx)
    assert '<div class="nota">' not in html
    assert 'class="historial"' in html
    assert "DATO_FALTANTE:ultimos_niveles_ts" in html
    assert ">14:10<" in html and ">14:20<" in html
    assert "resuelto" in html
    assert ">Revisar<" not in html


def test_dato_faltante_reciente_en_la_ventana_pide_revisar(tmp_path):
    eventos(tmp_path,
            *_rechequeos_recientes(),
            _bloqueo("2026-09-18T14:58:30Z", "AAA", "DATO_FALTANTE:ultimos_niveles_ts", "niveles_rancios"))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "alerta"
    assert "revisar: DATO_FALTANTE:ultimos_niveles_ts" in riesgo["detalle"]
    html = bd.render(ctx)
    assert ">Revisar<" in html
    assert "<b>revisar:</b> DATO_FALTANTE:ultimos_niveles_ts" in html
    assert "Activo ahora" in html


def test_mercado_cerrado_solo_es_informativo_y_no_pide_revisar(tmp_path):
    lineas = list(_rechequeos_recientes())
    for ts in ("2026-09-18T14:57:05Z", "2026-09-18T14:58:05Z", "2026-09-18T14:59:05Z"):
        lineas.append({"ts": ts, "tipo": "capacidad_llena", "codigo": "MERCADO_CERRADO",
                       "limite": "mercado_cerrado", "motivo": "el mercado está cerrado"})
    eventos(tmp_path, *lineas)
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "info"
    assert ctx["riesgo"]["revisar"] is False
    assert ctx["riesgo"]["capacidad"] == []
    assert ctx["riesgo"]["informativos"][0]["activo"] is True
    assert "mercado cerrado desde 14:57 hasta 14:59 (3 corridas)" in riesgo["detalle"]
    assert "capacidad llena" not in riesgo["detalle"]
    assert "revisar" not in riesgo["detalle"]
    html = bd.render(ctx)
    assert 'class="punto info"' in html
    assert ">Info<" in html
    assert ">Revisar<" not in html
    assert "Capacidad llena" not in html
    assert '<div class="nota">' not in html
    assert '<div class="nota-info">Mercado cerrado · <b><span title="MERCADO_CERRADO">Mercado cerrado</span></b>' in html
    assert "desde 14:57 hasta 14:59 (3 corridas)" in html


def test_maximo_posiciones_terminado_muestra_hasta_y_no_esta_activo(tmp_path):
    lineas = list(_rechequeos_recientes())
    for m in (20, 30, 40):
        lineas.append({"ts": f"2026-09-18T14:{m:02d}:05Z", "tipo": "capacidad_llena",
                       "codigo": "MAXIMO_POSICIONES", "limite": "maximo_posiciones",
                       "motivo": "5 posiciones"})
    eventos(tmp_path, *lineas)
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "ok"
    cap = ctx["riesgo"]["capacidad"]
    assert len(cap) == 1 and cap[0]["activo"] is False
    assert "historial: MAXIMO_POSICIONES desde 14:20 hasta 14:40 (3 corridas)" in riesgo["detalle"]
    html = bd.render(ctx)
    assert "desde 14:20 hasta 14:40 (3 corridas)" in html
    assert "nota-historial" in html and "resuelto" in html
    assert "Capacidad llena: <b>MAXIMO_POSICIONES</b>" not in html
    assert '<div class="nota">' not in html


def test_maximo_posiciones_aun_en_ventana_esta_activo_y_no_es_rojo(tmp_path):
    lineas = list(_rechequeos_recientes())
    lineas.append({"ts": "2026-09-18T14:59:05Z", "tipo": "capacidad_llena", "codigo": "MAXIMO_POSICIONES",
                   "limite": "maximo_posiciones", "motivo": "5 posiciones"})
    eventos(tmp_path, *lineas)
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert _etapas(ctx)["Riesgo"]["estado"] == "ok"
    cap = ctx["riesgo"]["capacidad"][0]
    assert cap["activo"] is True
    assert "capacidad llena: MAXIMO_POSICIONES desde 14:59 hasta 14:59 (1 corridas)" in _etapas(ctx)["Riesgo"]["detalle"]
    html = bd.render(ctx)
    assert '<div class="nota-info">Capacidad llena: <b>MAXIMO_POSICIONES</b>' in html
    assert "desde 14:59 hasta 14:59 (1 corridas)" in html
    assert '<div class="nota">' not in html


def test_sin_log_de_eventos_dice_sin_datos_recientes_y_no_verde(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "sin-datos"
    assert riesgo["estado"] != "ok"
    assert "sin datos recientes" in riesgo["detalle"]
    html = bd.render(ctx)
    assert "Sin datos recientes" in html
    assert "Ningún límite ha bloqueado" not in html


def test_mismo_ticker_y_codigo_con_distinto_creado_en_cuenta_una_fila(tmp_path):
    # Dos TRIGGERED del mismo símbolo (distinto creado_en) en el mismo
    # ciclo no son dos bloqueos: la sombra no duplica la fila.
    eventos(tmp_path,
            *_rechequeos_recientes(),
            {**_bloqueo("2026-09-18T14:58:30Z", "AAA", "CONCENTRACION", "concentracion"),
             "creado_en": "2026-09-18T10:00:00+00:00"},
            {**_bloqueo("2026-09-18T14:58:31Z", "AAA", "CONCENTRACION", "concentracion"),
             "creado_en": "2026-09-18T12:30:00+00:00"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    unicos = ctx["riesgo"]["unicos"]
    assert len(unicos) == 1
    assert unicos[0]["ticker"] == "AAA" and unicos[0]["codigo"] == "CONCENTRACION"
    assert unicos[0]["veces"] == 1
    assert ctx["riesgo"]["eventos"] == 1
    assert _etapas(ctx)["Riesgo"]["detalle"] == "1 bloqueos únicos (1 eventos) hoy"


def test_github_actions_atrasado_no_pinta_el_hunter_en_rojo_ni_es_problema(tmp_path):
    # 2026-09-24: GitHub dejó de ser el escáner (ahora escanea el VPS) y quedó
    # solo como respaldo manual que puede pasar horas sin correr. Con el
    # escaneo del VPS fresco, que GitHub lleve 60 min NO es alerta ni problema:
    # antes daba un rojo permanente falso en el Hunter.
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 50, tzinfo=timezone.utc))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca,
                       gha=gha_ok(datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)))
    hunter = _hunter(ctx)
    assert hunter["estado"] == "ok"                       # lo decide el escaneo del VPS
    assert "GitHub lleva" not in hunter["detalle"]        # sin la alerta vieja
    assert "#218" in hunter["nota"] and "no se usa" in hunter["nota"]   # dato aparte, rotulado
    assert 'class="mono historico">respaldo en GitHub (no se usa): corrida #218' in bd.render(ctx)
    assert not any("momentum_hunter.yml" in p for p in ctx["problemas"])   # y sin banner "Datos incompletos"
    # La señal real de un Hunter caído es que el ESCANEO DEL VPS se atrase;
    # eso lo cubren los tests de frescura del escaneo. Acá solo se fija que
    # GitHub, como respaldo, ya no dispara el rojo.


def test_el_panel_ofrece_tema_oscuro_por_boton_y_por_preferencia_del_sistema(tmp_path):
    """El panel trae tema oscuro (pedido del dueño 2026-09-24): botón para
    alternarlo, elección guardada en el navegador y respeto de la
    preferencia del sistema. El modo claro no cambia: las mismas variables
    se redefinen, y los colores fijos de las gráficas que se romperían en
    oscuro pasan a clases que siguen las variables."""
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    # Botón visible y accesible.
    assert 'id="tema-toggle"' in html and 'aria-label="Cambiar entre tema claro y oscuro"' in html
    # Se aplica antes de pintar (sin parpadeo en el refresco de 60 s) y se
    # guarda/lee del navegador.
    assert 'localStorage.getItem("tema")' in html and 'localStorage.setItem("tema"' in html
    # Oscuro por sistema (salvo elección clara) y por elección manual.
    assert "@media (prefers-color-scheme:dark)" in html
    assert ':root:not([data-theme="light"])' in html
    assert ':root[data-theme="dark"]' in html
    # Colores fijos de SVG que se romperían en oscuro, ahora por clase que
    # sigue las variables (las definiciones van siempre en el CSS).
    assert ".rejilla{stroke:var(--rejilla)}" in html
    assert ".ink{fill:var(--tinta)}" in html
    assert ".zona-riesgo{fill:var(--zona-riesgo)}" in html
    # Colores de serie/velas/marcas de las gráficas por variables (2026-09-24).
    assert ".serie{stroke:var(--acento)}" in html
    assert ".vela-sube{fill:var(--verde);stroke:var(--verde)}" in html
    assert ".m-entrada{stroke:var(--gris)}" in html
    # Y los colores fijos que rompían el oscuro ya no se emiten en ningún SVG.
    for fijo in ('fill="#16171a"', 'stroke="#bdb9ad"', 'stroke="#2451b8"', 'stroke="#5c5b55"',
                 'fill="#2451b8"', 'style="fill:#'):
        assert fijo not in html, fijo
    # El modo claro sigue igual: variable de fondo crema intacta.
    assert "--fondo:#f3f1ea" in html


# ───────── 2026-09-29: ventana, VWAP, objetivo, riesgo, uso de límites ─────────

def _serie(inicio, n, vol=1000.0):
    from datetime import timedelta
    ts = [(inicio + timedelta(minutes=i)).isoformat(timespec="seconds") for i in range(n)]
    return {"timestamps": ts, "open": [10.0] * n, "close": [10.0] * n,
            "high": [11.0] * n, "low": [9.0] * n, "volume": [vol] * n}


def test_recorte_quita_el_premarket_y_deja_15_min_antes_de_la_apertura():
    # 12:00 UTC = 08:00 ET. Apertura 13:30 UTC; corte 13:15 UTC.
    velas = _serie(datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc), 120)
    rec = bd.recortar_a_sesion(velas)
    assert rec["timestamps"][0] == "2026-09-29T13:15:00+00:00"
    assert len(rec["close"]) == 120 - 75
    assert all(len(rec[k]) == len(rec["close"]) for k in ("open", "high", "low", "volume", "timestamps"))


def test_recorte_no_deja_un_grafico_vacio_si_solo_hay_premarket():
    velas = _serie(datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc), 30)   # termina 08:29 ET
    assert bd.recortar_a_sesion(velas) is velas


def test_vwap_empieza_en_la_apertura_y_se_corta_si_falta_un_volumen():
    velas = _serie(datetime(2026, 9, 29, 13, 28, tzinfo=timezone.utc), 5)
    velas["high"] = [11.0, 11.0, 12.0, 14.0, 14.0]
    velas["low"] = [9.0, 9.0, 12.0, 14.0, 14.0]
    velas["close"] = [10.0, 10.0, 12.0, 14.0, 14.0]
    velas["volume"] = [500.0, 500.0, 100.0, 300.0, None]
    v = bd.vwap_de_sesion(velas)
    assert v[0] is None and v[1] is None            # 13:28 y 13:29 UTC: antes de 09:30 ET
    assert v[2] == pytest.approx(12.0)
    assert v[3] == pytest.approx((12 * 100 + 14 * 300) / 400)
    assert v[4] is None                              # volumen ausente: no es cero


def test_etiquetas_no_se_enciman_y_conservan_su_orden():
    ys = [50.0, 52.0, 51.0, 180.0]
    nuevas = bd.repartir_etiquetas(ys, 20.0, 190.0)
    ordenadas = sorted(nuevas)
    assert all(b - a >= bd._SEPARACION_ETIQUETAS - 1e-9 for a, b in zip(ordenadas, ordenadas[1:]))
    # 50 < 51 < 52 se mantiene
    assert nuevas[0] < nuevas[2] < nuevas[1]
    assert all(20.0 <= y <= 190.0 for y in nuevas)


def test_riesgo_de_una_posicion():
    r = bd.riesgo_de(24.77, 24.54, 25.23, 29)
    assert r["riesgo"] == pytest.approx(6.67) and r["rr"] == pytest.approx(2.0, abs=0.01)
    assert bd.riesgo_de(24.77, None, 25.23, 29) == {"riesgo": None, "rr": None}
    subido = bd.riesgo_de(24.77, 24.90, 25.23, 29)   # stop ya por encima de la entrada
    assert subido["riesgo"] < 0 and subido["rr"] is None


def test_uso_de_limites_cuenta_como_el_ejecutor():
    class Cfg:
        maximo_posiciones_abiertas = 5
        maximo_pct_efectivo_por_posicion = 0.15
    posiciones = [{"symbol": "CCL"}, {"symbol": "ETN"}]
    abiertas = [{"symbol": "CCL"}, {"symbol": "NVS"}, {"symbol": "NTAP"}]   # el TP de CCL no cuenta doble
    filas = [{"ticker": "CCL", "valor": 723.84, "riesgo": 6.67},
             {"ticker": "ETN", "valor": 432.78, "riesgo": 3.57}]
    lim = bd.uso_de_limites(posiciones, abiertas, filas, 4913.71, 3700.0, Cfg())
    assert lim["comprometidos"] == ["CCL", "ETN", "NTAP", "NVS"] and lim["tope_cupo"] == 5
    assert lim["concentracion"][0]["pct"] == pytest.approx(14.73, abs=0.01)
    assert lim["tope_concentracion_pct"] == 15
    assert lim["riesgo_total"] == pytest.approx(10.24)
    # Un riesgo desconocido no se suma como 0.
    filas[1]["riesgo"] = None
    assert bd.uso_de_limites(posiciones, abiertas, filas, 4913.71, 3700.0, Cfg())["riesgo_total"] is None
    # Órdenes ilegibles: el cupo es "sin dato", no 2.
    assert bd.uso_de_limites(posiciones, None, filas, 4913.71, 3700.0, Cfg())["comprometidos"] is None


def test_objetivo_del_bracket_se_dibuja_y_la_fila_trae_riesgo(tmp_path):
    _watchlist_con_ruptura(tmp_path)
    compra = _compra()
    compra["legs"].append({"id": "leg-tp", "symbol": "AAA", "side": "sell", "type": "limit",
                           "status": "new", "limit_price": "5.30", "submitted_at": "2026-09-18T14:32:00Z"})
    get = alpaca_falso({"equity": "5000", "cash": "4000"},
                       **{"/v2/positions": [_posicion()], "/v2/orders": [compra]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    assert ctx["operaciones"][0]["marcas"]["objetivo"] == 5.30
    html = bd.render(ctx)
    svg = _svg_velas(html, "AAA")
    assert "marca-objetivo" in svg and "objetivo $5.30" in svg
    assert "marca-vwap" in svg
    assert "Riesgo al stop" in html and "Cupo de jugadas" in html


def test_watchlist_pliega_las_terminales_y_pone_las_disparadas_primero(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [
        _entrada("VIG", "watching"), _entrada("DIS", "triggered"), _entrada("EXP", "expired")]}))
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "5000"}), velas=velas_ok))
    bloque = html.split("Watchlist actual", 1)[1].split("Latencia", 1)[0]
    # 2026-10-01: disparadas a la vista; vigilando y terminales plegadas.
    disparadas, resto = bloque.split("<details", 1)
    vigilando, terminales = resto.split("<details class='terminales'", 1)
    assert ">DIS<" in disparadas and ">VIG<" not in disparadas and "disparada" in disparadas
    assert "id='wl-vigilando'" in vigilando and ">VIG<" in vigilando and "1 ticker esperando" in vigilando
    # EXP cambió hoy (13:40 UTC): va plegada, no entre las activas.
    assert ">EXP<" in terminales and ">EXP<" not in disparadas + vigilando


def test_panel_lleva_la_hora_de_generacion_para_el_aviso_de_viejo(tmp_path):
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "5000"}), velas=velas_ok))
    assert f'data-generado="{int(AHORA.timestamp())}"' in html and 'id="panel-viejo" hidden' in html


def test_latencia_en_lenguaje_llano_con_veredicto():
    # Misma medida (velas de 1 min = minutos); solo cambia cómo se dice.
    base = {"lat_mediana": 4.0, "lat_fuera": 0, "presupuesto": 8.0}
    assert "Bien: todas las compras de hoy" in bd._veredicto_latencia(base)
    tarde = bd._veredicto_latencia({**base, "lat_fuera": 2})
    assert 'class="nota"' in tarde and "2 compras llegaron tarde" in tarde and "más de 8 min" in tarde
    assert "1 compra llegó tarde" in bd._veredicto_latencia({**base, "lat_fuera": 1})
    # Sin compras con el dato completo no hay veredicto, ni un "Bien" falso.
    assert bd._veredicto_latencia({**base, "lat_mediana": None, "lat_fuera": None}) == ""


# ───────── 2026-09-29: la escala sale de la sesión, no del premarket (CCL) ─────────

def _sesion_con_premarket_raro():
    # 13:15–13:29 UTC premarket con un precio aislado de $21.59; desde 13:30
    # (09:30 ET) la sesión se mueve entre 24 y 25.
    velas = _serie(datetime(2026, 9, 29, 13, 15, tzinfo=timezone.utc), 40)
    for campo, valor in (("open", 24.5), ("close", 24.5), ("high", 25.0), ("low", 24.0)):
        velas[campo] = [valor] * 40
    for i in range(3):
        velas["low"][i] = 21.59
    return velas


def test_escala_ignora_el_premarket_y_lo_avisa():
    velas = _sesion_con_premarket_raro()
    assert bd.rango_de_escala(velas) == (24.0, 25.0)
    marcas = {"ruptura": None, "entrada_precio": None, "entrada_hora": None, "stop": None, "objetivo": None}
    ahora = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
    svg = bd._grafico_velas({"velas": velas}, marcas, ZoneInfo("UTC"), ahora, clave="CCL")
    # El eje va de la sesión, no de $21.59.
    assert "$24.00" in svg and "$25.00" in svg and ">$21.59<" not in svg
    # Las velas raras siguen ahí, recortadas al área, y la nota dice el precio.
    assert 'clip-path="url(#recorte-CCL)"' in svg
    assert "premarket fuera de escala: mín $21.59" in svg
    # Un objetivo a 2R de una sesión ajustada sigue siendo una línea, no una nota al borde.
    con_objetivo = bd._grafico_velas({"velas": velas}, {**marcas, "objetivo": 26.5}, ZoneInfo("UTC"), ahora, clave="CCL")
    assert '<line class="marca-objetivo' in con_objetivo and "fuera del gráfico" not in con_objetivo


def test_escala_sin_sesion_suficiente_usa_toda_la_serie():
    # Antes de la apertura no hay velas de sesión: no se inventa una escala.
    velas = _serie(datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc), 30)
    velas["low"][0] = 7.5
    assert bd.rango_de_escala(velas) == (7.5, 11.0)
    marcas = {"ruptura": None, "entrada_precio": None, "entrada_hora": None, "stop": None, "objetivo": None}
    svg = bd._grafico_velas({"velas": velas}, marcas, ZoneInfo("UTC"), datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc))
    assert "fuera de escala" not in svg



# ───────── 2026-09-29: ruptura al decidir vs actual ─────────

def test_ruptura_al_decidir_sale_del_evento_orden_y_la_actual_va_aparte(tmp_path):
    # NVS: el ejecutor decidió con la EMA9 en $144.78; la watchlist, que se
    # sigue refrescando, dice ahora $145.24. El panel muestra las dos.
    _watchlist_con_ruptura(tmp_path, ruptura=5.24)
    eventos(tmp_path, {"ts": "2026-09-18T14:32:05+00:00", "tipo": "orden", "ticker": "AAA", "estado": "enviada",
                       "lado": "buy", "ruptura_al_decidir": 5.08, "patron_al_decidir": "momentum_continuo",
                       "vwap_al_decidir": 5.15, "precio_entrada": 5.12})
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": [_compra()]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    m = ctx["operaciones"][0]["marcas"]
    assert m["ruptura"] == 5.08 and m["ruptura_actual"] == 5.24
    assert m["patron"] == "momentum_continuo" and m["vwap_al_decidir"] == 5.15
    html = bd.render(ctx)
    svg = _svg_velas(html, "AAA")
    assert "ruptura al decidir $5.08" in svg and "ruptura actual $5.24" in svg
    assert "Ruptura al decidir" in html and "actual $5.24" in html
    assert "patrón momentum_continuo · VWAP al decidir $5.15" in html


def test_ruptura_al_decidir_sin_dato_no_se_rellena_con_la_actual(tmp_path):
    # Una orden de antes de este registro: el evento no trae el campo.
    _watchlist_con_ruptura(tmp_path, ruptura=5.24)
    eventos(tmp_path, {"ts": "2026-09-18T14:32:05+00:00", "tipo": "orden", "ticker": "AAA",
                       "estado": "enviada", "lado": "buy"})
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": [_compra()]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    m = ctx["operaciones"][0]["marcas"]
    assert m["ruptura"] is None and m["ruptura_actual"] == 5.24
    html = bd.render(ctx)
    velas_html = html.split("Velas de posiciones abiertas", 1)[1].split("Watchlist", 1)[0]
    assert "Ruptura al decidir</span><b>sin dato</b>" in velas_html


# ───────── 2026-10-01: plan de pago; un 401/403 del feed es una falla ─────────

@pytest.mark.parametrize("codigo", ["auth", "http_401", "http_403"])
def test_feed_rechazado_cae_a_yahoo_respaldo_y_reintenta_a_los_15_min(tmp_path, codigo):
    from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca
    llamadas = []

    def alpaca_403(ticker):
        llamadas.append(ticker)
        raise ErrorDatosAlpaca(codigo)

    cache = tmp_path / "cache"
    r = dv.obtener("AAA", AHORA, cache, ttl_seg=0, fuente=lambda t: _velas(), alpaca=alpaca_403)
    assert r["origen_fuente"] == "yahoo (respaldo)"
    assert bd._marca_fuente_velas(r["origen_fuente"]) == "Yahoo (respaldo)"
    assert "Alpaca rechazó el feed" in r["aviso_feed"] and "plan gratis" not in r["aviso_feed"]
    # Un minuto después no se vuelve a pedir el feed...
    r2 = dv.obtener("BBB", AHORA + timedelta(minutes=1), cache, ttl_seg=0,
                    fuente=lambda t: _velas(), alpaca=alpaca_403)
    assert llamadas == ["AAA"] and r2["aviso_feed"] == r["aviso_feed"]
    # ...pero a los 15 min sí: un cambio de plan o de claves se ve solo.
    dv.obtener("CCC", AHORA + timedelta(minutes=16), cache, ttl_seg=0,
               fuente=lambda t: _velas(), alpaca=alpaca_403)
    assert llamadas == ["AAA", "CCC"]


def test_una_pausa_vieja_de_6_h_se_acorta_al_desplegar(tmp_path):
    cache = tmp_path / "cache"
    dv._escribir_json(cache / dv.ARCHIVO_PAUSA_FEED, {
        "hasta": (AHORA + timedelta(hours=6)).isoformat(), "desde": AHORA.isoformat(), "codigo": "http_403"})
    llamadas = []

    def alpaca_ok(ticker):
        llamadas.append(ticker)
        return _velas()

    r = dv.obtener("AAA", AHORA + timedelta(minutes=20), cache, ttl_seg=0,
                   fuente=lambda t: _velas(), alpaca=alpaca_ok)
    assert llamadas == ["AAA"] and r["origen_fuente"] == "alpaca-sip" and r.get("aviso_feed") is None


def test_otro_fallo_del_feed_se_avisa_con_su_codigo_y_sigue_siendo_respaldo(tmp_path):
    from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca

    def alpaca_500(ticker):
        raise ErrorDatosAlpaca("http_500")

    r = dv.obtener("AAA", AHORA, tmp_path / "cache", ttl_seg=0, fuente=lambda t: _velas(), alpaca=alpaca_500)
    assert r["origen_fuente"] == "yahoo (respaldo)"
    assert "http_500" in r["aviso_feed"]
    assert not (tmp_path / "cache" / dv.ARCHIVO_PAUSA_FEED).exists()


def test_el_aviso_del_feed_rechazado_va_en_rojo_y_no_menciona_el_plan_gratis(tmp_path):
    aviso = dv.aviso_feed_rechazado("http_403", AHORA)

    def velas_rechazo(ticker):
        return {"velas": _velas(), "obtenido": AHORA, "origen": "fuente", "origen_fuente": "yahoo (respaldo)",
                "error": None, "aviso_feed": aviso}

    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion("AAA"), _posicion("BBB")],
                                             "/v2/orders": []})
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_rechazo))
    assert html.count(aviso) == 1 and '<div class="nota banda-datos"' in html
    assert "plan gratis" not in html and "el plan no da SIP" not in html



# ───────── 2026-09-29: un solo P&L del día ─────────

def test_la_grafica_de_hoy_termina_en_el_equity_en_vivo_y_cuadra_con_el_pnl(tmp_path):
    # Arriba +$13.60 (equity en vivo vs cierre anterior) y la gráfica
    # +$11.93 (última vela de 5 min): ahora la gráfica termina en vivo.
    get = alpaca_falso({"equity": "5013.60", "last_equity": "5000"},
                       **{HIST: _historial([5000, 5006, 5011.93], base=5000)})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["equity_dia"]["vivo"] == (AHORA, 5013.60)
    assert [v for _, v in ctx["equity_dia"]["puntos"]] == [5000, 5006, 5011.93]   # historial intacto
    html = bd.render(ctx)
    assert "Último · en vivo 15:00" in html and "+$13.60" in html
    resumen = html.split("Equity de hoy", 1)[1].split("Equity del último mes", 1)[0]
    assert "+$11.93" not in resumen
    assert 'class="serie-vivo"' in _svgs_equity(html)[0]


def test_sin_equity_o_con_la_sesion_de_otro_dia_no_hay_punto_en_vivo(tmp_path):
    hist = {"puntos": [(AHORA - timedelta(minutes=30), 5000.0)], "base": 5000.0, "es_hoy": True}
    assert "vivo" not in bd.con_punto_en_vivo(hist, None, AHORA)
    assert "vivo" not in bd.con_punto_en_vivo({**hist, "es_hoy": False}, 5010.0, AHORA)
    assert "vivo" not in bd.con_punto_en_vivo({**hist, "puntos": []}, 5010.0, AHORA)
    # Sin punto en vivo, "Último" dice de qué vela es.
    assert "Último · vela de las 14:30" in bd._resumen_equity(hist, ZoneInfo("UTC"))


# ───────── 2026-09-29: latencia, espera de cupo separada de la reacción ─────────

def _cupo_lleno_cada_minuto(desde, hasta):
    t, salida = desde, []
    while t <= hasta:
        salida.append({"ts": t.isoformat(), "tipo": "capacidad_llena", "codigo": "MAXIMO_POSICIONES",
                       "limite": "maximo_posiciones"})
        t += timedelta(minutes=1)
    return salida


def test_la_espera_por_cupo_no_cuenta_como_tarde(tmp_path):
    # RCL: ruptura 12:26, cupo lleno de 12:30 a 14:39, compra 14:41 (145 min).
    # CCL: compra sin cupo lleno en su ventana, 4 min de reacción.
    orden_rcl = {"ts": "2026-09-18T14:41:00+00:00", "tipo": "orden", "ticker": "RCL", "estado": "enviada",
                 "velas": 135.0, "medida": "ruptura_a_orden", "velas_desde_ruptura": 2, "velas_desde_disparo": 133.0}
    orden_ccl = {"ts": "2026-09-18T12:10:00+00:00", "tipo": "orden", "ticker": "CCL", "estado": "enviada",
                 "velas": 4.0, "medida": "ruptura_a_orden", "velas_desde_ruptura": 2, "velas_desde_disparo": 2.0}
    eventos(tmp_path, orden_ccl,
            *_cupo_lleno_cada_minuto(datetime(2026, 9, 18, 12, 30, tzinfo=timezone.utc),
                                     datetime(2026, 9, 18, 14, 39, tzinfo=timezone.utc)),
            orden_rcl)
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    rcl = next(d for d in ctx["lat_detalle"] if d["ticker"] == "RCL")
    assert rcl["total"] == 135.0 and rcl["espera"] == 130.0 and rcl["reaccion"] == 5.0
    assert ctx["lat_fuera"] == 0                       # nadie llegó tarde por reaccionar
    assert dict(ctx["lat"]) == {"CCL": 4.0, "RCL": 5.0}
    html = bd.render(ctx)
    assert "Esperando cupo (no cuenta como tarde): RCL 130 min." in html
    assert "Bien: todas las compras de hoy salieron dentro del límite." in html


def test_sin_eventos_de_cupo_toda_la_latencia_es_reaccion(tmp_path):
    eventos(tmp_path, {"ts": "2026-09-18T14:41:00+00:00", "tipo": "orden", "ticker": "RCL", "estado": "enviada",
                       "velas": 88.0, "medida": "ruptura_a_orden"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["lat_detalle"][0]["espera"] == 0 and ctx["lat_fuera"] == 1


def test_compras_sin_barra_se_listan_con_su_motivo(tmp_path):
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00+00:00", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:10:00+00:00", "tipo": "orden", "ticker": "LEN", "estado": "enviada",
             "velas": None, "medida": "ruptura_a_orden", "velas_desde_ruptura": None, "velas_desde_disparo": 3.0})
    compra_sin_evento = {"id": "o-cdns", "symbol": "CDNS", "side": "buy", "status": "filled",
                         "submitted_at": "2026-09-18T14:20:00Z"}
    get = alpaca_falso({"equity": "5000"}, **{"/v2/orders": [compra_sin_evento]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get)
    assert ctx["lat_sin_barra"] == [
        {"ticker": "LEN", "motivo": "el hunter no contó las velas desde la ruptura"},
        {"ticker": "CDNS", "motivo": "compra en Alpaca sin evento en el log"}]
    assert "Compras sin barra: LEN (el hunter no contó las velas desde la ruptura) · CDNS" in bd.render(ctx)
    # Sin ninguna compra con dato no hay veredicto; con alguna, no dice "todas".
    ctx2 = {"lat_mediana": 4.0, "lat_fuera": 0, "presupuesto": 8.0, "lat": [("CCL", 4.0)],
            "lat_sin_barra": ctx["lat_sin_barra"]}
    assert "la compra con dato salió dentro del límite (2 sin dato)" in bd._veredicto_latencia(ctx2)


def test_sin_log_no_se_afirma_que_falte_el_evento_de_una_compra(tmp_path):
    compra = {"id": "o-cdns", "symbol": "CDNS", "side": "buy", "status": "filled"}
    ctx = bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "5000"}, **{"/v2/orders": [compra]}))
    assert ctx["lat_sin_barra"] == []


def test_eje_de_latencia_con_pocas_marcas_y_la_barra_atipica_cortada():
    ctx = {"presupuesto": 8.0, "lat_detalle": [
        {"ticker": "CCL", "total": 3.0, "espera": 0.0, "reaccion": 3.0},
        {"ticker": "RCL", "total": 88.0, "espera": 0.0, "reaccion": 88.0}]}
    svg = bd._grafico_latencia(ctx)
    marcas = re.findall(r'text-anchor="end" class="eje">([^<]+)</text>', svg)
    assert marcas == ["0", "8", "16"]                  # no 0,4,...,88 amontonados
    assert "▲ 88" in svg
    # La etiqueta del límite se dibuja después de las barras: no queda tapada.
    assert svg.index("límite 8 min") > svg.rindex('<rect class="barra-')


# ---------------------------------------------------------------- noticias.html

from dashboard import noticias as nh  # noqa: E402


def _registro_noticias(tmp_path, acciones, **corrida):
    ruta = tmp_path / "noticias_leidas.json"
    base = {"version": 1, "corrida_ts": "2026-09-18T14:30:00+00:00", "escrito_ts": "2026-09-18T14:48:00+00:00",
            "fuente": "vps", "acciones": acciones}
    base.update(corrida)
    ruta.write_text(json.dumps({"version": 1, "corridas": [base]}), encoding="utf-8")
    return ruta


ACCIONES_NOTICIAS = [
    {"ticker": "CHPT", "resultado": "con_catalizador", "motivo": None, "tipo": "contrato",
     "keyword": "awarded contract", "n_noticias": 1,
     "noticias": [{"titular": "ChargePoint awarded contract", "fuente": "Reuters",
                   "publicada": "2026-09-18T13:00:00+00:00", "link": "https://x.test/1",
                   "tipo": "contrato", "keyword": "awarded contract", "motivo": None}]},
    {"ticker": "EBAY", "resultado": "sin_catalizador", "motivo": "sin_ancla", "tipo": "insider_buying",
     "keyword": "director buys", "n_noticias": 1, "noticias": []},
    {"ticker": "OLDX", "resultado": "sin_catalizador", "motivo": "fuera_ventana", "tipo": "contrato",
     "keyword": "awarded contract", "n_noticias": 1, "noticias": []},
    {"ticker": "NADA", "resultado": "sin_catalizador", "motivo": "sin_keyword", "tipo": None, "keyword": None,
     "n_noticias": 2, "noticias": [{"titular": "Nada opens office", "fuente": "AP", "publicada": None,
                                    "link": "javascript:alert(1)", "keyword": None, "motivo": "sin_keyword"}]},
    {"ticker": "VACIO", "resultado": "sin_noticias", "n_noticias": 0, "noticias": []},
    {"ticker": "ROTO", "resultado": "error_lectura", "n_noticias": 0, "noticias": []},
    {"ticker": "RARO"},   # todo faltante
    {"ticker": "MIXTO", "resultado": "sin_catalizador", "motivo": "sin_dato", "tipo": "rumor",
     "keyword": "reportedly", "n_noticias": 2, "noticias": []},   # motivos mezclados
]


def test_noticias_filtros_casi_pasan_y_sin_keyword():
    casi = [a["ticker"] for a in nh.casi_pasan(ACCIONES_NOTICIAS)]
    assert casi == ["EBAY", "OLDX"]
    assert [a["ticker"] for a in nh.sin_keyword(ACCIONES_NOTICIAS)] == ["NADA"]
    # Ni con catalizador, ni sin noticias, ni error, ni el faltante caen en "casi".
    assert all(nh.grupo(a) != "casi" for a in ACCIONES_NOTICIAS if a["ticker"] not in casi)


def test_noticias_pagina_con_hora_hace_y_aviso_no_en_vivo(tmp_path):
    ruta = _registro_noticias(tmp_path, ACCIONES_NOTICIAS)
    destino = nh.generar(ruta, tmp_path / "site", AHORA, ZoneInfo("UTC"))
    html = destino.read_text(encoding="utf-8")
    assert destino.name == "noticias.html"
    assert "2026-09-18 14:30 UTC" in html and "hace 12 min" in html
    assert "No es en vivo" in html
    assert "Casi pasan (2)" in html and "Sin keyword (1)" in html and "Todas (8)" in html
    assert "motivo sin dato" in html
    assert 'class="acc g-casi"' in html and 'class="acc g-sinkw"' in html
    assert 'href="https://x.test/1"' in html
    assert 'href="javascript' not in html and "link no válido" in html
    assert "500" in html   # la limitación del 500 silencioso queda escrita


def test_noticias_dato_faltante_es_sin_dato_nunca_cero(tmp_path):
    ruta = _registro_noticias(tmp_path, [{"ticker": "RARO"}], escrito_ts=None, fuente=None)
    html = nh.generar(ruta, tmp_path / "site", AHORA, ZoneInfo("UTC")).read_text(encoding="utf-8")
    fila = html[html.index('class="acc'):html.index("</details>")]
    assert "sin dato" in fila and ">0 <" not in fila and "Sin catalizador" not in fila
    # Sin escrito_ts, la antigüedad sale de la hora de inicio.
    assert "hace 30 min" in html


@pytest.mark.parametrize("contenido", [None, "", "{corrupto", "[]", '{"corridas": "x"}', '{"corridas": []}'])
def test_noticias_archivo_ausente_vacio_o_corrupto_no_rompe(tmp_path, contenido):
    ruta = tmp_path / "noticias_leidas.json"
    if contenido is not None:
        ruta.write_text(contenido, encoding="utf-8")
    corridas, problema = nh.cargar(ruta)
    assert corridas == [] and problema
    html = nh.generar(ruta, tmp_path / "site", AHORA, ZoneInfo("UTC")).read_text(encoding="utf-8")
    assert "Sin registro." in html and "sin dato" in html


def test_una_falla_de_noticias_no_rompe_el_panel(tmp_path, monkeypatch):
    c = cfg(tmp_path, noticias_leidas=tmp_path / "no_existe.json")
    monkeypatch.setattr(bd, "cargar_config", lambda: c)
    monkeypatch.setattr(bd, "construir", lambda ahora, cfg_: {"ahora": AHORA, "problemas": []})
    monkeypatch.setattr(bd, "render", lambda ctx: "<html>panel</html>")
    monkeypatch.setattr(nh, "render", lambda *a, **k: 1 / 0)
    assert bd.main() == 0
    assert (tmp_path / "site" / "index.html").read_text(encoding="utf-8") == "<html>panel</html>"
    assert not (tmp_path / "site" / "noticias.html").exists()


def test_cabecera_del_panel_tiene_el_link_a_noticias(tmp_path):
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    assert '<a class="pildora" href="noticias.html">Noticias leídas</a>' in html


# ───── Stop de pérdida diaria (2026-10-01) ─────

def _medicion_stop(ts, pnl, activo=False, activado_en=None, codigo=None, bloquea=None, modo="enforce",
                   fecha="2026-09-18"):
    return {"ts": ts, "tipo": "stop_diario", "modo": modo, "fecha_sesion": fecha, "pct": 1.0,
            "pnl": pnl, "pnl_pct": None if pnl is None else round(pnl / 50.0, 3), "umbral_usd": 50.0,
            "activo": activo, "bloquea": activo if bloquea is None else bloquea,
            "activado_en": activado_en, "codigo": codigo, "motivo": "m"}


def test_panel_stop_diario_inactivo_con_pnl_umbral_y_ultimo_chequeo(tmp_path):
    eventos(tmp_path, *_rechequeos_recientes(),
            _medicion_stop("2026-09-18T14:58:00Z", -10.0),
            _medicion_stop("2026-09-18T14:59:00Z", -19.67))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    sd = ctx["stop_diario"]
    assert sd["sin_datos"] is False and sd["activo"] is False and sd["pnl"] == -19.67
    assert sd["ultimo"] == "14:59" and sd["desde"] is None
    html = bd.render(ctx)
    assert "Stop diario (1.00 %): P&amp;L hoy −$19.67" in html
    assert "vs umbral −$50.00 · inactivo · último chequeo 14:59" in html


def test_panel_stop_diario_activo_desde_y_codigo_conocido(tmp_path):
    eventos(tmp_path, *_rechequeos_recientes(),
            _medicion_stop("2026-09-18T14:59:00Z", -60.0, activo=True, activado_en="2026-09-18T14:40:00+00:00",
                           codigo="PERDIDA_DIARIA"),
            {"ts": "2026-09-18T14:59:01Z", "tipo": "capacidad_llena", "codigo": "PERDIDA_DIARIA",
             "limite": "perdida_diaria", "motivo": "stop diario activo", "n_pendientes": 2})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["stop_diario"]["activo"] is True and ctx["stop_diario"]["desde"] == "14:40"
    riesgo = ctx["riesgo"]
    assert riesgo["codigos_nuevos"] == [] and riesgo["revisar"] is False
    assert _etapas(ctx)["Riesgo"]["estado"] == "ok"
    assert "stop diario activo: PERDIDA_DIARIA" in _etapas(ctx)["Riesgo"]["detalle"]
    html = bd.render(ctx)
    assert "<b>ACTIVO desde 14:40</b> — sin entradas nuevas hoy" in html
    assert 'Stop diario activo: <b><span title="PERDIDA_DIARIA">Stop diario</span></b>' in html


def test_panel_stop_diario_sin_datos_y_dato_faltante(tmp_path):
    # Medición de ayer: no cuenta como hoy.
    eventos(tmp_path, _medicion_stop("2026-09-18T14:59:00Z", -1.0, fecha="2026-09-17"))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert ctx["stop_diario"] == {"sin_datos": True}
    assert "Stop diario: sin medición hoy" in bd.render(ctx)

    eventos(tmp_path, _medicion_stop("2026-09-18T14:59:00Z", None, codigo="DATO_FALTANTE:last_equity",
                                     bloquea=True))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    html = bd.render(ctx)
    assert ctx["stop_diario"]["dato_faltante"] is True
    assert '<div class="nota">Stop diario (1.00 %): P&amp;L hoy sin dato' in html


# ───────────── Control de riesgo y aprendizaje (2026-10-01, sombra) ─────────────

def _aprendizaje(tmp_path, reporte=True, ajustes=True, regimen=True, sombra=True):
    d = tmp_path / "apr"
    d.mkdir(exist_ok=True)
    if reporte:
        (d / "ultimo_reporte.json").write_text(json.dumps({
            "fecha": "2026-09-18", "estadisticas": {
                "global": {"n": 21, "sesiones": 4, "r_medio": -0.2, "pnl": -64.67},
                "n_minimo": 20, "sesiones_minimas": 5,
                "segmentos": [{"dimension": "patron", "valor": "<b>orb</b>", "n": 5, "sesiones": 3, "wr": 0.0,
                               "r_medio": -0.71, "r_shr": -0.4, "pnl": -50.0, "estado": "observacion"}]},
            "sombra_retro_k3": [{"fecha": "2026-09-28", "pnl_real": -6.5, "pnl_sombra": -6.22, "delta": 0.28,
                                 "bloqueados": 2, "ganadores_bloqueados": 1}]}))
    if ajustes:
        (d / "ajustes.json").write_text(json.dumps({"modo": "sombra", "ajustes": [
            {"knob": "K3_max_posiciones", "valor": 3, "motivo": "racha: 3 trades perdedores seguidos",
             "desde": "2026-09-18", "vence": "2026-09-21"}]}))
    if regimen:
        (d / "regimen.json").write_text(json.dumps({
            "nivel": "CAUTELA", "calculado_en": "2026-09-18T14:58:00+00:00", "desde": "2026-09-18T14:00:00+00:00",
            "senales": [{"nombre": "spy_bajo_sma20", "activa": True, "detalle": "cierre previo 660 vs SMA20 665"},
                        {"nombre": "vixy_sube", "activa": None, "detalle": "sin datos de VIXY"}],
            "racha": {"disparada": False, "motivos": [], "perdedores_seguidos": 1},
            "motivos": ["cierre previo 660 vs SMA20 665"], "acciones_sombra": {"max_posiciones": 3, "sin_small_caps": True}}))
    if sombra:
        (d / "sombra_diaria.jsonl").write_text(json.dumps({"fecha": "2026-09-18", "pnl_real": -10.0, "pnl_sombra": -4.0,
                                                           "delta": 6.0, "bloqueados": 1, "ganadores_bloqueados": 0}) + "\n")
    return d


def test_seccion_control_con_los_tres_bloques(tmp_path):
    d = _aprendizaje(tmp_path)
    eventos(tmp_path,
            {"ts": "2026-09-18T14:50:00+00:00", "tipo": "stop_diario", "fecha_sesion": "2026-09-18", "modo": "enforce",
             "pct": 1.0, "pnl": -30.0, "pnl_pct": -0.6, "umbral_usd": 48.65, "activo": False, "bloquea": False},
            {"ts": "2026-09-18T14:55:00+00:00", "tipo": "gate_sombra", "ticker": "ABC",
             "bloquearia": [{"knob": "K5_sin_small_caps", "motivo": "x"}]})
    ctx = bd.construir(AHORA, cfg(tmp_path, aprendizaje=d), get=sin_alpaca)
    html = bd.render(ctx)
    assert "Control de riesgo y aprendizaje" in html
    assert "Pérdida del día vs umbral" in html and "−$30.00 de −$48.65 · inactivo" in html
    assert "CAUTELA" in html and "spy_bajo_sma20" in html and "small caps: bloqueadas" in html
    assert "K3_max_posiciones" in html and "racha: 3 trades perdedores seguidos" in html
    assert "Muestra insuficiente" in html and "retro K3" in html and "en vivo" in html
    assert "1 habrían bloqueado" in html
    assert "&lt;b&gt;orb&lt;/b&gt;" in html and "<b>orb</b>" not in html  # escape


def test_seccion_control_sin_archivos_dice_sin_dato_nunca_cero(tmp_path):
    vacio = tmp_path / "vacio"
    vacio.mkdir()
    ctx = bd.construir(AHORA, cfg(tmp_path, aprendizaje=vacio), get=sin_alpaca)
    html = bd.render(ctx)
    seccion = html[html.index("Control de riesgo y aprendizaje"):]
    assert "SIN DATO" in seccion and "Sin reporte nocturno todavía" in seccion
    assert "Pérdida del día vs umbral" in seccion and "sin dato" in seccion
    assert "INACTIVO" not in seccion and "· inactivo" not in seccion
    assert "Ajustes propuestos: ninguno" in seccion


def test_seccion_control_sin_carpeta_configurada(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    assert "sin carpeta de aprendizaje configurada" in bd.render(ctx)


def test_seccion_control_json_corrupto_avisa_y_el_panel_sigue(tmp_path):
    d = _aprendizaje(tmp_path, reporte=False)
    (d / "ajustes.json").write_text("{roto")
    (d / "sombra_diaria.jsonl").write_text("no-json\n")
    html = bd.render(bd.construir(AHORA, cfg(tmp_path, aprendizaje=d), get=sin_alpaca))
    assert "ajustes.json ilegible" in html and "sombra_diaria.jsonl con líneas ilegibles" in html
    assert "Watchlist actual" in html and "Límites de riesgo" in html


def test_seccion_control_bloque_que_revienta_no_tumba(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "_html_bloque_regimen", lambda ap, ctx: 1 / 0)
    html = bd.render(bd.construir(AHORA, cfg(tmp_path, aprendizaje=_aprendizaje(tmp_path)), get=sin_alpaca))
    assert "bloque no disponible (ZeroDivisionError)" in html and "Qué habría hecho la sombra" in html


def test_barra_stop_diario_0_60_100(tmp_path):
    for pnl, clase in ((10.0, ""), (-29.2, ""), (-48.65, "lleno")):
        ctx = {"stop_diario": {"sin_datos": False, "pnl": pnl, "umbral_usd": 48.65, "pct": 1.0, "activo": pnl <= -48.65,
                               "bloquea": pnl <= -48.65, "desde": "10:00"}}
        h = bd._html_bloque_stop(ctx)
        frac = max(0.0, -pnl) / 48.65
        assert f'style="width:{min(1.0, frac) * 100:.0f}%"' in h
        if clase:
            assert 'class="lleno"' in h


def test_seccion_control_no_usa_post_ni_cliente_de_ordenes():
    import inspect
    src = "".join(inspect.getsource(f) for f in (bd.leer_aprendizaje, bd._html_control_aprendizaje,
                                                   bd._html_bloque_stop, bd._html_bloque_regimen,
                                                   bd._html_bloque_aprendizaje))
    assert "post(" not in src.lower() and "alpaca_client" not in src and "colocar" not in src


# ───────────── Panel compacto + Trades de días anteriores (2026-10-01) ─────────────

def _seccion(html, titulo, siguiente=None):
    i = html.index(f'aria-label="{titulo}"')
    j = html.index("</section>", i)
    return html[i:j]


def test_control_compacto_tarjetas_y_plegables(tmp_path):
    d = _aprendizaje(tmp_path)
    eventos(tmp_path,
            {"ts": "2026-09-18T14:50:00+00:00", "tipo": "stop_diario", "fecha_sesion": "2026-09-18", "modo": "enforce",
             "pct": 1.0, "pnl": -30.0, "pnl_pct": -0.6, "umbral_usd": 48.65, "activo": False, "bloquea": False},
            {"ts": "2026-09-18T14:55:00+00:00", "tipo": "gate_sombra", "ticker": "ABC",
             "bloquearia": [{"knob": "K5_sin_small_caps", "motivo": "x"}]})
    html = bd.render(bd.construir(AHORA, cfg(tmp_path, aprendizaje=d), get=sin_alpaca))
    sec = _seccion(html, "Control de riesgo y aprendizaje")
    assert sec.count('class="ctl-card') == 4
    resumen = sec[sec.index("ctl-resumen"):sec.index("<details")]
    assert "−$30.00 de −$48.65" in resumen and "inactivo" in resumen
    assert "CAUTELA" in resumen and "1 de 2 señales activas" in resumen
    assert "no disparada" in resumen and "perdedores seguidos 1" in resumen
    assert 'title="K3_max_posiciones">Tope de posiciones (en prueba)' in resumen and "1 de 1 habrían frenado" in resumen
    for id_ in ("ctl-stop", "ctl-regimen", "ctl-segmentos", "ctl-ajustes", "ctl-sombra"):
        assert f'<details class="ctl" id="{id_}">' in sec  # plegado por defecto
    assert "tabla-ctl" in sec and "fila c3" not in sec
    assert "det-abiertos" in html  # recuerda los plegables abiertos entre recargas


def test_control_compacto_sin_archivos_tarjetas_sin_dato(tmp_path):
    vacio = tmp_path / "vacio"
    vacio.mkdir()
    html = bd.render(bd.construir(AHORA, cfg(tmp_path, aprendizaje=vacio), get=sin_alpaca))
    sec = _seccion(html, "Control de riesgo y aprendizaje")
    resumen = sec[sec.index("ctl-resumen"):sec.index("<details")]
    assert resumen.count("<b>sin dato</b>") == 3 and "<b>SIN DATO</b>" in resumen
    assert "<b>0</b>" not in resumen


def test_css_tablas_con_scroll_interno_y_responsive():
    assert ".tabla-ctl{max-width:100%;max-height:340px;overflow:auto" in bd.CSS
    assert "@media (max-width:480px){.ctl-resumen{grid-template-columns:1fr}" in bd.CSS
    assert ".tabla-ctl td.txt{white-space:normal;min-width:220px" in bd.CSS


def _memoria(d, *trades, extra=""):
    d.mkdir(exist_ok=True)
    (d / "memoria_trades.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trades) + extra)
    return d


def _t(oid, ticker, ent, sal, p_in=10.0, p_out=11.0, q=5.0, pnl=5.0, r=1.0, motivo="objetivo"):
    return {"order_id": oid, "ticker": ticker, "entrada_ts": ent, "salida_ts": sal, "precio_fill": p_in,
            "precio_salida": p_out, "cantidad": q, "pnl": pnl, "r": r, "motivo_salida": motivo, "fecha": ent[:10]}


def test_historial_por_dia_reciente_primero_y_hora_monterrey(tmp_path):
    d = _memoria(tmp_path / "apr",
                 _t("a", "AAA", "2026-09-16T14:27:58+00:00", "2026-09-16T14:54:03+00:00", pnl=-16.04, r=-1.05, motivo="stop"),
                 _t("b", "BBB", "2026-09-17T15:00:00+00:00", "2026-09-17T19:55:00+00:00", motivo="cierre"),
                 _t("c", "CCC", "2026-09-17T14:00:00+00:00", "2026-09-17T14:30:00+00:00", p_out=None, pnl=None, r=None,
                    motivo=None),
                 _t("b", "BBB", "2026-09-17T15:00:00+00:00", "2026-09-17T19:55:00+00:00"),  # duplicado
                 extra="{roto\n")
    html = bd.render(bd.construir(AHORA, cfg(tmp_path, aprendizaje=d), get=sin_alpaca))
    sec = _seccion(html, "Trades de días anteriores")
    assert sec.index("2026-09-17") < sec.index("2026-09-16")
    assert '<details class="ctl dia" id="hist-2026-09-17" open>' in sec
    assert '<details class="ctl dia" id="hist-2026-09-16">' in sec
    assert "2 trades · 1 G / 0 P" in sec and "+$5.00 (parcial: 1 sin dato)" in sec
    assert "1 trade · 0 G / 1 P" in sec and "−$16.04" in sec
    dia16 = sec[sec.index("hist-2026-09-16"):]
    assert "<td>08:27</td><td>08:54</td>" in dia16  # 14:27 UTC = 08:27 Monterrey (UTC−6)
    assert "−1.05" in dia16 and "<td>stop</td>" in dia16
    dia17 = sec[sec.index("hist-2026-09-17"):sec.index("hist-2026-09-16")]
    assert dia17.index("CCC") < dia17.index("BBB")  # orden por hora de entrada
    assert dia17.count("BBB") == 1
    assert "cierre EOD" in dia17 and dia17.count("sin dato") >= 4
    assert "1 línea(s) ilegibles" in sec


def test_historial_limite_de_sesiones(tmp_path):
    trades = [_t(str(i), "X", f"2026-08-{i:02d}T15:00:00+00:00", f"2026-08-{i:02d}T16:00:00+00:00") for i in range(1, 26)]
    h = bd.leer_historial_trades(_memoria(tmp_path / "apr", *trades))
    assert len(h["dias"]) == bd.HISTORIAL_MAX_SESIONES and h["ocultas"] == 5
    assert h["dias"][0]["fecha"] == "2026-08-25"


def test_historial_sin_memoria_no_inventa_ceros(tmp_path):
    vacio = tmp_path / "vacio"
    vacio.mkdir()
    html = bd.render(bd.construir(AHORA, cfg(tmp_path, aprendizaje=vacio), get=sin_alpaca))
    sec = _seccion(html, "Trades de días anteriores")
    assert "Sin memoria de trades todavía" in sec and "0 trades" not in sec and "0.00" not in sec
    html2 = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    assert "sin carpeta de aprendizaje configurada" in _seccion(html2, "Trades de días anteriores")


def test_historial_que_revienta_no_tumba_el_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "_html_historial_cuerpo", lambda ctx: 1 / 0)
    html = bd.render(bd.construir(AHORA, cfg(tmp_path, aprendizaje=_memoria(tmp_path / "apr")), get=sin_alpaca))
    assert "historial no disponible (ZeroDivisionError)" in html and "Control de riesgo y aprendizaje" in html


def test_historial_y_resumen_no_usan_post_ni_ordenes():
    import inspect
    src = "".join(inspect.getsource(f) for f in (bd.leer_historial_trades, bd._html_historial_cuerpo,
                                                   bd._html_resumen_control, bd._html_bloque_ajustes,
                                                   bd._html_bloque_sombra))
    assert "post(" not in src.lower() and "alpaca_client" not in src and "colocar" not in src


# ───────────── Visual 1+6+3 (2026-10-01): orden, índice, sin huecos ─────────────

def test_orden_de_secciones_lo_importante_arriba(tmp_path):
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    orden = ['id="resumen"', 'id="riesgo"', 'id="equity"', 'id="velas"', 'id="watchlist"',
             'id="ejecucion"', 'id="historial"']
    pos = [html.index(o) for o in orden]
    assert pos == sorted(pos)
    assert html.index('class="anclas"') < html.index('id="resumen"')


def test_indice_de_anclas_apunta_a_ids_existentes(tmp_path):
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    for id_, _t in bd.ANCLAS:
        assert f'href="#{id_}"' in html
        if id_ != "posiciones":  # la sección del bróker existe solo con datos del bróker
            assert f'id="{id_}"' in html
    assert 'class="anclas-movil"' in html and "@media (max-width:640px){.anclas{display:none}" in bd.CSS


def test_sistema_franja_ok_plegada_y_abierta_si_hay_que_revisar():
    etapa = lambda n, e: {"donde": "VPS", "estado": e, "nombre": n, "rol": "r", "detalle": "d"}
    ok = bd._html_sistema({"etapas": [etapa("Hunter", "ok"), etapa("Ejecutor", "ok")]}, "X")
    assert "<span class=\"punto ok\">OK</span>" in ok and "2/2 etapas OK" in ok and " open" not in ok
    mal = bd._html_sistema({"etapas": [etapa("Hunter", "ok"), etapa("Ejecutor", "sin-datos")]}, "X")
    assert "Revisar: Ejecutor" in mal and 'data-forzar="1" open' in mal and "1/2 etapas OK" in mal
    vacio = bd._html_sistema({"etapas": []}, "")
    assert "sin dato" in vacio and "0/0" not in vacio


def test_filas_sin_huecos_y_explicaciones_plegadas(tmp_path):
    assert ".fila.c2,.fila.c3{align-items:start}" in bd.CSS
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    assert '<details class="explica-mas" id="lat-explica">' in html
    assert "!d.dataset.forzar" in html  # lo que pide revisión no se cierra por la preferencia guardada


# ───────────── Visual 2+5+4 (2026-10-01): watchlist, jerga, números ─────────────

def test_codigos_legibles_con_tooltip_y_maximo_posiciones_intacto():
    assert bd._codigo_legible("MAXIMO_POSICIONES") == "MAXIMO_POSICIONES"
    assert bd._codigo_legible("DATO_FALTANTE:ultimos_niveles_ts") == \
        '<span title="DATO_FALTANTE:ultimos_niveles_ts">Falta dato: niveles recientes</span>'
    assert bd._codigo_legible("MERCADO_CERRADO") == '<span title="MERCADO_CERRADO">Mercado cerrado</span>'
    assert bd._codigo_legible("<raro>") == "&lt;raro&gt;"  # desconocido: tal cual, escapado
    assert bd._knob_legible("K3_max_posiciones") == '<span title="K3_max_posiciones">Tope de posiciones (en prueba)</span>'


def test_formato_unico_de_dinero_y_r():
    assert bd._fmt_usd(-16.04) == "−$16.04" and bd._fmt_usd(5) == "+$5.00" and bd._fmt_usd(None) == "sin dato"
    assert bd._fmt_r(-1.0451) == "−1.05" and bd._fmt_r(None) == "sin dato"
    assert "table.n5 td:nth-child(5)" in bd.CSS and "font-variant-numeric:tabular-nums" in bd.CSS


def test_limites_no_repite_el_stop_inactivo_pero_si_el_activo():
    base = {"limites": {}, "bloqueos": None}
    inactivo = bd._html_riesgo({**base, "stop_diario": {"sin_datos": False, "activo": False, "pnl": -5.0,
                                                         "umbral_usd": 48.65, "pct": 1.0}})
    assert 'ver <a href="#riesgo">Riesgo ↑</a>' in inactivo and "P&amp;L hoy" not in inactivo
    activo = bd._html_riesgo({**base, "stop_diario": {"sin_datos": False, "activo": True, "bloquea": True,
                                                       "pnl": -50.0, "umbral_usd": 48.65, "pct": 1.0, "desde": "10:00"}})
    assert "ACTIVO desde 10:00" in activo


def test_watchlist_titular_truncado_y_estado_como_punto_en_movil(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [_entrada("DIS", "triggered")]}))
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "5000"}), velas=velas_ok))
    assert "<td class='cat' tabindex='0' title=" in html and "<span class='dot est-triggered'" in html
    assert "text-overflow:ellipsis" in bd.CSS and ".tabla-watch .col-estado{display:none}" in bd.CSS


def test_sin_jerga_en_titulos():
    import inspect
    src = inspect.getsource(bd.render)
    assert "fail-closed</span>" not in src and "LLM" not in src and "watchlist.json →" not in src
    assert "knobs en sombra" not in src


# ───────────── Visual 8+9+7 (2026-10-01): encabezado, vacíos, velas ─────────────

def test_etiqueta_de_zona_corta_con_desfase():
    from zoneinfo import ZoneInfo
    assert bd.etiqueta_zona(ZoneInfo("America/Monterrey"), AHORA) == "MTY (UTC−6)"
    assert bd.etiqueta_zona(ZoneInfo("UTC"), AHORA) == "UTC"
    assert bd.etiqueta_zona(ZoneInfo("America/New_York"), AHORA) == "ET (UTC−4)"


def test_encabezado_compacto_con_mas(tmp_path):
    html = bd.render(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))
    cab = html[html.index("<header>"):html.index("</header>")]
    visible, mas = cab.split('<details class="mas">', 1)
    assert "PAPER · ALPACA" in visible and "Act. " in visible
    assert "Noticias leídas" in mas and "Solo lectura" in mas and 'id="tema-toggle"' in mas
    assert "a.pildora{color:inherit;text-decoration:none}" in bd.CSS


def test_dato_ausente_dice_sin_dato_no_guion():
    assert bd.fmt_dinero(None) == "sin dato" and bd.fmt_num(None) == "sin dato"
    assert bd.fmt_dinero(0) == "$0.00"  # un cero real sigue siendo cero


def test_aviso_de_feed_va_arriba_y_no_dentro_de_velas():
    ctx = {"avisos_feed": ["Alpaca rechazó el feed (401)"], "fuente_datos": "Yahoo"}
    banda = bd._html_banda_datos(ctx)
    assert 'class="nota banda-datos"' in banda and "Datos de mercado: Yahoo." in banda
    assert bd._html_banda_datos({"avisos_feed": []}) == ""


def test_velas_plegables_abierta_la_mas_reciente_y_escala_con_aire_bajo_5():
    import inspect
    src = inspect.getsource(bd.render)
    assert "abierto = \" open\" if op is reciente" in src
    g = inspect.getsource(bd._grafico_velas)
    assert "minimo < 5 and rango < minimo * 0.02" in g and 'text-anchor="start"' in g


def test_stream_orden_sin_fill_es_no_aplica_y_no_sin_dato():
    assert bd._precio_stream({"estado": "cancelada", "precio": None}) == "—"
    assert bd._precio_stream({"estado": "ejecutada", "precio": None}) == "sin dato"
    assert bd._precio_stream({"estado": "ejecutada", "precio": 81.6}) == "$81.60"
    assert "details.vela-op[open]{grid-column:1/-1}" in bd.CSS
