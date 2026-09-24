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
    assert et["Riesgo"]["detalle"] == "— bloqueos hoy"


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
    # Un bloqueo registrado es un hecho: sigue en alerta y con su conteo.
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:01:00Z", "tipo": "bloqueo_riesgo", "ticker": "AAA",
             "limite": "maximo_posiciones"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    # (2026-09-23) Un límite conocido bloqueando es el sistema funcionando:
    # se muestra con su conteo, pero ya no pide "Revisar" por sí solo.
    assert riesgo["estado"] == "ok" and riesgo["detalle"] == "1 bloqueos únicos (1 eventos) hoy"


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
    assert hunter["detalle"] == "escaneo VPS de las 14:40 · slot 3/8 · 12 evaluadas · watchlist de las 14:40"


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
    assert hunter["detalle"] == ("escaneo VPS de las 14:52 · slot 3/8 · 0 evaluadas · watchlist del jue 22:33"
                                 " · GitHub #218 de las 14:46")
    assert ctx["hunter_momento"] == datetime(2026, 9, 18, 14, 52, tzinfo=timezone.utc)


def test_detalle_del_hunter_muestra_dia_y_hora_en_dash_tz(tmp_path):
    # Caso real: watchlist del vie 18 sep 22:33 UTC, escaneo del lun 21 sep
    # 13:46 UTC, panel en Monterrey (UTC-6) el lunes a las 07:55.
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-18T22:33:00+00:00", "entradas": []}))
    lunes = datetime(2026, 9, 21, 13, 55, tzinfo=timezone.utc)
    escaneo_vps(tmp_path, datetime(2026, 9, 21, 13, 46, 8, tzinfo=timezone.utc))
    ctx = bd.construir(lunes, cfg(tmp_path, tz=ZoneInfo("America/Monterrey")), get=sin_alpaca, gha=gha_caido())
    assert _hunter(ctx)["detalle"] == "escaneo VPS de las 07:46 · slot 3/8 · 12 evaluadas · watchlist del vie 16:33"
    assert "generada vie 16:33" in bd.render(ctx)


def test_sin_escaneo_del_vps_es_sin_datos_aunque_github_y_la_watchlist_sean_frescos(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-18T14:55:00+00:00", "entradas": []}))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca,
                       gha=gha_ok(datetime(2026, 9, 18, 14, 56, tzinfo=timezone.utc)))
    assert _hunter(ctx)["estado"] == "sin-datos" and ctx["hunter_momento"] is None
    assert _hunter(ctx)["detalle"] == "sin escaneo del VPS hoy · watchlist de las 14:55 · GitHub #218 de las 14:56"
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
    assert "14:00" in svg and "14:15" in svg   # eje X en la zona del panel (UTC)
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
    assert "<span class=\"mono\">Inicial</span><b>—</b>" in html


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
    m = re.search(rf'<h2>{ticker}</h2>.*?(<svg viewBox="0 0 480 230" role="img" aria-label="Velas[^"]*">.*?</svg>)', html, re.S)
    return m.group(1) if m else None


def test_sin_posiciones_ni_ordenes_lo_dice(tmp_path):
    ctx = bd.construir(AHORA, cfg(tmp_path), get=alpaca_falso({"equity": "5000"}), velas=velas_ok)
    assert ctx["operaciones"] == []
    assert "Sin posiciones abiertas ni órdenes hoy." in bd.render(ctx)


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
    assert 'class="vela"' not in svg and "marca-" not in svg
    # Las marcas siguen en el pie, porque salen de Alpaca y la watchlist, no de Yahoo.
    assert "$5.05" in html and "$5.12" in html and "$4.90" in html


def test_velas_reales_se_grafican_con_las_tres_marcas_y_la_hora_del_fill(tmp_path):
    _watchlist_con_ruptura(tmp_path)
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": [_compra()]})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)
    m = ctx["operaciones"][0]["marcas"]
    assert m == {"ruptura": 5.05, "entrada_precio": 5.12,
                 "entrada_hora": datetime(2026, 9, 18, 14, 32, 10, tzinfo=timezone.utc), "stop": 4.90}
    html = bd.render(ctx)
    svg = _svg_velas(html, "AAA")
    assert svg.count('class="vela"') == 5
    for marca in ("marca-ruptura", "marca-stop", "marca-entrada", "marca-entrada-hora"):
        assert marca in svg, marca
    assert "ruptura $5.05" in svg and "stop $4.90" in svg and "entrada $5.12" in svg
    assert "5 velas · Yahoo 15:00" in html
    assert "$5.12 a las 14:32" in html


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
    assert html.count("<b>sin dato</b>") == 2 and "$5.10 (hora sin dato)" in html
    assert 'class="vela"' in svg


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
        if ruta == "/v2/orders" and params and params.get("status") == "open":
            return [abierta], None
        return get(ruta, params)

    ctx = bd.construir(AHORA, cfg(tmp_path), get=get_con_abiertas, velas=velas_ok)
    assert ctx["operaciones"][0]["marcas"]["stop"] == 4.80
    assert any(p.get("nested") == "true" and p.get("status") == "all" for r, p in llamadas if r == "/v2/orders")


