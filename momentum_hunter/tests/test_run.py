"""Pruebas del filtro de universo en dos bandas (small-cap de siempre +
large-cap complementaria, pedido 2026-08-07 tras el gap de 17% de ABNB)
-- sin red, con un `DataProvider` falso (mismo patrón que
`test_outcomes.py`)."""

from __future__ import annotations

from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Barras, BarraIntradia, FactoresMomentum, Metadata
from momentum_hunter.run import (
    _banda_de_universo,
    _construir_candidato_intradia,
    construir_candidatos_diarios,
    construir_candidatos_intradia,
)
from momentum_hunter.scoring import Puntuacion

CFG = MomentumConfig()


def _barras(ticker: str, precio: float, vol_prom: float, n: int = 25) -> Barras:
    fechas = [str(1_700_000_000 + i * 86_400) for i in range(n)]
    closes = [precio] * n
    return Barras(ticker, fechas, closes, closes, closes, closes, [vol_prom] * n)


class _FakeProvider(DataProvider):
    def __init__(self, metadata: dict[str, Metadata]) -> None:
        self._metadata = metadata

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {t: self._metadata[t] for t in tickers if t in self._metadata}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


# ------------------------- _banda_de_universo -------------------------

def test_banda_small_con_precio_y_volumen_dentro_del_rango():
    b = _barras("PENNY", precio=5.0, vol_prom=500_000.0)
    assert _banda_de_universo(b, CFG) == "small"


def test_banda_none_si_volumen_insuficiente_en_rango_small():
    b = _barras("ILIQUIDO", precio=5.0, vol_prom=1_000.0)
    assert _banda_de_universo(b, CFG) is None


def test_banda_large_si_precio_por_encima_del_techo_small_y_liquido():
    b = _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)
    assert _banda_de_universo(b, CFG) == "large"


def test_banda_none_si_precio_alto_pero_iliquido():
    b = _barras("CARO_ILIQUIDO", precio=178.0, vol_prom=100.0)
    assert _banda_de_universo(b, CFG) is None


def test_banda_none_si_incluir_large_cap_apagado():
    cfg = MomentumConfig(incluir_large_cap=False)
    b = _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)
    assert _banda_de_universo(b, cfg) is None


def test_banda_none_sin_barras_o_precio_invalido():
    assert _banda_de_universo(Barras("X", [], [], [], [], [], []), CFG) is None


# ------------------------- construir_candidatos_diarios -------------------------

def test_large_cap_no_se_descarta_por_market_cap_max():
    barras = {"ABNB": _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)}
    meta = {"ABNB": Metadata(ticker="ABNB", market_cap=100_000_000_000.0)}  # muy por
    # encima de market_cap_max -- en small-cap esto se descartaría.
    provider = _FakeProvider(meta)
    candidatos = construir_candidatos_diarios(
        ["ABNB"], barras, provider, CFG, con_catalizadores=False, bandas={"ABNB": "large"},
    )
    assert len(candidatos) == 1
    assert candidatos[0].es_large_cap is True


def test_small_cap_se_sigue_descartando_por_market_cap_max():
    # Mismo market_cap alto, pero SIN banda "large" (comportamiento de
    # siempre) -- debe descartarse igual que antes de este cambio.
    barras = {"TST": _barras("TST", precio=5.0, vol_prom=500_000.0)}
    meta = {"TST": Metadata(ticker="TST", market_cap=100_000_000_000.0)}
    provider = _FakeProvider(meta)
    candidatos = construir_candidatos_diarios(
        ["TST"], barras, provider, CFG, con_catalizadores=False, bandas={"TST": "small"},
    )
    assert candidatos == []


def test_sin_bandas_se_comporta_como_small_cap_de_siempre():
    # Compatibilidad: llamar sin `bandas` (como antes de este cambio) no
    # debe darle un pase gratis a nada -- se sigue aplicando market_cap_max.
    barras = {"TST": _barras("TST", precio=5.0, vol_prom=500_000.0)}
    meta = {"TST": Metadata(ticker="TST", market_cap=100_000_000_000.0)}
    provider = _FakeProvider(meta)
    candidatos = construir_candidatos_diarios(["TST"], barras, provider, CFG, con_catalizadores=False)
    assert candidatos == []


# ------------------------- construir_candidatos_intradia -------------------------
# ("Fase 2", 2026-08-11): cubre el núcleo compartido `_construir_candidato_intradia`
# extraído de este bucle, y las garantías de robustez del docstring original
# ("un ticker que falle no tumba la corrida completa").

def _bi_regular(ticker: str, n: int = 3) -> BarraIntradia:
    marcas = [f"2026-08-11T14:{30 + i:02d}:00+00:00" for i in range(n)]
    closes = [5.20 + i * 0.01 for i in range(n)]
    return BarraIntradia(ticker, marcas, closes, closes, closes, closes, [5_000.0] * n)


def _barras_diarias(ticker: str, cierre_ayer: float = 4.80) -> Barras:
    fechas = [str(1_754_870_400 + i * 86_400) for i in range(5)]   # termina el 2026-08-11
    closes = [cierre_ayer] * 4 + [5.22]
    return Barras(ticker, fechas, closes, closes, closes, closes, [500_000.0] * 5)


