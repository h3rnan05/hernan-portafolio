"""Telemetría de rechazos en la etapa KEYWORD -- observación, no decide.

Cubre el hueco del lunes 2026-09-14 (424 titulares → 0 catalizadores,
sin texto persistido). El filtro de ancla (#118) es post-keyword y
estas pruebas clavan que NO se mezcla con estos contadores."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from momentum_hunter import run as run_mod
from momentum_hunter import telemetria
from momentum_hunter.catalysts.ancla import ANCLA_TABLAS_VERSION
from momentum_hunter.catalysts.detector import (
    CATALYST_KEYWORDS,
    Titular,
    detectar_catalizador,
)
from momentum_hunter.catalysts.keyword_rechazos import (
    MOTIVO_FUERA_VENTANA,
    MOTIVO_RUMOR_SIN_FUENTES,
    MOTIVO_SIN_KEYWORD,
    explicar_rechazos_keyword,
)
from momentum_hunter.config import CONFIG, MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Metadata
from momentum_hunter.run import construir_candidatos_diarios
from momentum_hunter.tests.test_run import CFG, _barras

HOY = date(2026, 9, 14)


def _t(texto: str, fuente: str = "Reuters", fecha: str | None = None) -> Titular:
    return Titular(texto, fuente, fecha)


# ------------------------- explicar (mismo predicado, otro output) -------------------------

def test_sin_titulares_no_inventa_rechazos():
    assert explicar_rechazos_keyword("TST", [], CONFIG, hoy=HOY) == []
    assert explicar_rechazos_keyword("", [_t("x")], CONFIG, hoy=HOY) == []


def test_titular_sin_keyword_queda_muestreable():
    titulares = [_t("Company opens new office downtown")]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is None
    rechazos = explicar_rechazos_keyword("TST", titulares, CONFIG, hoy=HOY)
    assert len(rechazos) == 1
    assert rechazos[0]["ticker"] == "TST"
    assert rechazos[0]["titular"] == "Company opens new office downtown"
    assert rechazos[0]["motivo"] == MOTIVO_SIN_KEYWORD
    assert "nota" not in rechazos[0]


def test_sin_keyword_no_anota_almost_miss():
    # Shadow-CF / frases almost-miss van en otro PR. "FDA meeting"
    # no es keyword completa: acá solo se cuenta como sin_keyword,
    # sin nota que sugiera un match.
    titulares = [_t("Company schedules FDA meeting with investors")]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is None
    rechazos = explicar_rechazos_keyword("TST", titulares, CONFIG, hoy=HOY)
    assert rechazos[0]["motivo"] == MOTIVO_SIN_KEYWORD
    assert "nota" not in rechazos[0]


def test_titular_fuera_de_ventana():
    vieja = (HOY - timedelta(days=CONFIG.dias_ventana_catalizador + 5)).isoformat()
    titulares = [_t("Company Awarded Contract by US Government", fecha=vieja)]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is None
    rechazos = explicar_rechazos_keyword("TST", titulares, CONFIG, hoy=HOY)
    assert [r["motivo"] for r in rechazos] == [MOTIVO_FUERA_VENTANA]
    assert "ventana" in rechazos[0].get("nota", "")


def test_rumor_con_una_sola_fuente():
    titulares = [_t("Company is said to be exploring a sale", "BlogX")]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is None
    rechazos = explicar_rechazos_keyword("TST", titulares, CONFIG, hoy=HOY)
    assert [r["motivo"] for r in rechazos] == [MOTIVO_RUMOR_SIN_FUENTES]
    assert "fuentes=1" in rechazos[0]["nota"]
    assert "minimo=2" in rechazos[0]["nota"]


def test_catalizador_confirmado_no_aparece_como_rechazo():
    titulares = [_t("Company Awarded Contract by US Government")]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is not None
    assert explicar_rechazos_keyword("TST", titulares, CONFIG, hoy=HOY) == []


def test_rumor_confirmado_con_dos_fuentes_no_es_rechazo():
    titulares = [
        _t("Company is said to be exploring a sale", "BlogX"),
        _t("Sources say company exploring strategic options", "Reuters"),
    ]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is not None
    assert explicar_rechazos_keyword("TST", titulares, CONFIG, hoy=HOY) == []


def test_explicar_no_cambia_lo_que_devuelve_el_detector():
    # Cinturón: recorrer los predicados para observar no puede
    # reclasificar. Mismos inputs, mismo None / mismo tipo.
    casos = [
        [_t("Company opens new office downtown")],
        [_t("Company is said to be exploring a sale", "BlogX")],
        [_t("Company Awarded Contract by US Government")],
    ]
    for titulares in casos:
        antes = detectar_catalizador(titulares, CONFIG, hoy=HOY)
        explicar_rechazos_keyword("TST", titulares, CONFIG, hoy=HOY)
        despues = detectar_catalizador(titulares, CONFIG, hoy=HOY)
        if antes is None:
            assert despues is None
        else:
            assert despues is not None
            assert despues.tipo == antes.tipo
            assert despues.titular == antes.titular


# ------------------------- tope de muestra -------------------------

def test_cuentas_completas_muestra_acotada_por_motivo():
    m = telemetria.Metricas()
    tope = telemetria.KEYWORD_RECHAZOS_MUESTRA_POR_MOTIVO
    for i in range(tope + 5):
        m.registrar_keyword_rechazo(f"T{i:03d}", f"titular {i}", MOTIVO_SIN_KEYWORD)
    assert m.keyword_rechazos[MOTIVO_SIN_KEYWORD] == tope + 5
    assert len(m.keyword_rechazos_muestra) == tope


def test_muestra_acotada_por_ticker_sin_perder_el_conteo():
    m = telemetria.Metricas()
    for i in range(6):
        m.registrar_keyword_rechazo("AAA", f"titular {i}", MOTIVO_SIN_KEYWORD)
    assert m.keyword_rechazos[MOTIVO_SIN_KEYWORD] == 6
    assert len(m.keyword_rechazos_muestra) == telemetria.KEYWORD_RECHAZOS_MUESTRA_POR_TICKER
    assert {s["ticker"] for s in m.keyword_rechazos_muestra} == {"AAA"}


def test_muestra_reserva_cupo_por_motivo():
    # Si solo hubiera un tope global, 12 sin_keyword taparían un rumor.
    m = telemetria.Metricas()
    for i in range(telemetria.KEYWORD_RECHAZOS_MUESTRA_POR_MOTIVO):
        m.registrar_keyword_rechazo(f"S{i:03d}", "office downtown", MOTIVO_SIN_KEYWORD)
    m.registrar_keyword_rechazo("RUM", "is said to be exploring", MOTIVO_RUMOR_SIN_FUENTES)
    motivos = {s["motivo"] for s in m.keyword_rechazos_muestra}
    assert MOTIVO_SIN_KEYWORD in motivos
    assert MOTIVO_RUMOR_SIN_FUENTES in motivos


def test_titular_largo_se_trunca_en_la_muestra():
    m = telemetria.Metricas()
    largo = "x" * (telemetria.TITULAR_MUESTRA_MAX_CHARS + 80)
    m.registrar_keyword_rechazo("TST", largo, MOTIVO_SIN_KEYWORD)
    assert len(m.keyword_rechazos_muestra[0]["titular"]) == telemetria.TITULAR_MUESTRA_MAX_CHARS


def test_como_dict_incluye_keyword_rechazos_y_no_el_contador_interno():
    m = telemetria.Metricas()
    m.registrar_keyword_rechazo("TST", "office downtown", MOTIVO_SIN_KEYWORD)
    embudo = m.como_dict()["embudo"]
    assert embudo["keyword_rechazos"] == {MOTIVO_SIN_KEYWORD: 1}
    assert embudo["keyword_rechazos_muestra"] == [
        {"ticker": "TST", "titular": "office downtown", "motivo": MOTIVO_SIN_KEYWORD},
    ]
    dumped = json.dumps(m.como_dict())
    assert "_keyword_muestra_por_ticker" not in dumped


def test_jsonl_persiste_contadores_y_muestra(tmp_path):
    m = telemetria.Metricas()
    m.registrar_keyword_rechazo("TST", "office downtown", MOTIVO_SIN_KEYWORD)
    ahora = datetime(2026, 9, 14, 14, 0, tzinfo=UTC)
    path = telemetria.registrar_corrida(m, tmp_path, ahora, fuente="gha")
    assert path is not None
    linea = json.loads(path.read_text().splitlines()[0])
    assert linea["embudo"]["keyword_rechazos"] == {MOTIVO_SIN_KEYWORD: 1}
    assert linea["embudo"]["keyword_rechazos_muestra"][0]["ticker"] == "TST"


# ------------------------- hook en construir_candidatos_diarios -------------------------

class _FakeProvider(DataProvider):
    def __init__(self, metadata: dict[str, Metadata]) -> None:
        self._metadata = metadata

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {t: self._metadata[t] for t in tickers if t in self._metadata}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


class _NewsLista:
    def __init__(self, titulares, metricas=None) -> None:
        self._titulares = titulares

    def titulares(self, ticker):
        return list(self._titulares)


def _meta(ticker: str, nombre: str) -> dict[str, Metadata]:
    return {ticker: Metadata(ticker=ticker, nombre=nombre, market_cap=100_000_000.0)}


def test_hook_persiste_rechazo_sin_keyword_y_no_toca_ancla(monkeypatch):
    titulares = [_t("Company opens new office downtown")]
    monkeypatch.setattr(
        run_mod, "YahooNewsProvider", lambda metricas=None: _NewsLista(titulares))
    metricas = telemetria.Metricas()
    candidatos = construir_candidatos_diarios(
        ["TST"], {"TST": _barras("TST", precio=5.0, vol_prom=500_000.0)},
        _FakeProvider(_meta("TST", "Test Co")), CFG, con_catalizadores=True,
        bandas={"TST": "small"}, metricas=metricas,
    )
    assert candidatos[0].catalizador is None
    assert metricas.keyword_rechazos[MOTIVO_SIN_KEYWORD] == 1
    assert metricas.keyword_rechazos_muestra[0]["titular"] == titulares[0].texto
    assert sum(metricas.ancla_bloqueados.values()) == 0
    assert sum(metricas.con_catalizador.values()) == 0


def test_hook_ancla_bloqueado_no_se_cuenta_como_keyword(monkeypatch):
    # #118: keyword sí matcheó, el ancla lo tira. Mezclar eso con
    # keyword_rechazos es exactamente el hueco que NO hay que abrir.
    gme = "GameStop Rises 4% as Collectibles Sales Jump 57% and a Director Buys $1M"
    monkeypatch.setattr(
        run_mod, "YahooNewsProvider",
        lambda metricas=None: _NewsLista([_t(gme)]),
    )
    metricas = telemetria.Metricas()
    candidatos = construir_candidatos_diarios(
        ["EBAY"], {"EBAY": _barras("EBAY", precio=15.0, vol_prom=500_000.0)},
        _FakeProvider(_meta("EBAY", "eBay Inc.")), CFG, con_catalizadores=True,
        bandas={"EBAY": "small"}, metricas=metricas,
    )
    assert candidatos[0].catalizador is None
    assert metricas.ancla_bloqueados["sin_ancla"] == 1
    assert dict(metricas.keyword_rechazos) == {}
    assert metricas.keyword_rechazos_muestra == []


def test_hook_catalizador_con_ancla_no_registra_keyword_rechazos(monkeypatch):
    chpt = "ChargePoint (CHPT) Posts Strong Q2 Results, But Q3 Outlook Raises Concerns"
    monkeypatch.setattr(
        run_mod, "YahooNewsProvider",
        lambda metricas=None: _NewsLista([_t(chpt)]),
    )
    metricas = telemetria.Metricas()
    candidatos = construir_candidatos_diarios(
        ["CHPT"], {"CHPT": _barras("CHPT", precio=12.0, vol_prom=500_000.0)},
        _FakeProvider(_meta("CHPT", "ChargePoint Holdings, Inc.")), CFG,
        con_catalizadores=True, bandas={"CHPT": "small"}, metricas=metricas,
    )
    assert candidatos[0].catalizador is not None
    assert dict(metricas.keyword_rechazos) == {}
    assert metricas.con_catalizador["small"] == 1


def test_hook_sin_noticias_no_registra_keyword_rechazos(monkeypatch):
    monkeypatch.setattr(
        run_mod, "YahooNewsProvider", lambda metricas=None: _NewsLista([]))
    metricas = telemetria.Metricas()
    construir_candidatos_diarios(
        ["TST"], {"TST": _barras("TST", precio=5.0, vol_prom=500_000.0)},
        _FakeProvider(_meta("TST", "Test Co")), CFG, con_catalizadores=True,
        bandas={"TST": "small"}, metricas=metricas,
    )
    assert dict(metricas.keyword_rechazos) == {}
    assert metricas.titulares_total == 0


def test_fallo_al_explicar_no_tumba_el_ticker(monkeypatch):
    monkeypatch.setattr(
        run_mod, "YahooNewsProvider",
        lambda metricas=None: _NewsLista([_t("office downtown")]),
    )
    monkeypatch.setattr(
        run_mod, "explicar_rechazos_keyword",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    metricas = telemetria.Metricas()
    candidatos = construir_candidatos_diarios(
        ["TST"], {"TST": _barras("TST", precio=5.0, vol_prom=500_000.0)},
        _FakeProvider(_meta("TST", "Test Co")), CFG, con_catalizadores=True,
        bandas={"TST": "small"}, metricas=metricas,
    )
    assert len(candidatos) == 1
    assert candidatos[0].catalizador is None
    assert metricas.errores["keyword_rechazos:RuntimeError"] == 1


def test_este_cambio_no_toca_keywords_ancla_ni_umbrales():
    assert CONFIG.dias_ventana_catalizador == 3
    assert CONFIG.fuentes_minimas_rumor == 2
    assert CONFIG.score_minimo_alerta == 55.0
    assert ANCLA_TABLAS_VERSION == "v1"
    # Candado de la lista viva. El parche 2026-09-22 solo agrega las
    # frases de ALKS (phase 1b / phase 1 / proof-of-concept) y LFMD
    # (partnership / collaboration). Ancla, ventana y riesgo paper no se mueven.
    assert CATALYST_KEYWORDS["fda"] == (
        "fda approval", "fda clearance", "fda grants", "breakthrough therapy",
        "phase 3 results", "phase 2 results", "clinical trial results", "fda approves",
        "phase 1b", "phase 1", "proof-of-concept", "proof of concept",
    )
    assert CATALYST_KEYWORDS["nuevo_cliente"] == (
        "signs agreement with", "partnership with", "strategic partnership",
        "new customer", "expands partnership",
        "partnership", "collaboration",
    )
    assert "strategy" not in {
        alias for aliases in
        __import__("momentum_hunter.catalysts.ancla", fromlist=["ALIASES"]).ALIASES.get("MSTR", ())
        for alias in aliases
    }
    from momentum_paper_trader.config import CONFIG as PAPER
    assert PAPER.riesgo_dolares_por_operacion == 100.0


def test_observacion_no_importa_keywords_ni_anota_almost_miss():
    import inspect
    from momentum_hunter.catalysts import keyword_rechazos
    src = inspect.getsource(keyword_rechazos)
    assert "import CATALYST_KEYWORDS" not in src
    assert "_nota_casi" not in src
    assert "casi:" not in src