def test_marca_fuera_de_rango_se_anota_en_el_borde_sin_aplastar_las_velas(tmp_path):
    _watchlist_con_ruptura(tmp_path, ruptura=50.0)   # lejísimos de velas de $5
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion()], "/v2/orders": []})
    svg = _svg_velas(bd.render(bd.construir(AHORA, cfg(tmp_path), get=get, velas=velas_ok)), "AAA")
    assert "marca-ruptura-fuera" in svg and "fuera del gráfico" in svg
    assert '<line class="marca-ruptura"' not in svg


def test_tope_de_tickers_por_corrida(tmp_path):
    get = alpaca_falso({"equity": "5000"}, **{"/v2/positions": [_posicion("AAA"), _posicion("BBB")], "/v2/orders": []})
    pedidos = []

    def velas_contando(ticker):
        pedidos.append(ticker)
        return velas_ok(ticker)

    ctx = bd.construir(AHORA, cfg(tmp_path, velas_max_tickers=1), get=get, velas=velas_contando)
    assert pedidos == ["AAA"] and ctx["operaciones_omitidas"] == ["BBB"]
    assert "Sin graficar por el tope" in bd.render(ctx)


def test_tickers_en_operacion_posiciones_primero_sin_duplicados():
    assert bd.tickers_en_operacion([_posicion("BBB")], [_compra("AAA"), _compra("BBB")]) == ["BBB", "AAA"]


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
    assert "Yahoo limitó peticiones (429)" in html
    assert 'class="vela"' in html   # la copia vieja sí se dibuja


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
    eventos(tmp_path,
            {"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"},
            _bloqueo("2026-09-18T14:50:00Z", "AAA", "DATO_FALTANTE:niveles", "niveles_ausentes"),
            _bloqueo("2026-09-18T14:51:00Z", "BBB", "TICKER_COMPROMETIDO", "ticker_comprometido"))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "alerta"
    assert "revisar: DATO_FALTANTE:niveles" in riesgo["detalle"]
    assert ctx["riesgo"]["dato_faltante"] == ["DATO_FALTANTE:niveles"]
    assert "<b>revisar:</b> DATO_FALTANTE:niveles" in bd.render(ctx)


def test_codigo_nuevo_pide_revisar_y_el_legado_se_mapea(tmp_path):
    # Un evento viejo solo con `limite` conocido se mapea al catálogo (no es
    # nuevo); un código que el catálogo no conoce sí pide revisar.
    eventos(tmp_path,
            {"ts": "2026-09-18T14:55:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:50:00Z", "tipo": "bloqueo_riesgo", "ticker": "AAA", "limite": "maximo_posiciones"},
            _bloqueo("2026-09-18T14:51:00Z", "BBB", "LIMITE_INVENTADO", "inventado"))
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
    assert riesgo["detalle"] == "0 bloqueos únicos (0 eventos) hoy · capacidad llena: MAXIMO_POSICIONES desde 14:30 (25 corridas)"
    cap = ctx["riesgo"]["capacidad"]
    assert len(cap) == 1 and cap[0]["desde"] == "14:30" and cap[0]["hasta"] == "14:54" and cap[0]["corridas"] == 25
    html = bd.render(ctx)
    assert "Capacidad llena: <b>MAXIMO_POSICIONES</b>" in html and "25 corridas" in html


def test_github_actions_mas_de_45_min_sin_correr_en_sesion_es_alerta(tmp_path):
    # Escaneo del VPS fresco (Hunter OK por sí solo), pero GitHub lleva 60 min.
    escaneo_vps(tmp_path, datetime(2026, 9, 18, 14, 50, tzinfo=timezone.utc))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca,
                       gha=gha_ok(datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)))
    hunter = _hunter(ctx)
    assert hunter["estado"] == "alerta"
    assert "GitHub lleva 60 min sin correr (máx 45)" in hunter["detalle"]
    assert any("momentum_hunter.yml" in p and "60 min" in p for p in ctx["problemas"])
    # Con 30 min, OK; y sin dato de Actions no se inventa alerta.
    ctx_ok = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca,
                          gha=gha_ok(datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc)))
    assert _hunter(ctx_ok)["estado"] == "ok"
    assert _hunter(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca, gha=gha_caido()))["estado"] == "ok"
    # Fuera de sesión (sábado) no es alerta aunque lleve horas.
    sabado = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)
    ctx_s = bd.construir(sabado, cfg(tmp_path), get=sin_alpaca,
                         gha=gha_ok(datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)))
    assert _hunter(ctx_s)["estado"] != "alerta" or "GitHub lleva" not in _hunter(ctx_s)["detalle"]


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
    # Y los colores fijos que rompían el oscuro ya no se emiten en ningún SVG.
    assert 'fill="#16171a"' not in html and 'stroke="#bdb9ad"' not in html
    # El modo claro sigue igual: variable de fondo crema intacta.
    assert "--fondo:#f3f1ea" in html
