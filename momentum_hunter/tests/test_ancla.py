"""Filtro de ancla ticker/nombre -- los FP reales del corpus y los
casos que tienen que seguir pasando. No toca umbrales."""

from __future__ import annotations

from datetime import date

from momentum_hunter import run as run_mod
from momentum_hunter import telemetria
from momentum_hunter.catalysts.ancla import (
    ALIASES,
    ANCLA_TABLAS_VERSION,
    GENERIC_TOKENS,
    ancla_ok,
    match_token,
    name_tokens,
)
from momentum_hunter.catalysts.detector import Titular
from momentum_hunter.config import CONFIG, MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Metadata
from momentum_hunter.run import construir_candidatos_diarios
from momentum_hunter.tests.test_run import CFG, _barras


# Titulares reales (o el recorte que el diseño usa como ejemplo).
_GME_DIRECTOR = (
    "GameStop Rises 4% as Collectibles Sales Jump 57% and a Director Buys $1M"
)
_MACYS_GUIDANCE = (
    "Macy's Raises Full-Year Outlook Following Fiscal Second-Quarter Beat"
)
_ADI_ALIF = "Analog Devices to Acquire Alif Semiconductor for $1.35 Billion"
_KR_DIGEST = (
    "Top Midday Stories: Rate-Hike Expectations Rise After August CPI "
    "Release; Oracle Shares Up Modestly After Strong Earnings Results"
)
_MRVL_Q2 = "MRVL Stock Rises 8.6% Since Q2 Results: Time to Hold or Fold?"
_ABT_FDA = "Abbott’s dual ablation catheter lands FDA approval"
_CHPT_Q2 = "ChargePoint (CHPT) Posts Strong Q2 Results, But Q3 Outlook Raises Concerns"
_NTLA_FDA = (
    "Intellia Therapeutics Shares Rise as FDA Grants Priority Review to Lonvo-z"
)
_ACQUISITION = "Company announces acquisition of a smaller rival"


def test_tablas_van_versionadas_y_mstr_no_lleva_strategy():
    # "strategy" ancla cualquier plan de negocio. MSTR solo acepta
    # microstrategy -- el nombre legal nuevo no se usa como alias.
    assert ANCLA_TABLAS_VERSION == "v1"
    assert "strategy" in GENERIC_TOKENS
    assert "strategy" not in ALIASES["MSTR"]
    assert "microstrategy" in ALIASES["MSTR"]
    assert "semiconductor" in GENERIC_TOKENS


# ------------------------- FP: titulares ajenos -------------------------

def test_ebay_no_ancla_director_buys_de_gamestop():
    ok, motivo = ancla_ok("EBAY", "eBay Inc.", _GME_DIRECTOR)
    assert ok is False
    assert motivo == "sin_ancla"


def test_tgt_no_ancla_guidance_de_macys():
    ok, motivo = ancla_ok("TGT", "Target Corporation", _MACYS_GUIDANCE)
    assert ok is False
    assert motivo == "sin_ancla"


def test_on_no_ancla_el_deal_de_adi_alif():
    # El caso que obliga a GENERIC["semiconductor"]: sin eso, el nombre
    # legal de ON ancla un titular que es de ADI.
    ok, motivo = ancla_ok("ON", "ON Semiconductor Corporation", _ADI_ALIF)
    assert ok is False
    assert motivo == "sin_ancla"


def test_kr_no_ancla_digest_sin_kroger():
    ok, motivo = ancla_ok("KR", "The Kroger Co.", _KR_DIGEST)
    assert ok is False
    assert motivo == "sin_ancla"


def test_alab_no_ancla_titular_de_mrvl():
    ok, motivo = ancla_ok("ALAB", "Astera Labs, Inc.", _MRVL_Q2)
    assert ok is False
    assert motivo == "sin_ancla"


# ------------------------- on-topic -------------------------

def test_adi_pasa_por_alias_o_nombre():
    ok, motivo = ancla_ok("ADI", "Analog Devices, Inc.", _ADI_ALIF)
    assert ok is True
    assert motivo in {"alias", "nombre"}


def test_abt_pasa_por_abbott_aunque_el_ticker_no_este():
    ok, motivo = ancla_ok("ABT", "Abbott Laboratories", _ABT_FDA)
    assert ok is True
    assert motivo in {"alias", "nombre"}


def test_chpt_pasa_porque_el_ticker_esta_en_el_titular():
    ok, motivo = ancla_ok("CHPT", "ChargePoint Holdings, Inc.", _CHPT_Q2)
    assert ok is True
    assert motivo == "ticker"


def test_ntla_pasa_por_el_nombre():
    ok, motivo = ancla_ok("NTLA", "Intellia Therapeutics, Inc.", _NTLA_FDA)
    assert ok is True
    assert motivo in {"alias", "nombre"}


def test_mstr_pasa_por_ticker_sin_usar_strategy_como_alias():
    titular = (
        "MSTR Stock Slips After Strategy Leaves Bitcoin Stash Untouched, "
        "Doubles Buyback Program To $2B"
    )
    ok, motivo = ancla_ok("MSTR", "Strategy Inc", titular)
    assert ok is True
    assert motivo == "ticker"


def test_sei_pasa_por_alias_solaris():
    titular = "A Solaris Energy Director Buys Nearly 8,000 Company Shares Worth Over $500,000"
    ok, motivo = ancla_ok("SEI", "Solaris Energy Infrastructure, Inc.", titular)
    assert ok is True
    assert motivo in {"alias", "nombre"}


