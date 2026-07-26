"""Factores de momentum, calculados desde barras diarias. Sin
pandas/numpy (mismo principio que `screener/factors/technical.py`: corre
ligero en CI). Cada función devuelve un número crudo; scoring.py decide
cómo combinarlos.

`vwap_proxy` es explícitamente una APROXIMACIÓN: el VWAP real se calcula
sobre ticks intradía (que este proyecto no descarga -- ver
`momentum_hunter/README.md`), no sobre barras diarias. Aquí se aproxima
como el precio típico (H+L+C)/3 ponderado por volumen de los últimos
días -- útil como referencia de "a qué precio promedio se ha negociado
recientemente", pero NO intercambiable con el VWAP intradía real que
usaría un day trader en vivo."""

from __future__ import annotations

from momentum_hunter.models import Barras, FactoresMomentum


def sma(valores: list[float], n: int) -> float | None:
    return sum(valores[-n:]) / n if len(valores) >= n else None


def ema_serie(valores: list[float], n: int) -> list[float] | None:
    """Serie completa de EMA (necesaria para MACD, que es la diferencia
    entre dos EMAs evaluadas en el mismo punto del tiempo). Semilla: SMA
    de las primeras `n` barras, como hace la convención estándar."""
    if len(valores) < n:
        return None
    k = 2.0 / (n + 1)
    ema = [sum(valores[:n]) / n]
    for v in valores[n:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def ema(valores: list[float], n: int) -> float | None:
    serie = ema_serie(valores, n)
    return serie[-1] if serie else None


def gap_pct(b: Barras) -> float | None:
    """Gap de apertura de hoy vs. cierre de ayer -- el disparador clásico
    de momentum intradía."""
    if len(b) < 2 or b.close[-2] == 0:
        return None
    return (b.open[-1] - b.close[-2]) / b.close[-2]


def rvol(b: Barras, ventana: int = 20) -> float | None:
    """Relative volume: volumen de hoy / promedio de los `ventana` días
    anteriores (excluyendo hoy)."""
    if len(b.volume) < ventana + 1:
        return None
    anteriores = b.volume[-ventana - 1:-1]
    promedio = sum(anteriores) / len(anteriores)
    return b.volume[-1] / promedio if promedio > 0 else None


def maximo_52s(b: Barras) -> float | None:
    """Máximo de las últimas ~52 semanas (252 barras) usando `high`, no
    `close` -- el nivel real que un breakout tiene que superar."""
    ventana = b.high[-252:] if len(b.high) >= 252 else b.high
    return max(ventana) if ventana else None


def distancia_maximo_52s(b: Barras) -> float | None:
    """Qué tan cerca del máximo de 52 semanas está el cierre de hoy
    (1.0 = en máximos o por encima)."""
    maximo = maximo_52s(b)
    return b.close[-1] / maximo if maximo else None


def breakout_nd(b: Barras, n: int = 20) -> bool:
    """Ruptura de un máximo de `n` sesiones en base de cierre -- cierre de
    hoy por encima del máximo de cierre de las `n` sesiones ANTERIORES
    (excluye hoy, si no cualquier día que hace nuevo máximo calificaría
    trivialmente)."""
    if len(b.close) < n + 1:
        return False
    return b.close[-1] > max(b.close[-n - 1:-1])


def vwap_proxy(b: Barras, ventana: int = 10) -> float | None:
    """Ver docstring del módulo: aproximación con barras diarias, no VWAP
    intradía real."""
    if len(b) < ventana:
        return None
    tipicos = [(b.high[i] + b.low[i] + b.close[i]) / 3 for i in range(-ventana, 0)]
    vols = b.volume[-ventana:]
    vol_total = sum(vols)
    if vol_total <= 0:
        return None
    return sum(t * v for t, v in zip(tipicos, vols, strict=True)) / vol_total


def atr(b: Barras, periodo: int = 14) -> float | None:
    """Average True Range, periodo estándar de day-trading (14, distinto
    del periodo 20 que usa el screener del S&P 500 -- son proyectos
    independientes con convenciones propias)."""
    if len(b.close) < 2:
        return None
    trs = [
        max(b.high[i] - b.low[i], abs(b.high[i] - b.close[i - 1]), abs(b.low[i] - b.close[i - 1]))
        for i in range(1, len(b.close))
    ]
    ventana = trs[-periodo:]
    return sum(ventana) / len(ventana) if ventana else None


def rsi(b: Barras, periodo: int = 14) -> float | None:
    c = b.close
    if len(c) <= periodo:
        return None
    ganancias, perdidas = [], []
    for i in range(-periodo, 0):
        d = c[i] - c[i - 1]
        (ganancias if d > 0 else perdidas).append(abs(d))
    ag = sum(ganancias) / periodo
    ap = sum(perdidas) / periodo
    if ap == 0:
        return 100.0
    rs = ag / ap
    return 100.0 - 100.0 / (1.0 + rs)


def macd(
    b: Barras, rapida: int = 12, lenta: int = 26, señal: int = 9,
) -> tuple[float, float, float] | None:
    """(línea MACD, línea de señal, histograma). None si no hay
    suficiente historia para las tres EMAs involucradas."""
    ema_rapida = ema_serie(b.close, rapida)
    ema_lenta = ema_serie(b.close, lenta)
    if ema_rapida is None or ema_lenta is None:
        return None
    # Alinear ambas series al mismo punto final (empiezan en índices
    # distintos porque sus semillas usan ventanas distintas).
    n = min(len(ema_rapida), len(ema_lenta))
    linea_macd = [ema_rapida[-n + i] - ema_lenta[-n + i] for i in range(n)]
    serie_señal = ema_serie(linea_macd, señal)
    if serie_señal is None:
        return None
    macd_val = linea_macd[-1]
    señal_val = serie_señal[-1]
    return macd_val, señal_val, macd_val - señal_val


def calcular(b: Barras) -> FactoresMomentum:
    """Punto de entrada único: todos los factores de un ticker en una
    sola pasada, para que scoring.py y classification.py nunca
    recalculen el mismo número dos veces con datos distintos."""
    macd_resultado = macd(b)
    return FactoresMomentum(
        gap_pct=gap_pct(b),
        rvol=rvol(b),
        breakout_20d=breakout_nd(b, 20),
        distancia_max_52s=distancia_maximo_52s(b),
        ema20=ema(b.close, 20),
        ema50=ema(b.close, 50),
        vwap_proxy=vwap_proxy(b),
        atr=atr(b),
        rsi=rsi(b),
        macd=macd_resultado[0] if macd_resultado else None,
        macd_signal=macd_resultado[1] if macd_resultado else None,
        macd_hist=macd_resultado[2] if macd_resultado else None,
    )
