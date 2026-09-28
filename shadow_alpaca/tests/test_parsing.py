"""Parseo, ausencia que no es cero, y el mismo detector en las dos fuentes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from momentum_hunter.catalysts.detector import Titular
from momentum_hunter.universe import slot_rotativo
from shadow_alpaca.cliente import ClienteDatos, ErrorDatos
from shadow_alpaca.historia import (
    barra_de_alpaca,
    barras_de_chart_yahoo,
    descargar_simbolo,
    diff_barras,
    formatear_diff,
)
from shadow_alpaca.informe import acuerdo, resumir
from shadow_alpaca.lectura import (
    tickers_de_watchlist,
    tickers_del_slot,
    tickers_movers_jsonl,
    ventana_como_el_hunter,
    ventana_por_slot,
)
from shadow_alpaca.noticias import (
    LIMITE_PAGINA,
    armar_registro,
    correr as correr_noticias,
    extraer_pagina,
    titular_de_noticia,
)
from shadow_alpaca.numeros import numero
from shadow_alpaca.screener import (
    MOTIVO_MARKET_CAP,
    TOP_ACTIVOS,
    TOP_MOVERS,
    fila_activo,
    parsear_activos,
    solape,
    correr as correr_screener,
)

AHORA = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


def test_un_ausente_no_es_cero_y_un_cero_real_se_conserva():
    assert numero(None) is None
    assert numero(False) is None
    assert numero(True) is None
    assert numero("") is None
    assert numero("0") is None
    assert numero(float("nan")) is None
    assert numero(0) == 0.0
    assert numero(0.0) == 0.0


def test_noticia_sin_headline_no_cuenta_y_la_fecha_zeta_queda_con_huso():
    assert titular_de_noticia({"source": "benzinga"}) is None
    assert titular_de_noticia({"headline": "   "}) is None
    t = titular_de_noticia({
        "headline": "FDA approval",
        "created_at": "2026-09-28T14:00:00Z",
    })
    assert t is not None
    assert t.fuente == "desconocida"
    assert t.fecha == "2026-09-28T14:00:00+00:00"


def test_pagina_de_noticias_sin_clave_no_es_lista_vacia():
    with pytest.raises(ErrorDatos, match="news_ausente"):
        extraer_pagina({"next_page_token": None})
    items, token = extraer_pagina({"news": [], "next_page_token": ""})
    assert items == [] and token is None


def test_el_mismo_detector_en_las_dos_fuentes_y_el_retraso_no_inventa_minutos():
    alpaca = [Titular("FDA approval for the drug", "benzinga", "2026-09-28T14:00:00+00:00")]
    yahoo = [Titular("Company gets FDA approval today", "Reuters", "2026-09-28T14:40:00+00:00")]
    reg = armar_registro(
        "ACME",
        titulares_alpaca=alpaca, alpaca_ok=True, alpaca_motivo=None,
        titulares_yahoo=yahoo, yahoo_ok=True, yahoo_motivo=None,
        ahora=AHORA, en_slot=True, en_watchlist=False, slot=2, n_slots=8,
        scan_inicio_ts="2026-09-28T14:01:00+00:00",
    )
    assert reg["alpaca"]["catalizador"] == "fda"
    assert reg["yahoo"]["catalizador"] == "fda"
    assert reg["alpaca"]["titulares"] == 1
    assert reg["lag_min_articulo_a_deteccion"]["alpaca"] == pytest.approx(60.0)
    assert reg["lag_min_articulo_a_deteccion"]["yahoo"] == pytest.approx(20.0)
    assert reg["lag_min_entre_fuentes"] == pytest.approx(40.0)
    assert reg["fuente_mas_temprana"] == "alpaca"

    sin_hora = [Titular("FDA approval", "Yahoo", "2026-09-28")]
    reg2 = armar_registro(
        "ACME",
        titulares_alpaca=alpaca, alpaca_ok=True, alpaca_motivo=None,
        titulares_yahoo=sin_hora, yahoo_ok=True, yahoo_motivo=None,
        ahora=AHORA, en_slot=True, en_watchlist=False, slot=None, n_slots=None,
        scan_inicio_ts=None,
    )
    assert reg2["yahoo"]["catalizador"] == "fda"
    assert reg2["lag_min_articulo_a_deteccion"]["yahoo"] is None
    assert reg2["lag_min_entre_fuentes"] is None


def test_yahoo_en_pausa_no_se_pide_y_no_queda_como_vacio(tmp_path):
    class _Yahoo:
        def __init__(self):
            self.llamadas = []

        def titulares(self, ticker):
            self.llamadas.append(ticker)
            return []

    class _Pausa:
        def activa(self, ahora=None):
            return True

    class _Cliente:
        def paginas(self, path, params, tope):
            assert params["limit"] == LIMITE_PAGINA == 50
            return [{
                "news": [{
                    "headline": "FDA approval today",
                    "source": "benzinga",
                    "created_at": "2026-09-28T14:00:00Z",
                    "symbols": ["ACME"],
                }],
                "next_page_token": None,
            }], False

    yahoo = _Yahoo()
    rc = correr_noticias(
        cliente=_Cliente(),
        dir_salida_path=tmp_path,
        dir_telemetria=tmp_path,
        watchlist_path=tmp_path / "no-esta.json",
        ahora=AHORA,
        proveedor_yahoo=yahoo,
        pausa_yahoo=_Pausa(),
        tickers_explicitos=["ACME"],
    )
    assert rc == 0 and yahoo.llamadas == []
    lineas = [
        json.loads(l) for l in (tmp_path / "2026-09-28" / "noticias.jsonl").read_text().splitlines()
    ]
    fila = next(l for l in lineas if l.get("tipo") == "noticia")
    assert fila["yahoo"]["disponible"] is False
    assert fila["yahoo"]["motivo"] == "pausa_429"
    assert fila["yahoo"]["titulares"] is None
    assert fila["alpaca"]["titulares"] == 1
    assert fila["alpaca"]["catalizador"] == "fda"


def test_un_429_de_yahoo_a_mitad_de_corrida_no_se_anota_como_cero_titulares(tmp_path):
    class _Limite(Exception):
        pass

    class _Yahoo:
        def __init__(self):
            self.n = 0

        def titulares(self, ticker):
            self.n += 1
            self._metricas.registrar_error("noticias", _Limite())
            # El nombre tiene que parecer un rate limit. Se renombra la clase.
            return []

    # El tipo se llama RateLimit para que el freno lo reconozca sin leer el mensaje.
    _Limite.__name__ = "YFRateLimitError"

    class _Cliente:
        def paginas(self, path, params, tope):
            return [{"news": [], "next_page_token": None}], False

    class _Pausa:
        def activa(self, ahora=None):
            return False

    yahoo = _Yahoo()
    correr_noticias(
        cliente=_Cliente(),
        dir_salida_path=tmp_path,
        dir_telemetria=tmp_path,
        watchlist_path=tmp_path / "no-esta.json",
        ahora=AHORA,
        proveedor_yahoo=yahoo,
        pausa_yahoo=_Pausa(),
        tickers_explicitos=["AAA", "BBB"],
    )
    assert yahoo.n == 1
    lineas = [json.loads(l) for l in (tmp_path / "2026-09-28" / "noticias.jsonl").read_text().splitlines()]
    por_ticker = {l["ticker"]: l for l in lineas if l.get("tipo") == "noticia"}
    for ticker in ("AAA", "BBB"):
        assert por_ticker[ticker]["yahoo"]["disponible"] is False
        assert por_ticker[ticker]["yahoo"]["titulares"] is None
        assert por_ticker[ticker]["yahoo"]["motivo"] == "pausa_429"


def test_sin_credenciales_no_dispara_yahoo_ni_anota_cero_tickers(tmp_path):
    class _Yahoo:
        def __init__(self):
            self.n = 0

        def titulares(self, ticker):
            self.n += 1
            return []

    class _Cliente:
        def paginas(self, path, params, tope):
            raise ErrorDatos("sin_credenciales")

    class _Pausa:
        def activa(self, ahora=None):
            return False

    yahoo = _Yahoo()
    rc = correr_noticias(
        cliente=_Cliente(),
        dir_salida_path=tmp_path,
        dir_telemetria=tmp_path,
        watchlist_path=tmp_path / "no-esta.json",
        ahora=AHORA,
        proveedor_yahoo=yahoo,
        pausa_yahoo=_Pausa(),
        tickers_explicitos=["ACME"],
    )
    assert rc == 2 and yahoo.n == 0
    lineas = [json.loads(l) for l in (tmp_path / "2026-09-28" / "noticias.jsonl").read_text().splitlines()]
    assert lineas[0]["tickers"] is None
    assert lineas[0]["motivo"] == "sin_credenciales"


def test_market_cap_no_se_filtra_y_un_cero_del_payload_no_se_copia():
    fila = fila_activo({"symbol": "aapl", "volume": 10, "market_cap": 0, "trade_count": None})
    assert fila is not None
    assert fila["symbol"] == "AAPL"
    assert fila["market_cap"] is None
    assert fila["motivo_market_cap"] == MOTIVO_MARKET_CAP
    assert fila["volume"] == 10.0
    assert fila["trade_count"] is None
    bloque = parsear_activos({
        "most_actives": [
            {"symbol": "AAA", "market_cap": 0},
            {"symbol": "BBB"},
            {"volume": 5},
        ],
    })
    assert bloque["disponible"] is True
    assert bloque["n"] == 2
    assert all(f["market_cap"] is None for f in bloque["filas"])
    assert bloque["filas"][0]["volume"] is None


def test_most_actives_ausente_no_es_un_top_vacio():
    bloque = parsear_activos({"last_updated": "x"})
    assert bloque["disponible"] is False
    assert bloque["n"] is None
    assert bloque["filas"] is None


def test_solape_con_base_ausente_no_es_cero():
    vacio = solape({"AAA"}, None, "sin_movers_jsonl")
    assert vacio["n"] is None and vacio["pct"] is None
    assert vacio["motivo"] == "sin_movers_jsonl"
    real = solape({"AAA", "BBB"}, {"CCC"}, None)
    assert real["n"] == 0 and real["pct"] == 0.0
    indefinido = solape(set(), {"AAA"}, None)
    assert indefinido["n"] == 0 and indefinido["pct"] is None


def test_screener_registra_solapes_y_fuera_de_sesion_no_llama(tmp_path):
    simbolos = ["AAA", "BBB"] + [f"T{i:02d}" for i in range(18)]
    dia = tmp_path / "telemetria" / "2026-09-28" / "vps"
    dia.mkdir(parents=True)
    evento = {
        "modo": "escaneo", "timestamp": "2026-09-28T14:12:00+00:00",
        "inicio_ts": "2026-09-28T14:01:00+00:00",
        "slot": 0, "n_slots": 2, "universo_escaneado": 10, "universo_total": 20,
    }
    (dia / "events.jsonl").write_text(json.dumps(evento) + "\n", encoding="utf-8")
    (dia / "movers.jsonl").write_text(json.dumps({
        "candidatas": [{"ticker": "AAA"}, {"ticker": "ZZZ"}],
    }) + "\n", encoding="utf-8")
    auditoria = tmp_path / "auditoria"
    auditoria.mkdir()
    (auditoria / "2026-09-28.json").write_text(json.dumps({
        "corridas": [{"candidatos": [{"ticker": "BBB"}]}],
    }), encoding="utf-8")

    class _Cliente:
        def __init__(self):
            self.n = 0

        def get(self, path, params):
            self.n += 1
            if path.endswith("most-actives"):
                assert params["top"] == TOP_ACTIVOS == 100
                return {"most_actives": [
                    {"symbol": "AAA", "volume": 3, "trade_count": 1, "market_cap": 0},
                    {"symbol": "BBB", "trade_count": 2},
                ]}
            assert params["top"] == TOP_MOVERS == 50
            return {"gainers": [{"symbol": "AAA", "percent_change": 1.2, "price": 4}], "losers": []}

    cliente = _Cliente()
    rc = correr_screener(
        cliente=cliente,
        dir_salida_path=tmp_path / "salida",
        dir_telemetria=tmp_path / "telemetria",
        dir_auditoria=auditoria,
        ahora=AHORA,
        cargar_simbolos=lambda: simbolos,
    )
    assert rc == 0 and cliente.n == 2
    linea = json.loads((tmp_path / "salida" / "2026-09-28" / "screener.jsonl").read_text().splitlines()[0])
    assert linea["market_cap"] is None
    assert linea["filtro_market_cap"] is False
    assert linea["most_actives"]["filas"][0]["market_cap"] is None
    assert linea["most_actives"]["filas"][1]["volume"] is None
    assert linea["solapes"]["slot"]["n"] == 2
    assert linea["solapes"]["slot"]["pct"] == pytest.approx(100.0)
    assert linea["solapes"]["movers_jsonl"]["n"] == 1
    assert linea["solapes"]["movers_jsonl"]["pct"] == pytest.approx(50.0)
    assert linea["solapes"]["candidatos_auditoria"]["tickers"] == ["BBB"]

    cliente.n = 0
    domingo = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)
    rc = correr_screener(
        cliente=cliente,
        dir_salida_path=tmp_path / "salida",
        dir_telemetria=tmp_path / "telemetria",
        dir_auditoria=auditoria,
        ahora=domingo,
        cargar_simbolos=lambda: simbolos,
    )
    assert rc == 0 and cliente.n == 0
    fuera = json.loads((tmp_path / "salida" / "2026-09-27" / "screener.jsonl").read_text())
    assert fuera["most_actives"] is None and fuera["solapes"] is None
    assert fuera["motivo"] == "fuera_de_sesion"


def test_sin_movers_jsonl_el_solape_queda_ausente(tmp_path):
    tickers, motivo = tickers_movers_jsonl(tmp_path, "2026-09-28")
    assert tickers is None and motivo == "sin_movers_jsonl"


def test_la_ventana_del_slot_es_la_del_hunter_y_sin_slot_no_se_corta_el_prefijo():
    simbolos = [f"T{i:04d}" for i in range(95)]
    limite = 10
    ahora = datetime(2026, 9, 21, 14, 1, tzinfo=UTC)
    ranura = slot_rotativo(len(simbolos), limite, ahora)
    assert ranura is not None
    slot, n_slots = ranura
    assert ventana_por_slot(simbolos, limite, slot) == ventana_como_el_hunter(simbolos, limite, ahora)
    # La última ventana hace wrap. Tiene que ser el mismo wrap del hunter,
    # no un corte corto ni el prefijo fijo de las más grandes.
    ultimo = n_slots - 1
    hallado = None
    for i in range(n_slots * 2):
        t = ahora + timedelta(minutes=30 * i)
        otra = slot_rotativo(len(simbolos), limite, t)
        if otra is not None and otra[0] == ultimo:
            hallado = t
            break
    assert hallado is not None
    assert ventana_por_slot(simbolos, limite, ultimo) == ventana_como_el_hunter(simbolos, limite, hallado)
    assert len(ventana_por_slot(simbolos, limite, ultimo)) == limite
    assert tickers_del_slot(
        {"slot": None, "universo_escaneado": 10}, simbolos,
    ) == (None, "slot_ausente_no_se_inventa_el_corte")
    assert tickers_del_slot(None, simbolos)[1] == "sin_escaneo"


def test_watchlist_ilegible_no_es_lista_vacia(tmp_path):
    ausente, motivo = tickers_de_watchlist(tmp_path / "no.json")
    assert ausente is None and motivo == "sin_archivo"
    rota = tmp_path / "watchlist.json"
    rota.write_text("{", encoding="utf-8")
    tickers, motivo = tickers_de_watchlist(rota)
    assert tickers is None and motivo == "ilegible"
    rota.write_text(json.dumps({"entradas": [{
        "ticker": "abc", "nombre": "Abc", "estado": "watching",
        "creado_en": "2026-09-28T13:00:00+00:00",
        "actualizado_en": "2026-09-28T13:00:00+00:00",
        "transiciones": [{
            "estado": "watching", "timestamp": "2026-09-28T13:00:00+00:00", "motivo": "x",
        }],
    }]}), encoding="utf-8")
    tickers, motivo = tickers_de_watchlist(rota)
    assert tickers == ["ABC"] and motivo is None


def test_barra_sin_volumen_no_se_guarda_y_el_diff_no_la_trata_como_cero(tmp_path):
    assert barra_de_alpaca({
        "t": "2016-01-04T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5,
    }) is None
    assert barra_de_alpaca({
        "t": "2016-01-04T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0,
    })["v"] == 0.0

    class _Cliente:
        def paginas(self, path, params, tope):
            assert path == "/v2/stocks/bars"
            assert params["adjustment"] == "split"
            assert params["feed"] == "sip"
            assert params["timeframe"] == "1Day"
            assert params["limit"] == 10_000
            return [{
                "bars": {"AAPL": [
                    {"t": "2016-01-04T05:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
                    {"t": "2016-01-05T05:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": None},
                ]},
            }], False

    meta = descargar_simbolo(
        _Cliente(), "AAPL", "1Day", datetime(2016, 1, 4).date(), datetime(2016, 1, 8).date(), tmp_path,
    )
    assert meta["n_barras"] == 1
    assert meta["descartadas_incompletas"] == 1
    assert meta["truncado"] is False
    texto = (tmp_path / "AAPL" / "1Day.jsonl").read_text(encoding="utf-8")
    assert texto.count("\n") == 1
    assert '"v": null' not in texto and '"v": 0' not in texto

    sip = [json.loads(texto)]
    yahoo = [{
        "t": "2016-01-04T14:30:00+00:00", "o": 10, "h": 11, "l": 9, "c": 10, "v": 90,
    }, {
        "t": "2016-01-06T14:30:00+00:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1,
    }]
    diff = diff_barras(sip, yahoo, "1Day")
    assert diff["n_en_ambos"] == 1
    assert diff["n_solo_sip"] == 0
    assert diff["n_solo_yahoo"] == 1
    assert diff["mediana_diff_cierre_pct"] == pytest.approx(5.0)
    solo = diff_barras(sip, None, "1Day")
    assert solo["n_yahoo"] is None
    assert solo["n_en_ambos"] is None
    assert solo["mediana_abs_diff_cierre_pct"] is None
    texto_diff = formatear_diff("AAPL", "1Day", solo, "pausa_429")
    assert "no disponible" in texto_diff
    assert "0" not in texto_diff.split("Yahoo no disponible")[0] or "velas SIP" in texto_diff


def test_chart_de_yahoo_con_volumen_ausente_no_inventa_la_vela():
    cuerpo = {"chart": {"result": [{
        "timestamp": [1451865600, 1451952000],
        "indicators": {"quote": [{
            "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
            "close": [1.0, 2.0], "volume": [None, 5],
        }]},
    }]}}
    velas, motivo = barras_de_chart_yahoo(cuerpo)
    assert motivo is None and velas is not None and len(velas) == 1
    assert velas[0]["v"] == 5.0
    nada, motivo = barras_de_chart_yahoo({"chart": {"result": None, "error": {"code": "x"}}})
    assert nada is None and motivo == "yahoo_sin_resultado"


def test_informe_en_espanol_no_promedia_ausencias_como_cero():
    filas = [
        {
            "tipo": "noticia", "ticker": "AAA",
            "alpaca": {"disponible": True, "catalizador": "fda"},
            "yahoo": {"disponible": True, "catalizador": "fda"},
            "lag_min_articulo_a_deteccion": {"alpaca": 10, "yahoo": 30},
            "lag_min_entre_fuentes": 20,
        },
        {
            "tipo": "noticia", "ticker": "BBB",
            "alpaca": {"disponible": True, "catalizador": "contrato"},
            "yahoo": {"disponible": True, "catalizador": None},
            "lag_min_articulo_a_deteccion": {"alpaca": 30, "yahoo": None},
            "lag_min_entre_fuentes": None,
        },
        {
            "tipo": "noticia", "ticker": "CCC",
            "alpaca": {"disponible": True, "catalizador": "fda"},
            "yahoo": {"disponible": False, "motivo": "pausa_429", "catalizador": None, "titulares": None},
            "lag_min_articulo_a_deteccion": {"alpaca": 0, "yahoo": None},
            "lag_min_entre_fuentes": None,
        },
    ]
    ac = acuerdo([f for f in filas if f["tipo"] == "noticia"])
    assert ac["comparables"] == 2 and ac["acuerdos"] == 1
    assert ac["pct"] == pytest.approx(50.0)
    assert ac["excluidos"] == 1
    assert ac["solo_alpaca"] == ["contrato"]
    screeners = [
        {"tipo": "screener", "en_sesion": True, "solapes": {
            "slot": {"pct": 10}, "candidatos_auditoria": {"pct": None}, "movers_jsonl": {"pct": 40},
        }},
        {"tipo": "screener", "en_sesion": False, "motivo": "fuera_de_sesion", "solapes": None},
        {"tipo": "screener", "en_sesion": True, "solapes": {
            "slot": {"pct": 30}, "candidatos_auditoria": {"pct": None}, "movers_jsonl": {"pct": None},
        }},
    ]
    texto = resumir(filas, screeners, ["2026-09-28"])
    assert "Acuerdo de catalizador: 50.0 %" in texto
    assert "solo en Alpaca: contrato" in texto
    assert "Mediana del retraso artículo→detección, Alpaca: 10.0 min" in texto
    # El None de Yahoo no entra como 0: si entrara, la mediana de 30 y 0 sería 15.
    assert "Mediana del retraso artículo→detección, Yahoo: 30.0 min" in texto
    assert "Mediana del retraso entre fuentes: 20.0 min" in texto
    assert "candidatos de la auditoría: sin datos" in texto
    assert "Mediana del solape con el slot del escaneo: 20.0 %" in texto
    assert "Mediana del solape con movers.jsonl: 40.0 %" in texto
    assert "sin datos" in texto
    assert "null" in texto
    assert "no alimenta" in texto


def test_el_cliente_pagina_con_limite_50_y_no_loguea_el_secreto(caplog):
    import logging

    paginas = {"n": 0}

    class _Resp:
        status_code = 200
        headers = {}

        def json(self):
            return cuerpo

    def transport(url, params, headers, timeout):
        assert url == "https://data.alpaca.markets/v1beta1/news"
        assert headers["APCA-API-SECRET-KEY"] == "SECRETO-NO-LOG"
        paginas["n"] += 1
        if "page_token" not in params:
            assert params["limit"] == 50
            return type("R", (), {
                "status_code": 200,
                "headers": {},
                "json": lambda self=None: {"news": [], "next_page_token": "tok"},
            })()
        assert params["page_token"] == "tok"
        return type("R", (), {
            "status_code": 200,
            "headers": {},
            "json": lambda self=None: {"news": [], "next_page_token": None},
        })()

    cliente = ClienteDatos(api_key="KEY-NO-LOG", api_secret="SECRETO-NO-LOG", transport=transport, reintentos=1)
    out, truncado = cliente.paginas("/v1beta1/news", {"limit": 50, "symbols": "AAPL"}, 5)
    assert truncado is False and len(out) == 2 and paginas["n"] == 2

    def boom(*a, **k):
        raise __import__("requests").RequestException("SECRETO-NO-LOG en la url")

    cliente2 = ClienteDatos(api_key="KEY-NO-LOG", api_secret="SECRETO-NO-LOG", transport=boom, reintentos=1)
    caplog.set_level(logging.WARNING)
    with pytest.raises(ErrorDatos, match="red"):
        cliente2.get("/v2/stocks/bars", {})
    assert "SECRETO-NO-LOG" not in caplog.text
    assert "KEY-NO-LOG" not in caplog.text


def test_directorio_de_salida_rechaza_la_telemetria_del_hunter(tmp_path, monkeypatch):
    from shadow_alpaca.jsonl_log import append_linea

    raiz = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError):
        append_linea(raiz / "momentum_hunter" / "telemetria" / "2026-09-28" / "noticias.jsonl", {"a": 1})
    destino = tmp_path / "ok" / "noticias.jsonl"
    append_linea(destino, {"tipo": "noticia", "titulares": None})
    assert json.loads(destino.read_text())["titulares"] is None


def test_yahoo_pausa_en_el_diff_no_es_serie_vacia(monkeypatch):
    from shadow_alpaca.historia import pedir_yahoo

    class _Pausa:
        def activa(self, ahora=None):
            return True

    def transport(*a, **k):
        raise AssertionError("no debía pedir Yahoo")

    velas, motivo = pedir_yahoo(
        "AAPL", "1Day", datetime(2016, 1, 4).date(), datetime(2016, 1, 5).date(),
        pausa=_Pausa(), transport=transport,
    )
    assert velas is None and motivo == "pausa_429"
