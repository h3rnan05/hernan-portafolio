"""Registro de auditoría `noticias_leidas`: observa, no decide.

Lo que se prueba:
  - el campo `link` de `Titular` no cambia igualdad ni decisiones;
  - el provider sigue devolviendo lo mismo y solo anota su estado;
  - la clasificación por acción (con/sin catalizador, casi pasan,
    sin keyword, sin noticias, error, sin dato);
  - la etapa 1 da los MISMOS candidatos con y sin registro, aunque el
    registro falle, y sin llamadas extra a la fuente de noticias;
  - la escritura conserva las últimas N corridas y nunca lanza;
  - el ejecutor no lee este archivo."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from momentum_hunter import noticias_leidas as nl
from momentum_hunter import run as run_mod
from momentum_hunter import telemetria
from momentum_hunter.catalysts import detector
from momentum_hunter.catalysts.detector import (
    CATALYST_KEYWORDS,
    Titular,
    YahooNewsProvider,
    clasificar_titular,
    detectar_catalizador,
    parsear_rss_yahoo,
)
from momentum_hunter.config import CONFIG
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Metadata
from momentum_hunter.tests.test_ancla import _barras

HOY = date.today().isoformat()
VIEJA = (date.today() - timedelta(days=CONFIG.dias_ventana_catalizador + 5)).isoformat()


# ------------------------------------------------------------ link


def test_link_no_entra_en_la_igualdad_ni_en_la_decision():
    a = Titular("Acme awarded contract", "Reuters", HOY)
    b = Titular("Acme awarded contract", "Reuters", HOY, "https://example.com/x")
    assert a == b and hash(a) == hash(b)
    assert detectar_catalizador([a], CONFIG) == detectar_catalizador([b], CONFIG)
    for texto in ("Acme reportedly in talks", "Acme opens office", "Acme Q2 results"):
        sin = [Titular(texto, "A", HOY), Titular(texto, "B", HOY)]
        con = [Titular(texto, "A", HOY, "https://a"), Titular(texto, "B", HOY, "https://b")]
        assert detectar_catalizador(sin, CONFIG) == detectar_catalizador(con, CONFIG)


def test_parsear_extrae_el_link_de_la_misma_respuesta():
    anidado = {"content": {"title": "T", "provider": {"displayName": "Reuters"},
                           "canonicalUrl": {"url": "https://finance.yahoo.com/n/1"}}}
    assert YahooNewsProvider._parsear(anidado).link == "https://finance.yahoo.com/n/1"
    click = {"content": {"title": "T", "clickThroughUrl": {"url": "https://x.test/2"}}}
    assert YahooNewsProvider._parsear(click).link == "https://x.test/2"
    plano = {"title": "T", "publisher": "AP", "link": "https://x.test/3"}
    assert YahooNewsProvider._parsear(plano).link == "https://x.test/3"
    assert YahooNewsProvider._parsear({"title": "T", "link": "javascript:alert(1)"}).link is None
    assert YahooNewsProvider._parsear({"title": "T"}).link is None
    rss = ('<rss><channel><item><title>A</title><link>https://x.test/r</link></item>'
           '<item><title>B</title></item></channel></rss>')
    assert [t.link for t in parsear_rss_yahoo(rss)] == ["https://x.test/r", None]


# ------------------------------------------------------------ estado del provider


def _yf(monkeypatch, news=None, error=None):
    import sys
    import types

    class _T:
        def __init__(self, t):
            if error:
                raise error

        @property
        def news(self):
            return news

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_T))


def test_estado_del_provider_no_cambia_lo_que_devuelve(monkeypatch):
    _yf(monkeypatch, news=[{"title": "Hola", "publisher": "AP"}])
    p = YahooNewsProvider(rss=lambda t: pytest.fail("no debe pedir RSS"))
    assert p.titulares("A") == [Titular("Hola", "AP", None)] and p.estado["A"] == "yfinance"

    _yf(monkeypatch, news=[])
    p = YahooNewsProvider(rss=lambda t: [Titular("r", "desconocida")])
    assert p.titulares("B") == [Titular("r", "desconocida")] and p.estado["B"] == "rss"
    p = YahooNewsProvider(rss=lambda t: [])
    assert p.titulares("C") == [] and p.estado["C"] == "vacio"

    def _roto(t):
        raise ConnectionError("x")
    p = YahooNewsProvider(rss=_roto)
    assert p.titulares("D") == [] and p.estado["D"] == "error"

    _yf(monkeypatch, error=RuntimeError("yf"))
    monkeypatch.setenv(detector.ENV_RSS_FALLBACK, "0")
    p = YahooNewsProvider()
    assert p.titulares("E") == [] and p.estado["E"] == "error"


# ------------------------------------------------------------ keyword


def test_keyword_de_coincide_con_clasificar_titular():
    for tipo, frases in CATALYST_KEYWORDS.items():
        for kw in frases:
            texto = f"Acme {kw.upper()} today"
            assert nl.keyword_de(texto)[0] == clasificar_titular(texto)
    assert nl.keyword_de("Acme opens office") is None and clasificar_titular("Acme opens office") is None
    assert nl.keyword_de("Acme Phase 1b Results") == ("fda", "phase 1b")


# ------------------------------------------------------------ clasificación


def _clas(titulares, estado="yfinance", ancla_ok=True):
    det = detectar_catalizador(titulares, CONFIG)
    final = det if ancla_ok else None
    return nl.clasificar("ACME", estado, titulares, det, final, CONFIG)


def test_con_catalizador_y_su_keyword():
    f = _clas([Titular("Acme awarded contract by Navy", "Reuters", HOY, "https://x")])
    assert f["resultado"] == nl.CON_CATALIZADOR and f["keyword"] == "awarded contract" and f["tipo"] == "contrato"
    assert f["noticias"][0]["link"] == "https://x" and f["n_noticias"] == 1


def test_casi_pasan_ancla_ventana_y_rumor():
    f = _clas([Titular("Other Co awarded contract", "Reuters", HOY)], ancla_ok=False)
    assert (f["resultado"], f["motivo"], f["keyword"]) == (nl.SIN_CATALIZADOR, "sin_ancla", "awarded contract")
    f = _clas([Titular("Acme awarded contract", "Reuters", VIEJA)])
    assert (f["resultado"], f["motivo"]) == (nl.SIN_CATALIZADOR, "fuera_ventana")
    assert f["noticias"][0]["motivo"] == "fuera_ventana"
    f = _clas([Titular("Acme reportedly in talks", "Reuters", HOY)])
    assert (f["resultado"], f["motivo"], f["tipo"]) == (nl.SIN_CATALIZADOR, "rumor_sin_fuentes", "rumor")
    assert f["motivo"] in nl.MOTIVOS_CASI


def test_sin_keyword_sin_noticias_error_y_sin_dato():
    f = _clas([Titular("Acme opens office", "Reuters", HOY)])
    assert (f["resultado"], f["motivo"], f["keyword"]) == (nl.SIN_CATALIZADOR, "sin_keyword", None)
    assert f["noticias"][0]["keyword"] is None and f["noticias"][0]["motivo"] == "sin_keyword"
    assert _clas([], "vacio")["resultado"] == nl.SIN_NOTICIAS
    assert _clas([], "error")["resultado"] == nl.ERROR_LECTURA
    # Estado desconocido y sin titulares: no se afirma "sin noticias".
    assert _clas([], None)["resultado"] == nl.SIN_DATO


def test_recorta_noticias_pero_guarda_el_total():
    ts = [Titular(f"Acme note {i}", "AP", HOY) for i in range(nl.MAX_NOTICIAS_POR_ACCION + 7)]
    f = _clas(ts)
    assert len(f["noticias"]) == nl.MAX_NOTICIAS_POR_ACCION and f["n_noticias"] == nl.MAX_NOTICIAS_POR_ACCION + 7


# ------------------------------------------------------------ etapa 1 idéntica


class _Provider(DataProvider):
    def __init__(self, meta):
        self._meta = meta

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {t: self._meta[t] for t in tickers if t in self._meta}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


TEXTOS = {
    "CHPT": "ChargePoint awarded contract by city",       # con catalizador
    "EBAY": "GameStop director buys shares",               # keyword ajena: ancla
    "NADA": "Nada Corp opens office",                      # sin keyword
    "VACIO": None,                                         # sin noticias
}


class _News:
    llamadas: list[str] = []

    def __init__(self, metricas=None):
        self.estado = {}

    def titulares(self, t):
        _News.llamadas.append(t)
        texto = TEXTOS[t]
        self.estado[t] = "yfinance" if texto else "vacio"
        return [Titular(texto, "Reuters", HOY, f"https://x.test/{t}")] if texto else []


def _etapa1(monkeypatch, registro):
    _News.llamadas = []
    monkeypatch.setattr(run_mod, "YahooNewsProvider", _News)
    tickers = list(TEXTOS)
    barras = {t: _barras(t, precio=10.0, vol_prom=500_000.0) for t in tickers}
    nombres = {"CHPT": "ChargePoint Holdings, Inc.", "EBAY": "eBay Inc.", "NADA": "Nada Corp", "VACIO": "Vacio Inc"}
    meta = {t: Metadata(ticker=t, nombre=nombres[t], market_cap=100_000_000.0) for t in tickers}
    m = telemetria.Metricas()
    cands = run_mod.construir_candidatos_diarios(
        tickers, barras, _Provider(meta), CONFIG, True, {t: "small" for t in tickers}, m,
        registro_noticias=registro)
    return cands, m.como_dict()["embudo"], list(_News.llamadas)


def _huella(cands):
    return [(c.ticker, c.catalizador, c.puntuacion.score_total, c.precio) for c in cands]


def test_etapa1_identica_con_y_sin_registro_y_sin_llamadas_extra(monkeypatch):
    sin, emb_sin, llam_sin = _etapa1(monkeypatch, None)
    reg = nl.Registro(datetime(2026, 9, 30, 14, 0, tzinfo=UTC), "vps")
    con, emb_con, llam_con = _etapa1(monkeypatch, reg)
    assert _huella(sin) == _huella(con) and emb_sin == emb_con
    assert llam_sin == llam_con == list(TEXTOS)          # una lectura por ticker, igual que antes
    res = {a["ticker"]: (a["resultado"], a["motivo"]) for a in reg.corrida["acciones"]}
    assert res == {"CHPT": (nl.CON_CATALIZADOR, None), "EBAY": (nl.SIN_CATALIZADOR, "sin_ancla"),
                   "NADA": (nl.SIN_CATALIZADOR, "sin_keyword"), "VACIO": (nl.SIN_NOTICIAS, None)}


def test_etapa1_identica_aunque_el_registro_falle(monkeypatch):
    sin, _, _ = _etapa1(monkeypatch, None)
    reg = nl.Registro(datetime(2026, 9, 30, 14, 0, tzinfo=UTC))
    monkeypatch.setattr(nl, "clasificar", lambda *a, **k: 1 / 0)
    con, _, _ = _etapa1(monkeypatch, reg)
    assert _huella(sin) == _huella(con) and reg.corrida["acciones"] == []


def test_main_escribe_el_registro_y_en_dry_run_no(monkeypatch, tmp_path):
    from momentum_hunter.tests.test_run import _preparar_main_escaneo
    barras = {"CHPT": _barras("CHPT", precio=10.0, vol_prom=500_000.0)}
    _preparar_main_escaneo(monkeypatch, tmp_path, barras, argv=["momentum_hunter.run", "--dry-run"])
    monkeypatch.setattr(run_mod, "YahooNewsProvider", _News)
    run_mod.main()
    assert not Path(nl.ARCHIVO).exists()
    _preparar_main_escaneo(monkeypatch, tmp_path, barras)
    run_mod.main()
    datos = json.loads(Path(nl.ARCHIVO).read_text(encoding="utf-8"))
    assert [a["ticker"] for a in datos["corridas"][0]["acciones"]] == ["CHPT"]


def _shortlist_de_main(monkeypatch, tmp_path, modo):
    """Corre `main` con la etapa 1 real y captura lo que llega a la etapa
    intradía, que es lo único por donde las noticias llegan a la
    watchlist. `modo`: normal | sin_registro | registro_roto."""
    from momentum_hunter.tests.test_run import _preparar_main_escaneo
    barras = {t: _barras(t, precio=10.0, vol_prom=500_000.0) for t in TEXTOS}
    _preparar_main_escaneo(monkeypatch, tmp_path, barras)
    monkeypatch.setattr(run_mod, "YahooNewsProvider", _News)
    nombres = {"CHPT": "ChargePoint Holdings, Inc.", "EBAY": "eBay Inc.", "NADA": "Nada Corp", "VACIO": "Vacio Inc"}
    monkeypatch.setattr(run_mod, "_proveedor_de_datos", lambda: _ProviderEscaneo(barras, nombres))
    if modo == "sin_registro":
        monkeypatch.setattr(run_mod, "_nuevo_registro_noticias", lambda inicio: None)
    elif modo == "registro_roto":
        monkeypatch.setattr(nl, "clasificar", lambda *a, **k: 1 / 0)
        monkeypatch.setattr(nl, "escribir", lambda *a, **k: 1 / 0)
    capturado = []

    def _intradia(shortlist, *a, **k):
        capturado.extend((c.ticker, c.catalizador, c.puntuacion.score_total) for c in shortlist)
        return []
    monkeypatch.setattr(run_mod, "construir_candidatos_intradia", _intradia)
    monkeypatch.setattr(run_mod, "seleccionar_y_auditar", lambda *a, **k: ([], {}, []))
    monkeypatch.setattr(run_mod.mercado, "evaluar", lambda provider: type("C", (), {"veredicto": "ok"})())
    watch = []
    monkeypatch.setattr(run_mod, "_actualizar_watchlist", lambda *a, **k: watch.append(a) or ([], {}, []))
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: None)
    monkeypatch.setattr(run_mod.radar, "construir_resumen", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.tracker, "cargar", lambda: [])
    monkeypatch.setattr(run_mod.tracker, "guardar", lambda xs: None)
    monkeypatch.setattr(run_mod.vigilancia, "vigilar", lambda *a, **k: [])
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda *a, **k: None)
    run_mod.main()
    return capturado, [a[0] for a in watch]


class _ProviderEscaneo(DataProvider):
    def __init__(self, barras, nombres):
        self._b, self._n = barras, nombres

    def barras(self, tickers, dias=280):
        return {t: self._b[t] for t in tickers if t in self._b}

    def metadata(self, tickers):
        return {t: Metadata(ticker=t, nombre=self._n[t], market_cap=100_000_000.0) for t in tickers if t in self._n}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


def test_main_misma_entrada_a_la_watchlist_con_y_sin_registro(monkeypatch, tmp_path):
    normal = _shortlist_de_main(monkeypatch, tmp_path / "a", "normal")
    assert [t for t, _, _ in normal[0]] == ["CHPT"]
    assert _shortlist_de_main(monkeypatch, tmp_path / "b", "sin_registro") == normal
    assert _shortlist_de_main(monkeypatch, tmp_path / "c", "registro_roto") == normal


def test_main_sigue_igual_si_escribir_falla(monkeypatch, tmp_path):
    from momentum_hunter.tests.test_run import _preparar_main_escaneo
    barras = {"CHPT": _barras("CHPT", precio=10.0, vol_prom=500_000.0)}
    _preparar_main_escaneo(monkeypatch, tmp_path, barras)
    monkeypatch.setattr(run_mod, "YahooNewsProvider", _News)
    # Un directorio donde va el archivo: la escritura falla sin lanzar.
    Path(nl.ARCHIVO).mkdir(parents=True)
    run_mod.main()


# ------------------------------------------------------------ escritura


def test_escribir_conserva_las_ultimas_n_y_reemplaza_un_archivo_corrupto(tmp_path):
    ruta = tmp_path / "nl.json"
    ruta.write_text("{corrupto", encoding="utf-8")
    for i in range(5):
        r = nl.Registro(datetime(2026, 9, 30, 14, i, tzinfo=UTC))
        assert nl.escribir(r, ruta=ruta, max_corridas=3)
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert [c["corrida_ts"][11:16] for c in datos["corridas"]] == ["14:04", "14:03", "14:02"]
    assert not (tmp_path / "nl.json.tmp").exists()


def test_escribir_nunca_lanza(tmp_path):
    (tmp_path / "dir").mkdir()
    assert nl.escribir(nl.Registro(datetime.now(UTC)), ruta=tmp_path / "dir") is False


# ------------------------------------------------------------ fronteras


def test_el_ejecutor_no_lee_el_registro():
    raiz = Path(__file__).resolve().parents[2] / "momentum_paper_trader"
    for path in raiz.rglob("*.py"):
        if "tests" in path.parts:
            continue
        assert "noticias_leidas" not in path.read_text(encoding="utf-8"), path


def test_el_registro_no_menciona_ejecucion_de_ordenes():
    fuente = inspect.getsource(nl).lower()
    for palabra in ("place_order", "buy_order", "sell_order", "alpaca", "ibapi", "interactive_brokers"):
        assert palabra not in fuente
