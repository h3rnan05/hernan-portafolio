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
        "presupuesto_velas": 8.0,
        "hunter_max_min": 45.0,
        "rechequeo_max_min": 12.0,
        "tz": ZoneInfo("UTC"),
    }
    base.update(extra)
    return base


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
    assert _hunter(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))["estado"] == "ok"


def test_hunter_usa_el_ultimo_commit_si_el_json_no_trae_hora(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "fecha_ultimo_commit",
                        lambda ruta: datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    (tmp_path / "watchlist.json").write_text(json.dumps({"entradas": [{"ticker": "AAA"}]}))
    _, generado, _ = bd.leer_watchlist(tmp_path / "watchlist.json")
    assert generado == datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    # 3 h de antigüedad en plena sesión: "Revisar", no "OK".
    assert _hunter(bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca))["estado"] == "alerta"


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
    assert riesgo["estado"] == "ok" and riesgo["detalle"] == "0 bloqueos hoy"
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


def test_rechequeo_viejo_no_oculta_un_bloqueo_real(tmp_path):
    # Un bloqueo registrado es un hecho: sigue en alerta y con su conteo.
    eventos(tmp_path,
            {"ts": "2026-09-18T14:00:00Z", "tipo": "rechequeo"},
            {"ts": "2026-09-18T14:01:00Z", "tipo": "bloqueo_riesgo", "ticker": "AAA",
             "limite": "maximo_posiciones"})
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    riesgo = _etapas(ctx)["Riesgo"]
    assert riesgo["estado"] == "alerta" and riesgo["detalle"] == "1 bloqueos hoy"


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
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    hunter = next(e for e in ctx["etapas"] if e["nombre"] == "Hunter")
    assert hunter["detalle"] == "watchlist del jue 22:33"
    html = bd.render(ctx)
    assert "generada jue 22:33" in html
    assert "mié 14:00" in html   # columna Detectado de la tabla


def test_hunter_de_hoy_sigue_sin_dia(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({
        "generado": "2026-09-18T14:40:00+00:00", "entradas": []}))
    ctx = bd.construir(AHORA, cfg(tmp_path), get=sin_alpaca)
    hunter = next(e for e in ctx["etapas"] if e["nombre"] == "Hunter")
    assert hunter["detalle"] == "watchlist de las 14:40"


# ───────────────────────── curva de equity ─────────────────────────

HIST = bd.RUTA_HISTORIAL


def _historial(equity, timestamps=None, base=None):
    inicio = int(datetime(2026, 9, 18, 15, 40, tzinfo=timezone.utc).timestamp())
    ts = timestamps or [inicio + 300 * i for i in range(len(equity))]  # cada 5 min
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
    assert puntos[0][0] == datetime(2026, 9, 18, 15, 40, tzinfo=timezone.utc)
    assert ctx["equity_dia"]["base"] == 5000
    svg = _svgs_equity(bd.render(ctx))[0]
    assert svg.count("<polyline") == 1
    coords = svg.split('points="')[1].split('"')[0].split(" ")
    assert len(coords) == 4
    assert "inicial $5,000.00" in svg and 'stroke-dasharray="5 4"' in svg
    assert "Sin datos" not in svg
    assert "15:40" in svg and "15:55" in svg   # eje X en la zona del panel (UTC)


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