def _candidato_diario_intradia(ticker: str) -> CandidatoDiario:
    return CandidatoDiario(
        ticker=ticker, nombre=None, precio=5.20, volumen_promedio=500_000.0,
        factores=FactoresMomentum(atr=0.30),
        catalizador=Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                                 fecha="2026-08-11T13:45:00+00:00"),
        meta=Metadata(ticker=ticker), puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
    )


class _FakeProviderIntradia(DataProvider):
    def __init__(self, barras: dict[str, BarraIntradia]) -> None:
        self._barras = barras

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {t: self._barras[t] for t in tickers if t in self._barras}


def test_construir_candidatos_intradia_devuelve_un_candidato_por_ticker_con_datos():
    shortlist = [_candidato_diario_intradia("RKLB")]
    barras_diarias = {"RKLB": _barras_diarias("RKLB")}
    provider = _FakeProviderIntradia({"RKLB": _bi_regular("RKLB")})
    candidatos = construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG)
    assert len(candidatos) == 1
    assert candidatos[0].ticker == "RKLB"


def test_construir_candidatos_intradia_ignora_ticker_sin_barras_del_proveedor():
    shortlist = [_candidato_diario_intradia("SINDATOS")]
    barras_diarias = {"SINDATOS": _barras_diarias("SINDATOS")}
    provider = _FakeProviderIntradia({})   # el proveedor no devolvió nada para este ticker
    assert construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG) == []


def test_construir_candidatos_intradia_ignora_ticker_sin_velas_de_hoy():
    shortlist = [_candidato_diario_intradia("VACIO")]
    barras_diarias = {"VACIO": _barras_diarias("VACIO")}
    bi_vacia = BarraIntradia("VACIO", [], [], [], [], [], [])
    provider = _FakeProviderIntradia({"VACIO": bi_vacia})
    assert construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG) == []


def test_construir_candidatos_intradia_un_ticker_roto_no_tumba_el_resto():
    # A "ROTO" le falta su entrada en `barras_diarias` -- el KeyError de
    # `_cierre_anterior` lo descarta (try/except del bucle), pero "OK" se
    # sigue evaluando con normalidad.
    shortlist = [_candidato_diario_intradia("ROTO"), _candidato_diario_intradia("OK")]
    barras_diarias = {"OK": _barras_diarias("OK")}
    provider = _FakeProviderIntradia({"ROTO": _bi_regular("ROTO"), "OK": _bi_regular("OK")})
    candidatos = construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG)
    assert [c.ticker for c in candidatos] == ["OK"]


def test_construir_candidato_intradia_none_sin_velas_de_hoy():
    bi_vacia = BarraIntradia("X", [], [], [], [], [], [])
    resultado = _construir_candidato_intradia(
        "X", None, None, Metadata(ticker="X"), False, 0.30, 88.0, None, bi_vacia, CFG)
    assert resultado is None


def test_construir_candidato_intradia_deriva_cierre_anterior_de_bi_sin_barras_diarias():
    # Bug real encontrado en revisión de PR (2026-08-11): sin esto, una
    # candidata descubierta ANTES de la apertura regular (sin gap
    # congelado todavía) se quedaba sin gap para siempre en el chequeo
    # liviano de la watchlist, que nunca pasa `cierre_anterior` (no pide
    # barras diarias). Ahora se deriva directo de las velas intradía.
    marcas = ["2026-08-10T13:30:00+00:00", "2026-08-10T19:59:00+00:00"] + [
        f"2026-08-11T14:{30 + i:02d}:00+00:00" for i in range(3)
    ]
    cierres_ayer = [4.00, 4.00]
    cierres_hoy = [4.60, 4.65, 4.70]   # gap real: (4.60 - 4.00) / 4.00 = 15%
    closes = cierres_ayer + cierres_hoy
    bi = BarraIntradia("RKLB", marcas, closes, closes, closes, closes, [5_000.0] * len(marcas))

    candidato = _construir_candidato_intradia(
        "RKLB", None, None, Metadata(ticker="RKLB"), False, 0.30, 88.0,
        None, bi, CFG,   # cierre_anterior=None, sin gap_pct_fallback tampoco
    )
    assert candidato is not None
    assert candidato.factores.gap_pct is not None
    assert abs(candidato.factores.gap_pct - 0.15) < 1e-9


def test_construir_candidato_intradia_usa_el_gap_congelado_si_no_hay_cierre_anterior():
    # Sin `cierre_anterior` (None, como en el chequeo liviano de la
    # watchlist) `fi.calcular` no puede derivar el gap solo -- debe caer
    # al `gap_pct_fallback` congelado en vez de perderlo.
    bi = _bi_regular("RKLB")
    candidato = _construir_candidato_intradia(
        "RKLB", None, None, Metadata(ticker="RKLB"), False, 0.30, 88.0, None, bi, CFG,
        gap_pct_fallback=0.10,
    )
    assert candidato is not None
    assert candidato.factores.gap_pct == 0.10
