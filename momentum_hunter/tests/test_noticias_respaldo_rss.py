"""Respaldo RSS de noticias y aviso de "noticias ciegas" (2026-09-29).

Ese día `finance.yahoo.com/xhr/ncp` (el endpoint de `.news` en yfinance
1.7.0) respondía HTTP 500 y yfinance devolvía `[]` sin excepción: 4
escaneos con titulares_total=0 sin un error contado. Sin red: yfinance
se sustituye en `sys.modules` y el RSS se inyecta o se parchea.
"""

from __future__ import annotations

import json
import sys
import types
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter import telemetria
from momentum_hunter.catalysts import detector
from momentum_hunter.catalysts.detector import (
    FUENTE_RSS,
    Titular,
    YahooNewsProvider,
    detectar_catalizador,
    parsear_rss_yahoo,
)
from momentum_hunter.catalysts.detector import titulares_rss_yahoo as _rss_real
from momentum_hunter.config import CONFIG

RSS_KMX = """\ufeff<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<rss version="2.0"><channel>
<description>Latest Financial News for KMX</description>
<item>
  <title>CarMax Q2 Results Top Estimates</title>
  <link>https://finance.yahoo.com/x</link>
  <pubDate>Tue, 29 Sep 2026 13:46:00 +0000</pubDate>
</item>
<item>
  <title>What CarMax (KMX) Said on Its Q2 Earnings Call</title>
  <pubDate>Tue, 29 Sep 2026 10:03:01 -0400</pubDate>
</item>
<item><title>   </title></item>
<item><title>Sin fecha</title></item>
<item><title>Fecha rota</title><pubDate>ayer</pubDate></item>
</channel></rss>"""

RSS_VACIO = """<?xml version="1.0"?><rss version="2.0"><channel>
<description>Latest Financial News for </description></channel></rss>"""


def _yf_falso(monkeypatch, news=None, excepcion=None):
    llamadas = []

    class _Ticker:
        def __init__(self, t):
            llamadas.append(t)

        @property
        def news(self):
            if excepcion is not None:
                raise excepcion
            return news

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Ticker))
    return llamadas


# --- parseo del RSS ---------------------------------------------------

def test_parsear_rss_formato_titular_y_fecha_utc():
    ts = parsear_rss_yahoo(RSS_KMX)
    assert [t.texto for t in ts] == [
        "CarMax Q2 Results Top Estimates",
        "What CarMax (KMX) Said on Its Q2 Earnings Call",
        "Sin fecha",
        "Fecha rota",
    ]
    assert ts[0].fecha == "2026-09-29T13:46:00+00:00"
    # -0400 → UTC, timestamp completo (lo usa minutos_desde_catalizador)
    assert ts[1].fecha == "2026-09-29T14:03:01+00:00"
    assert ts[2].fecha is None and ts[3].fecha is None
    assert {t.fuente for t in ts} == {FUENTE_RSS}


def test_parsear_rss_vacio_es_lista_vacia():
    assert parsear_rss_yahoo(RSS_VACIO) == []


def test_parsear_rss_xml_roto_lanza():
    with pytest.raises(ET.ParseError):
        parsear_rss_yahoo("<html>Will be right back")


def test_rss_titulares_alimentan_el_detector_igual():
    ts = parsear_rss_yahoo(RSS_KMX)
    c = detectar_catalizador(ts, CONFIG, hoy=date(2026, 9, 29))
    assert c is not None and c.tipo == "earnings"
    assert c.titular == "CarMax Q2 Results Top Estimates"


def test_rumor_solo_con_rss_nunca_junta_dos_fuentes():
    # El RSS no trae el medio: la regla de rumores no se relaja.
    ts = [Titular("X reportedly in talks", FUENTE_RSS, "2026-09-29T10:00:00+00:00"),
          Titular("X is said to be exploring sale", FUENTE_RSS, "2026-09-29T11:00:00+00:00")]
    assert CONFIG.fuentes_minimas_rumor >= 2
    assert detectar_catalizador(ts, CONFIG, hoy=date(2026, 9, 29)) is None


def test_titulares_rss_yahoo_pide_el_feed_del_ticker(monkeypatch):
    visto = {}

    class _Resp:
        text = RSS_KMX

        def raise_for_status(self):
            return None

    def _get(url, params=None, headers=None, timeout=None):
        visto.update(url=url, params=params, timeout=timeout)
        return _Resp()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=_get))
    ts = _rss_real("KMX")
    assert visto["url"] == "https://feeds.finance.yahoo.com/rss/2.0/headline"
    assert visto["params"]["s"] == "KMX"
    assert visto["timeout"] == detector.YAHOO_RSS_TIMEOUT_S
    assert len(ts) == 4


# --- proveedor --------------------------------------------------------

