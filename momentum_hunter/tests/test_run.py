"""Pruebas del filtro de universo en dos bandas (small-cap de siempre +
large-cap complementaria, pedido 2026-08-07 tras el gap de 17% de ABNB)
-- sin red, con un `DataProvider` falso (mismo patrón que
`test_outcomes.py`)."""

from __future__ import annotations

from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Barras, Metadata
from momentum_hunter.run import _banda_de_universo, construir_candidatos_diarios

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
