"""Tasa de captura: lista de movers reales, quién los vio, noticias como
etiqueta, dónde se cayó cada uno. Todo inyectado: sin red ni disco real."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from momentum_hunter.config import CONFIG
from momentum_hunter.models import Barras, Metadata
from shadow_alpaca import tasa_captura as tc

# Miércoles 30/9/2026, 16:25 Nueva York.
AHORA = datetime(2026, 9, 30, 20, 25, tzinfo=UTC)
HOY = "2026-09-30"
REPO = Path(__file__).resolve().parents[2]


def _epoch(d: datetime) -> int:
    return int(d.timestamp())


def _quote(ticker, precio=5.0, cambio=35.0, volumen=3_000_000, cuando=AHORA, **extra):
    q = {"symbol": ticker, "regularMarketPrice": precio, "regularMarketChangePercent": cambio,
         "regularMarketVolume": volumen, "regularMarketTime": _epoch(cuando), "shortName": f"{ticker} Inc"}
    q.update(extra)
    return q


def _barras(ticker, vol=400_000.0, close=4.0, dias=30, con_hoy=True, vol_hoy=9e9):
    """`dias` velas hábiles hasta ayer (29/9), más la de hoy si se pide."""
    fechas = []
    d = date(2026, 9, 29)
    while len(fechas) < dias:
        if d.weekday() < 5:
            fechas.append(d.isoformat())
        d -= timedelta(days=1)
    fechas.reverse()
    vols = [vol] * dias
    closes = [close] * dias
    if con_hoy:
        fechas.append(HOY)
        vols.append(vol_hoy)
        closes.append(close * 1.4)
    return Barras(ticker=ticker, fechas=fechas, open=closes, close=closes, high=closes, low=closes, volume=vols)


# ───────────────────────── screener y criterio ─────────────────────────

def test_fila_sin_un_dato_no_se_rellena_con_cero():
    desc = Counter()
    filas = tc.parsear_screener({"quotes": [
        _quote("AAA"),
        {**_quote("BBB"), "regularMarketVolume": None},
        {**_quote("CCC"), "regularMarketTime": None},
        "basura",
    ]}, desc)
    assert [f.ticker for f in filas] == ["AAA"]
    assert desc == Counter({"cotizacion_incompleta": 1, "sin_hora_cotizacion": 1, "fila_ilegible": 1})


def test_respuesta_sin_quotes_es_un_error_no_cero_movers():
    with pytest.raises(ValueError):
        tc.parsear_screener({}, Counter())


def test_screener_lleno_avisa_posible_truncado():
    quotes = [_quote(f"T{i}") for i in range(tc.TOP_SCREENER)]
    filas, truncado = tc.consultar_screener(screen=lambda *a, **k: {"quotes": quotes})
    assert truncado and len(filas) == tc.TOP_SCREENER


def test_promedio_20d_excluye_la_vela_de_hoy():
    b = _barras("AAA", vol=100_000, vol_hoy=50_000_000)
    assert tc.promedio_20d(tc.barras_hasta_ayer(b, HOY)) == 100_000


def test_promedio_20d_con_menos_de_20_dias_o_un_hueco_es_none():
    assert tc.promedio_20d(tc.barras_hasta_ayer(_barras("A", dias=19), HOY)) is None
    b = _barras("A")
    b.volume[-3] = None
    assert tc.promedio_20d(tc.barras_hasta_ayer(b, HOY)) is None


def test_clasificar_movers_aplica_rvol_y_separa_los_no_verificables():
    filas = tc.parsear_screener({"quotes": [
        _quote("REAL", volumen=2_000_000),          # 2M / 400k = 5x justo
        _quote("FLOJO", volumen=1_000_000),         # 2,5x
        _quote("NUEVO", volumen=9_000_000),         # sin 20 días de historia
        _quote("VIEJO", cuando=AHORA - timedelta(days=1)),  # cotización de ayer
    ]}, Counter())
    diarias = {"REAL": _barras("REAL"), "FLOJO": _barras("FLOJO"), "NUEVO": _barras("NUEVO", dias=5),
               "VIEJO": _barras("VIEJO")}
    desc = Counter()
    reales, dudosos = tc.clasificar_movers(filas, diarias, HOY, desc)
    assert [m.ticker for m in reales] == ["REAL"]
    assert reales[0].rvol == pytest.approx(5.0)
    assert dudosos == [{"ticker": "NUEVO", "cambio_pct": 35.0, "motivo": "sin_promedio_20d"}]
    assert desc["volumen_relativo_bajo"] == 1 and desc["cotizacion_de_otro_dia"] == 1


def test_clasificar_movers_revisa_el_criterio_aunque_el_screener_ya_filtre():
    filas = tc.parsear_screener({"quotes": [_quote("CARA", precio=25.0), _quote("POCO", cambio=19.9)]}, Counter())
    desc = Counter()
    reales, _ = tc.clasificar_movers(filas, {"CARA": _barras("CARA"), "POCO": _barras("POCO")}, HOY, desc)
    assert reales == [] and desc["fuera_de_criterio"] == 2


# ───────────────────────── noticias ─────────────────────────

def test_ventana_de_noticias_arranca_el_dia_habil_anterior():
    assert tc.desde_noticias(date(2026, 9, 30)) == date(2026, 9, 29)
    assert tc.desde_noticias(date(2026, 9, 28)) == date(2026, 9, 25)   # lunes → viernes


def test_resultado_noticia_distingue_si_no_sin_fecha_y_error():
    T = SimpleNamespace
    desde, hoy = date(2026, 9, 29), date(2026, 9, 30)
    assert tc.resultado_noticia([T(fecha="2026-09-30"), T(fecha="2026-09-29T23:00:00+00:00")],
                                True, None, desde, hoy) == ("si", 2)
    assert tc.resultado_noticia([T(fecha="2026-07-30")], True, None, desde, hoy) == ("no", 0)
    assert tc.resultado_noticia([], True, None, desde, hoy) == ("no", 0)
    assert tc.resultado_noticia([T(fecha=None)], True, None, desde, hoy) == ("sin_fecha", 0)
    assert tc.resultado_noticia(None, False, "pausa_429", desde, hoy) == ("error:pausa_429", None)


def test_fecha_con_hora_se_lleva_a_nueva_york():
    # 02:00 UTC del 30 son las 22:00 del 29 en Nueva York.
    assert tc.fecha_ny("2026-09-30T02:00:00+00:00") == date(2026, 9, 29)
    assert tc.fecha_ny("basura") is None


# ───────────────────────── lo que dejó el bot ─────────────────────────

def test_watchlist_solo_cuenta_lo_que_entro_hoy():
    entradas = [
        SimpleNamespace(ticker="HOY", creado_en="2026-09-30T14:00:00+00:00", estado="TRIGGERED"),
        SimpleNamespace(ticker="AYER", creado_en="2026-09-29T14:00:00+00:00", estado="WATCHING"),
        # 23:30 UTC del 29 es el 29 en NY: no es de hoy.
        SimpleNamespace(ticker="BORDE", creado_en="2026-09-30T02:30:00+00:00", estado="WATCHING"),
    ]
    assert tc.detectados_watchlist(entradas, HOY) == {"HOY": "TRIGGERED"}


def _escribir_jsonl(path: Path, objs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(o) + "\n" for o in objs), encoding="utf-8")


def test_sombra_una_clase_en_cualquier_corrida_cuenta_y_a_gana_a_b(tmp_path):
    _escribir_jsonl(tmp_path / HOY / "vps" / "movers.jsonl", [
        {"candidatas": [{"ticker": "AAA", "clase": "B"}, {"ticker": "BBB", "motivo_rechazo": "rvol_bajo"}]},
        {"candidatas": [{"ticker": "AAA", "clase": "A"}, {"ticker": "BBB", "motivo_rechazo": "bajo_vwap"},
                        {"ticker": "CCC", "clase": "B"}]},
        {"candidatas": [{"ticker": "AAA", "motivo_rechazo": "bajo_vwap"}, {"ticker": "CCC", "motivo_rechazo": "x"}]},
    ])
    sombra, motivo = tc.resultado_sombra(tmp_path, HOY)
    assert motivo is None
    assert sombra == {"AAA": "A", "BBB": "bajo_vwap", "CCC": "B"}


def test_sombra_sin_archivo_es_sin_dato_no_cero(tmp_path):
    assert tc.resultado_sombra(tmp_path, HOY) == (None, "sin_movers_jsonl")


def test_trazas_de_auditoria_y_noticias_leidas(tmp_path):
    (tmp_path / f"{HOY}.json").write_text(json.dumps({"corridas": [
        {"candidatos": [{"ticker": "AAA", "decision": "descartada_por_evaluador",
                         "evaluacion": {"paso_detenido": "dinero_entrando"}}]},
        {"candidatos": [{"ticker": "BBB", "decision": "vetada_por_abogado_del_diablo", "evaluacion": {}}]},
    ]}))
    assert tc.trazas_auditoria(tmp_path, HOY) == {
        "AAA": "intradia:dinero_entrando", "BBB": "intradia:vetada_por_abogado_del_diablo"}
    assert tc.trazas_auditoria(tmp_path, "2026-01-01") == {}

    ruta = tmp_path / "noticias_leidas.json"
    ruta.write_text(json.dumps({"corridas": [
        {"corrida_ts": "2026-09-30T19:31:00+00:00",
         "acciones": [{"ticker": "CCC", "resultado": "sin_catalizador", "motivo": "fuera_ventana"}]},
        {"corrida_ts": "2026-09-30T19:01:00+00:00",
         "acciones": [{"ticker": "CCC", "resultado": "sin_noticias"},
                      {"ticker": "DDD", "resultado": "sin_noticias"}]},
        {"corrida_ts": "2026-09-29T19:01:00+00:00", "acciones": [{"ticker": "EEE", "resultado": "x"}]},
    ]}))
    # La más nueva (primera del archivo) manda; la de ayer no cuenta.
    assert tc.trazas_noticias_leidas(ruta, HOY) == {
        "CCC": "noticias:sin_catalizador:fuera_ventana", "DDD": "noticias:sin_noticias"}


def test_ranuras_escaneadas_se_reconstruyen_desde_el_slot(tmp_path):
    simbolos = [f"S{i}" for i in range(10)]
    _escribir_jsonl(tmp_path / HOY / "vps" / "events.jsonl", [
        {"modo": "escaneo", "slot": 0, "n_slots": 4},
        {"modo": "watchlist"},
        {"modo": "escaneo", "slot": 3, "n_slots": 4},   # última ventana: wrap
    ])
    vistos, motivo = tc.tickers_escaneados(tmp_path, HOY, simbolos, 3)
    assert motivo is None
    assert vistos == {"S0", "S1", "S2", "S9"}


def test_ranuras_no_se_adivinan_si_el_universo_no_coincide(tmp_path):
    _escribir_jsonl(tmp_path / HOY / "vps" / "events.jsonl", [{"modo": "escaneo", "slot": 0, "n_slots": 8}])
    assert tc.tickers_escaneados(tmp_path, HOY, ["A", "B"], 1) == (
        None, "universo_o_limite_distinto_al_del_escaneo")
    assert tc.tickers_escaneados(tmp_path, "2026-01-01", ["A"], 1) == (None, "sin_escaneos_hoy")
    assert tc.tickers_escaneados(tmp_path, HOY, None, 1) == (None, "universo_no_disponible")


def test_universo_reconstruido_usa_las_ramas_del_escaneo():
    meta = Metadata(ticker="AAA", market_cap=100_000_000.0)
    ok = tc.barras_hasta_ayer(_barras("AAA", vol=400_000, close=4.0), HOY)
    assert tc.motivo_universo_reconstruido(ok, meta, CONFIG) is None
    barata = tc.barras_hasta_ayer(_barras("AAA", close=0.5), HOY)
    assert tc.motivo_universo_reconstruido(barata, meta, CONFIG) == "universo:precio_bajo"
    poco_vol = tc.barras_hasta_ayer(_barras("AAA", vol=10_000), HOY)
    assert tc.motivo_universo_reconstruido(poco_vol, meta, CONFIG) == "universo:vol_bajo_small"
    grande = Metadata(ticker="AAA", market_cap=5e9)
    assert tc.motivo_universo_reconstruido(ok, grande, CONFIG) == "tamano_small_cap"
    assert tc.motivo_universo_reconstruido(ok, Metadata(ticker="AAA", es_etf=True), CONFIG) == "tipo_excluido"
    assert tc.motivo_universo_reconstruido(ok, None, CONFIG) == "metadata:sin_dato"
    assert tc.motivo_universo_reconstruido(None, meta, CONFIG) == "universo:sin_dato"


def test_motivo_de_caida_prefiere_la_traza_real():
    base = dict(auditoria={"A": "intradia:patron"}, noticias_leidas={"A": "noticias:x", "B": "noticias:y"},
                universo={"A", "B", "D", "E"}, escaneados={"A", "B", "E"}, reconstruido=None)
    assert tc.motivo_caida("A", **base) == "intradia:patron"
    assert tc.motivo_caida("B", **base) == "noticias:y"
    assert tc.motivo_caida("C", **base) == "fuera_del_universo"
    assert tc.motivo_caida("D", **base) == "ranura_no_escaneada"
    assert tc.motivo_caida("E", **base) == "sin_traza_tras_universo"
    assert tc.motivo_caida("E", **{**base, "reconstruido": "universo:precio_bajo"}) == "universo:precio_bajo"
    assert tc.motivo_caida("E", **{**base, "escaneados": None}) == "sin_dato"


# ───────────────────────── resumen ─────────────────────────

def _fila(ticker, wl="no", sombra="no", ny="no", na="no", motivo="x"):
    return tc.FilaCaptura(HOY, ticker, None, 5.0, 30.0, 1e6, 1e5, 10.0, wl, None, sombra, None,
                          ny, None, na, None, None if wl == "si" else motivo)


def test_resumen_cuenta_y_el_sin_dato_no_entra_al_porcentaje():
    filas = [_fila("A", wl="si", sombra="si", ny="si", na="si"), _fila("B", sombra="si", ny="error:pausa_429"),
             _fila("C", sombra="sin_dato", na="sin_fecha"), _fila("D")]
    r = tc.resumir(filas, HOY, no_verificables=[], descartes=Counter(), truncado=False)
    assert r["movers_reales"] == 4
    assert r["watchlist"] == {"detectados": 1, "con_dato": 4, "pct": 25.0}
    assert r["sombra"] == {"detectados": 2, "con_dato": 3, "pct": 66.7}
    assert r["ambos"] == 1 and r["ninguno"] == 1
    assert r["noticia_yahoo"] == {"si": 1, "no": 2, "sin_fecha": 0, "error": 1}
    assert r["noticia_alpaca"] == {"si": 1, "no": 2, "sin_fecha": 1, "error": 0}
    assert r["motivos_no_detectado"] == {"x": 3}


def test_formatear_sin_movers_y_con_error():
    vacio = tc.resumir([], HOY, no_verificables=[], descartes=Counter(), truncado=False)
    assert "ninguna acción cumplió" in tc.formatear(vacio, [])
    err = tc.resumir([], HOY, no_verificables=[], descartes=Counter(), truncado=False, error="screener:Timeout")
    texto = tc.formatear(err, [])
    assert "No se pudo armar" in texto and "screener:Timeout" in texto


# ───────────────────────── corrida completa ─────────────────────────

class _Provider:
    def __init__(self, diarias, metadata):
        self.diarias, self.meta = diarias, metadata

    def barras(self, tickers, dias=280):
        return {t: self.diarias[t] for t in tickers if t in self.diarias}

    def metadata(self, tickers):
        return {t: self.meta[t] for t in tickers if t in self.meta}


class _Yahoo:
    def __init__(self, por_ticker):
        self.por_ticker = por_ticker

    def titulares(self, t):
        return self.por_ticker.get(t, [])


class _Alpaca:
    def __init__(self, items):
        self.items, self.pedidos = items, []

    def paginas(self, ruta, params, tope):
        self.pedidos.append((ruta, params))
        return [{"news": self.items}], False


def _titular(fecha):
    return SimpleNamespace(texto="x", fuente="y", fecha=fecha)


def _escenario(tmp_path):
    quotes = [_quote("WL"), _quote("AUD"), _quote("FUERA"), _quote("SINSLOT"), _quote("CHICA"), _quote("BAJO", volumen=100)]
    diarias = {t: _barras(t) for t in ("WL", "AUD", "FUERA", "SINSLOT", "BAJO")}
    diarias["CHICA"] = _barras("CHICA", vol=10_000, close=4.0)
    tel = tmp_path / "tel"
    _escribir_jsonl(tel / HOY / "vps" / "movers.jsonl", [
        {"candidatas": [{"ticker": "WL", "clase": "A"}, {"ticker": "AUD", "motivo_rechazo": "bajo_vwap"}]}])
    # Universo de 4: WL, AUD, CHICA, SINSLOT. Límite 2 → 2 ranuras; hoy solo se escaneó la 0.
    _escribir_jsonl(tel / HOY / "vps" / "events.jsonl", [{"modo": "escaneo", "slot": 0, "n_slots": 2}])
    aud = tmp_path / "aud"
    aud.mkdir()
    (aud / f"{HOY}.json").write_text(json.dumps({"corridas": [{"candidatos": [
        {"ticker": "AUD", "decision": "descartada_por_evaluador", "evaluacion": {"paso_detenido": "patron"}}]}]}))
    return dict(
        ahora=AHORA,
        screen=lambda *a, **k: {"quotes": quotes},
        provider=_Provider(diarias, {"CHICA": Metadata(ticker="CHICA", market_cap=1e8)}),
        entradas_watchlist=[SimpleNamespace(ticker="WL", creado_en="2026-09-30T14:00:00+00:00", estado="TRIGGERED")],
        dir_telemetria=tel, dir_auditoria=aud, ruta_noticias_leidas=tmp_path / "no_existe.json",
        simbolos_universo=["WL", "CHICA", "AUD", "SINSLOT"], limite_escaneo=2,
        proveedor_yahoo=_Yahoo({"WL": [_titular("2026-09-30")], "AUD": [_titular("2026-06-01")]}),
        pausa_yahoo=SimpleNamespace(activa=lambda ahora: False),
        cliente_alpaca=_Alpaca([{"headline": "WL sube", "source": "benzinga",
                                 "created_at": "2026-09-30T11:00:00Z", "symbols": ["WL", "AUD"]}]),
    )


def test_corrida_completa_arma_filas_y_motivos(tmp_path):
    filas, r = tc.correr(**_escenario(tmp_path))
    por = {f.ticker: f for f in filas}
    assert set(por) == {"WL", "AUD", "FUERA", "SINSLOT", "CHICA"}     # BAJO no llega a 5x
    assert por["WL"].detectado_watchlist == "si" and por["WL"].estado_watchlist == "TRIGGERED"
    assert por["WL"].motivo_no_detectado is None
    assert por["WL"].detectado_sombra == "si" and por["WL"].detalle_sombra == "A"
    assert por["AUD"].motivo_no_detectado == "intradia:patron"
    assert por["AUD"].detectado_sombra == "no" and por["AUD"].detalle_sombra == "bajo_vwap"
    assert por["FUERA"].motivo_no_detectado == "fuera_del_universo"
    assert por["FUERA"].detalle_sombra == "no_salio_en_screener"
    assert por["SINSLOT"].motivo_no_detectado == "ranura_no_escaneada"
    assert por["CHICA"].motivo_no_detectado == "universo:vol_bajo_small"
    assert (por["WL"].noticia_yahoo, por["WL"].noticia_alpaca) == ("si", "si")
    assert (por["AUD"].noticia_yahoo, por["AUD"].noticia_alpaca) == ("no", "si")
    assert por["FUERA"].noticia_alpaca == "no"
    assert r["watchlist"]["detectados"] == 1 and r["sombra"]["detectados"] == 1
    assert r["error"] is None


def test_alpaca_pide_desde_el_dia_habil_anterior_al_host_de_datos(tmp_path):
    esc = _escenario(tmp_path)
    tc.correr(**esc)
    ruta, params = esc["cliente_alpaca"].pedidos[0]
    assert ruta == "/v1beta1/news"
    # 29/9 00:00 en Nueva York = 04:00 UTC.
    assert params["start"] == "2026-09-29T04:00:00Z"


def test_sin_credenciales_de_alpaca_es_error_no_no(tmp_path):
    from shadow_alpaca.cliente import ErrorDatos

    class _SinClave:
        def paginas(self, *a):
            raise ErrorDatos("sin_credenciales")

    filas, r = tc.correr(**{**_escenario(tmp_path), "cliente_alpaca": _SinClave()})
    assert {f.noticia_alpaca for f in filas} == {"error:sin_credenciales"}
    assert r["noticia_alpaca"]["error"] == len(filas)


def test_screener_caido_falla_cerrado(tmp_path):
    def _roto(*a, **k):
        raise TimeoutError("http://algo?token=secreto")

    filas, r = tc.correr(**{**_escenario(tmp_path), "screen": _roto})
    assert filas == [] and r["error"] == "screener:TimeoutError"
    assert "secreto" not in json.dumps(r)


def test_feriado_avisa_que_las_cotizaciones_son_de_otro_dia(tmp_path):
    viejo = [_quote("WL", cuando=AHORA - timedelta(days=1))]
    filas, r = tc.correr(**{**_escenario(tmp_path), "screen": lambda *a, **k: {"quotes": viejo}})
    assert filas == []
    assert any("otro día" in a for a in r["avisos"])


def test_la_corrida_no_escribe_la_watchlist(tmp_path, monkeypatch):
    from momentum_hunter import watchlist

    def _prohibido(*a, **k):
        raise AssertionError("la tasa de captura no escribe la watchlist")

    monkeypatch.setattr(watchlist, "guardar", _prohibido)
    monkeypatch.setattr(watchlist, "guardar_vps_state", _prohibido)
    tc.correr(**_escenario(tmp_path))


# ───────────────────────── disco ─────────────────────────

def test_guardar_escribe_csv_resumen_e_historico_sin_duplicar_el_dia(tmp_path):
    filas, r = tc.correr(**_escenario(tmp_path))
    salida = tmp_path / "salida"
    tc.guardar(salida, filas, r)
    tc.guardar(salida, filas, r)       # misma fecha otra vez
    otro = {**r, "fecha": "2026-09-29"}
    tc.guardar(salida, [], otro)

    with (salida / f"{HOY}.csv").open(encoding="utf-8") as fh:
        filas_csv = list(csv.DictReader(fh))
    assert {f["ticker"] for f in filas_csv} == {f.ticker for f in filas}
    assert list(filas_csv[0]) == tc.COLUMNAS
    wl = next(f for f in filas_csv if f["ticker"] == "WL")
    assert wl["motivo_no_detectado"] == ""          # None es celda vacía, no "0" ni "None"
    assert json.loads((salida / f"{HOY}.resumen.json").read_text())["movers_reales"] == len(filas)
    with (salida / tc.HISTORICO).open(encoding="utf-8") as fh:
        hist = list(csv.DictReader(fh))
    assert [h["fecha"] for h in hist] == ["2026-09-29", HOY]


def test_no_se_puede_escribir_dentro_del_hunter_ni_del_paper():
    r = tc.resumir([], HOY, no_verificables=[], descartes=Counter(), truncado=False)
    for paquete in ("momentum_hunter", "momentum_paper_trader"):
        with pytest.raises(ValueError, match="directorio_operativo"):
            tc.guardar(REPO / paquete / "tasa_captura", [], r)


def test_dir_salida_por_defecto_es_el_estado_del_vps(monkeypatch, tmp_path):
    monkeypatch.delenv(tc.ENV_DIR, raising=False)
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path))
    assert tc.dir_salida() == tmp_path / "tasa_captura"
    monkeypatch.setenv(tc.ENV_DIR, str(tmp_path / "otra"))
    assert tc.dir_salida() == tmp_path / "otra"


def test_main_manda_telegram_solo_con_la_bandera(tmp_path, monkeypatch):
    from momentum_hunter import run as hunter_run

    enviados = []
    monkeypatch.setattr(hunter_run, "enviar_telegram", lambda texto, **k: enviados.append(texto))
    filas, r = tc.correr(**_escenario(tmp_path))
    monkeypatch.setattr(tc, "correr", lambda: (filas, r))
    assert tc.main(["--salida", str(tmp_path / "s")]) == 0
    assert enviados == []
    assert tc.main(["--salida", str(tmp_path / "s"), "--telegram"]) == 0
    assert len(enviados) == 1 and "Tasa de captura" in enviados[0]


# ───────────────────────── despliegue ─────────────────────────

def test_wrapper_es_no_op_salvo_variable_y_el_timer_va_en_hora_de_nueva_york():
    wrapper = (REPO / "infra/systemd/bin/run_tasa_captura.sh").read_text(encoding="utf-8")
    assert 'MOMENTUM_TASA_CAPTURA:-0}" != "1"' in wrapper
    assert "MOMENTUM_TASA_CAPTURA_TELEGRAM" in wrapper
    assert "-m shadow_alpaca tasa_captura" in wrapper
    timer = (REPO / "infra/systemd/momentum-tasa-captura.timer").read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 16:25:00 America/New_York" in timer
    assert "Persistent=false" in timer