def test_con_titulares_de_yfinance_no_toca_el_rss(monkeypatch):
    _yf_falso(monkeypatch, news=[{"content": {"title": "A Awarded Contract",
                                              "provider": {"displayName": "Reuters"},
                                              "pubDate": "2026-09-29T10:00:00Z"}}])
    rss = []
    p = YahooNewsProvider(rss=lambda t: rss.append(t) or [])
    ts = p.titulares("A")
    assert [t.fuente for t in ts] == ["Reuters"]
    assert rss == []


def test_yfinance_vacio_cae_al_rss(monkeypatch):
    # Exactamente el 29-sep: HTTP 500 tragado → [] sin excepción.
    _yf_falso(monkeypatch, news=[])
    m = telemetria.Metricas()
    p = YahooNewsProvider(m, rss=lambda t: parsear_rss_yahoo(RSS_KMX))
    ts = p.titulares("KMX")
    assert len(ts) == 4
    assert dict(m.errores) == {}


def test_yfinance_excepcion_cuenta_y_cae_al_rss(monkeypatch):
    class YFRateLimitError(Exception):
        pass

    _yf_falso(monkeypatch, excepcion=YFRateLimitError("429"))
    m = telemetria.Metricas()
    p = YahooNewsProvider(m, rss=lambda t: parsear_rss_yahoo(RSS_KMX))
    assert len(p.titulares("KMX")) == 4
    assert m.errores["noticias:YFRateLimitError"] == 1


def test_rss_que_falla_cuenta_error_y_devuelve_vacio(monkeypatch):
    _yf_falso(monkeypatch, news=[])
    m = telemetria.Metricas()

    def _roto(t):
        raise ConnectionError("sin red")

    p = YahooNewsProvider(m, rss=_roto)
    assert p.titulares("KMX") == []
    assert m.errores["noticias_rss:ConnectionError"] == 1


def test_rss_apagable_por_entorno(monkeypatch):
    _yf_falso(monkeypatch, news=[])
    monkeypatch.setenv(detector.ENV_RSS_FALLBACK, "0")
    rss = []
    p = YahooNewsProvider(rss=lambda t: rss.append(t) or parsear_rss_yahoo(RSS_KMX))
    assert p.titulares("KMX") == []
    assert rss == []


def test_por_defecto_usa_titulares_rss_yahoo(monkeypatch):
    _yf_falso(monkeypatch, news=[])
    monkeypatch.setattr(detector, "titulares_rss_yahoo", lambda t: [Titular("x", FUENTE_RSS, None)])
    assert YahooNewsProvider().titulares("KMX") == [Titular("x", FUENTE_RSS, None)]


# --- aviso de noticias ciegas ------------------------------------------

@pytest.fixture
def _aviso(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "0")
    enviados = []
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda texto, **kw: enviados.append(texto))
    return enviados


def _metricas(operables=100, titulares=0):
    m = telemetria.Metricas()
    m.operables["small"] = operables
    m.titulares_total = titulares
    return m


AHORA = datetime(2026, 9, 29, 13, 8, tzinfo=UTC)


def test_aviso_una_vez_por_dia(_aviso):
    m = _metricas()
    m.errores["noticias_rss:ConnectionError"] = 3
    assert run_mod._alertar_noticias_ciegas(m, True, False, ahora=AHORA) is True
    assert run_mod._alertar_noticias_ciegas(m, True, False, ahora=AHORA.replace(hour=14)) is True
    assert len(_aviso) == 1
    assert "13:08 UTC" in _aviso[0] and "100" in _aviso[0]
    assert "noticias_rss:ConnectionError" in _aviso[0]
    marca = json.loads(run_mod.PATH_ALERTA_NOTICIAS.read_text())
    assert marca["fecha"] == "2026-09-29"
    # Otro día vuelve a avisar.
    run_mod._alertar_noticias_ciegas(m, True, False, ahora=datetime(2026, 9, 30, 13, 8, tzinfo=UTC))
    assert len(_aviso) == 2


def test_sin_aviso_con_titulares_o_pocos_operables_o_sin_catalizadores(_aviso):
    assert run_mod._alertar_noticias_ciegas(_metricas(titulares=5), True, False, ahora=AHORA) is False
    assert run_mod._alertar_noticias_ciegas(
        _metricas(operables=run_mod.MIN_OPERABLES_ALERTA_NOTICIAS - 1), True, False, ahora=AHORA) is False
    assert run_mod._alertar_noticias_ciegas(_metricas(), False, False, ahora=AHORA) is False
    assert run_mod._alertar_noticias_ciegas(None, True, False, ahora=AHORA) is False
    assert _aviso == []


def test_dry_run_detecta_pero_no_manda(_aviso):
    assert run_mod._alertar_noticias_ciegas(_metricas(), True, True, ahora=AHORA) is True
    assert _aviso == []
    assert not run_mod.PATH_ALERTA_NOTICIAS.resolver().exists()


def test_fallo_del_envio_no_tumba(_aviso, monkeypatch):
    def _explota(texto, **kw):
        raise RuntimeError("telegram caído")

    monkeypatch.setattr(run_mod, "enviar_telegram", _explota)
    assert run_mod._alertar_noticias_ciegas(_metricas(), True, False, ahora=AHORA) is True