# ------------------------- frontera de ticker corto -------------------------

def test_on_no_matchea_dentro_de_acquisition():
    assert match_token("ON", _ACQUISITION) is False
    ok, motivo = ancla_ok("ON", "ON Semiconductor Corporation", _ACQUISITION)
    assert ok is False
    assert motivo == "sin_ancla"


def test_ticker_corto_si_va_como_palabra():
    assert match_token("ON", "ON reports quarterly results") is True
    ok, _ = ancla_ok("ON", None, "ON reports quarterly results")
    assert ok is True


def test_ticker_largo_puede_ser_substring():
    assert match_token("CHPT", "ChargePoint (CHPT) Posts Strong Q2 Results") is True


# ------------------------- titular vacío -------------------------

def test_titular_vacio_bloquea():
    for vacio in (None, "", "   "):
        ok, motivo = ancla_ok("NTLA", "Intellia Therapeutics, Inc.", vacio)
        assert ok is False, vacio
        assert motivo == "sin_titular"


# ------------------------- name_tokens -------------------------

def test_name_tokens_tira_sufijos_y_genericos():
    assert name_tokens("eBay Inc.") == ["ebay"]
    assert name_tokens("The Kroger Co.") == ["kroger"]
    assert name_tokens("Target Corporation") == ["target"]
    assert name_tokens("Astera Labs, Inc.") == ["astera"]
    assert name_tokens("Intellia Therapeutics, Inc.") == ["intellia"]
    assert name_tokens("ChargePoint Holdings, Inc.") == ["chargepoint"]
    # ON y MSTR no tienen token de identidad una vez tirado lo genérico.
    assert name_tokens("ON Semiconductor Corporation") == []
    assert name_tokens("Strategy Inc") == []


def test_name_tokens_devuelve_bigram_si_hay_dos_utiles():
    # "devices" es GENERIC -- Analog Devices no sirve como ejemplo de
    # bigrama. Dos palabras distintivas sí: primero + "primero segundo".
    assert name_tokens("Analog Devices, Inc.") == ["analog"]
    assert name_tokens("Bending Spoons S.p.A.") == ["bending", "bending spoons"]


def test_name_tokens_none_o_vacio():
    assert name_tokens(None) == []
    assert name_tokens("   ") == []


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


class _NewsFijo:
    def __init__(self, texto: str, metricas=None) -> None:
        self._texto = texto

    def titulares(self, ticker):
        return [Titular(self._texto, "Yahoo", date.today().isoformat())]


def test_hook_bloquea_catalizador_ajeno_y_cuenta_ancla_bloqueados(monkeypatch):
    monkeypatch.setattr(
        run_mod, "YahooNewsProvider", lambda metricas=None: _NewsFijo(_GME_DIRECTOR))
    barras = {"EBAY": _barras("EBAY", precio=15.0, vol_prom=500_000.0)}
    meta = {"EBAY": Metadata(ticker="EBAY", nombre="eBay Inc.", market_cap=100_000_000.0)}
    metricas = telemetria.Metricas()
    candidatos = construir_candidatos_diarios(
        ["EBAY"], barras, _FakeProvider(meta), CFG, con_catalizadores=True,
        bandas={"EBAY": "small"}, metricas=metricas,
    )
    assert len(candidatos) == 1
    assert candidatos[0].catalizador is None
    assert metricas.ancla_bloqueados["sin_ancla"] == 1
    assert sum(metricas.con_catalizador.values()) == 0
    assert metricas.como_dict()["embudo"]["ancla_bloqueados"] == {"sin_ancla": 1}


def test_hook_deja_pasar_titular_con_ancla(monkeypatch):
    monkeypatch.setattr(
        run_mod, "YahooNewsProvider", lambda metricas=None: _NewsFijo(_CHPT_Q2))
    barras = {"CHPT": _barras("CHPT", precio=12.0, vol_prom=500_000.0)}
    meta = {
        "CHPT": Metadata(
            ticker="CHPT", nombre="ChargePoint Holdings, Inc.", market_cap=100_000_000.0),
    }
    metricas = telemetria.Metricas()
    candidatos = construir_candidatos_diarios(
        ["CHPT"], barras, _FakeProvider(meta), CFG, con_catalizadores=True,
        bandas={"CHPT": "small"}, metricas=metricas,
    )
    assert candidatos[0].catalizador is not None
    assert candidatos[0].catalizador.titular == _CHPT_Q2
    assert sum(metricas.ancla_bloqueados.values()) == 0
    assert metricas.con_catalizador["small"] == 1


def test_este_cambio_no_toca_umbrales_de_score_ni_paper():
    # Cinturón: el filtro es aditivo. Si alguien "aprovecha" el PR para
    # mover IA/ATR/riesgo, esta prueba lo delata.
    assert CONFIG.score_minimo_alerta == 55.0
    assert CONFIG.riesgo_recompensa_minimo == 1.5
    assert MomentumConfig().score_minimo_alerta == 55.0
    from momentum_paper_trader.config import CONFIG as PAPER
    assert PAPER.riesgo_dolares_por_operacion == 100.0
    ia = (__import__("pathlib").Path(__file__).resolve().parents[2]
          / "momentum_paper_trader" / "ia_decision.py").read_text()
    assert "confianza < 7" in ia
