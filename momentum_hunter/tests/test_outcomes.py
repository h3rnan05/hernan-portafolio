"""Pruebas de actualización de resultados -- usa un `DataProvider` falso
(sin red) con barras diarias fabricadas para poder calcular a mano el
retorno esperado en cada horizonte."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Barras, Metadata
from momentum_hunter.outcomes import actualizar_resultados
from momentum_hunter.tracker import AlertaRegistrada

FECHA_INICIO = date(2026, 6, 20)
IDX_ENTRADA = 6          # bar[IDX_ENTRADA] cae exactamente en la fecha de la alerta
N_DESPUES = 15           # más que el horizonte más largo (10), con margen


def _epoch(d: date) -> str:
    return str(int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp()))


def _barras_fabricadas() -> Barras:
    n = IDX_ENTRADA + N_DESPUES
    fechas = [_epoch(FECHA_INICIO + timedelta(days=i)) for i in range(n)]
    # Antes de la entrada el precio es irrelevante (9.0 plano). Desde
    # IDX_ENTRADA, close[IDX_ENTRADA + h] = 10.0 + h -- así el retorno a
    # `h` días es exactamente h/10 sobre una entrada de 10.0.
    closes = [9.0] * IDX_ENTRADA + [10.0 + h for h in range(N_DESPUES)]
    highs = [c + 0.05 for c in closes]
    lows = [c - 0.05 for c in closes]
    highs[IDX_ENTRADA + 2] = 50.0   # pico favorable dentro de la ventana
    lows[IDX_ENTRADA + 1] = 2.0     # mínimo adverso dentro de la ventana
    vols = [1_000_000.0] * n
    opens = closes
    return Barras("ACME", fechas, opens, closes, highs, lows, vols)


class _FakeProvider(DataProvider):
    def __init__(self, barras: dict[str, Barras]) -> None:
        self._barras = barras

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        return {t: self._barras[t] for t in tickers if t in self._barras}

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        return {t: Metadata(t) for t in tickers}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


def _alerta() -> AlertaRegistrada:
    fecha_alerta = FECHA_INICIO + timedelta(days=IDX_ENTRADA)
    return AlertaRegistrada(
        id="a1", ticker="ACME", fecha=f"{fecha_alerta.isoformat()}T12:00:00+00:00",
        precio_entrada=10.0, stop=9.0, objetivo1=11.0, objetivo2=12.0,
        clasificacion="🔥 BREAKOUT", estrategia="Long Call", score=90.0,
    )


def test_actualizar_resultados_calcula_retorno_por_horizonte():
    cfg = MomentumConfig()
    alertas = [_alerta()]
    provider = _FakeProvider({"ACME": _barras_fabricadas()})

    actualizar_resultados(alertas, provider, cfg)

    a = alertas[0]
    assert a.resultados_pct["1d"] == 0.1
    assert a.resultados_pct["3d"] == 0.3
    assert a.resultados_pct["5d"] == 0.5
    assert a.resultados_pct["10d"] == 1.0
    assert a.resuelta is True


def test_actualizar_resultados_registra_excursion_favorable_y_adversa():
    cfg = MomentumConfig()
    alertas = [_alerta()]
    provider = _FakeProvider({"ACME": _barras_fabricadas()})

    actualizar_resultados(alertas, provider, cfg)

    a = alertas[0]
    assert a.precio_maximo_pct == (50.0 - 10.0) / 10.0
    assert a.precio_minimo_pct == (2.0 - 10.0) / 10.0


def test_actualizar_resultados_no_toca_alertas_ya_resueltas():
    cfg = MomentumConfig()
    a = _alerta()
    a.resultados_pct = {"1d": 0.99, "3d": 0.99, "5d": 0.99, "10d": 0.99}
    a.resuelta = True
    provider = _FakeProvider({"ACME": _barras_fabricadas()})

    actualizar_resultados([a], provider, cfg)

    assert a.resultados_pct["1d"] == 0.99  # no se recalculó


def test_actualizar_resultados_ticker_sin_barras_no_falla():
    cfg = MomentumConfig()
    alertas = [_alerta()]
    provider = _FakeProvider({})  # ACME no está disponible
    actualizar_resultados(alertas, provider, cfg)  # no debe lanzar
    assert alertas[0].resuelta is False
