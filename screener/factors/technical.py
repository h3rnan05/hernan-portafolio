"""Factores técnicos y de precio, calculados desde barras diarias.

Cada función toma `Barras` y devuelve un número crudo (aún NO normalizado;
la normalización cross-sectional 0-100 la hace scoring.py). Todo con la
librería estándar — sin pandas/numpy — para que corra ligero en CI.
"""

from __future__ import annotations

import statistics

from screener.data.provider import Barras


def sma(valores: list[float], n: int) -> float | None:
    """Media móvil simple de las últimas `n` barras -- pública porque
    telegram_bot/trade_command.py la reutiliza como nivel de referencia
    real (no inventado) para "precio ideal para volver a revisar" en el
    Plan de acción de /trade."""
    return sum(valores[-n:]) / n if len(valores) >= n else None


def retorno(close: list[float], dias: int) -> float | None:
    """Retorno simple sobre `dias` barras (momentum)."""
    if len(close) <= dias or close[-dias - 1] == 0:
        return None
    return close[-1] / close[-dias - 1] - 1.0


def momentum_compuesto(b: Barras) -> float | None:
    """Momentum estilo institucional: promedio de retornos 3/6/12 meses
    (~63/126/252 barras). Excluye el mes más reciente sería lo purista;
    aquí se mantiene simple e incluye hasta hoy."""
    partes = [retorno(b.close, d) for d in (63, 126, 252)]
    partes = [p for p in partes if p is not None]
    return sum(partes) / len(partes) if partes else None


def volatilidad_anual(b: Barras, ventana: int = 63) -> float | None:
    """Desviación estándar anualizada de retornos diarios (menor = mejor
    para el factor defensivo baja_vol)."""
    c = b.close[-ventana - 1:]
    if len(c) < 20:
        return None
    rets = [c[i] / c[i - 1] - 1.0 for i in range(1, len(c)) if c[i - 1]]
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets) * (252 ** 0.5)


def maximo_52s(b: Barras) -> float | None:
    """Máximo de cierre de las últimas 52 semanas (~252 barras) -- nivel
    técnico real de referencia, público porque telegram_bot/
    trade_command.py lo reutiliza como el nivel de ruptura para
    reconsiderar una tesis alcista en pausa (no un porcentaje inventado)."""
    ventana = b.close[-252:] if len(b.close) >= 252 else b.close
    return max(ventana) if ventana else None


def proximidad_maximo_52s(b: Barras) -> float | None:
    """Qué tan cerca del máximo de 52 semanas está el precio (1.0 = en máximos).
    Es la señal de fuerza/ruptura que buscan los trend-followers."""
    maximo = maximo_52s(b)
    return b.close[-1] / maximo if maximo else None


def score_tendencia(b: Barras) -> float | None:
    """Alineación con medias móviles: +1 por cada condición alcista cumplida
    (precio>50DMA, 50>100, 100>200). Rango 0..3 → lo normaliza scoring.py."""
    c = b.close
    sma50, sma100, sma200 = sma(c, 50), sma(c, 100), sma(c, 200)
    if None in (sma50, sma100, sma200):
        return None
    p = c[-1]
    return float((p > sma50) + (sma50 > sma100) + (sma100 > sma200))


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


def dollar_volume(b: Barras, ventana: int = 21) -> float | None:
    """Liquidez: precio × volumen promedio (dólares negociados por día)."""
    if len(b) < ventana:
        return None
    dvs = [b.close[i] * b.volume[i] for i in range(-ventana, 0)]
    return sum(dvs) / ventana


def atr(b: Barras, periodo: int = 20) -> float | None:
    """Average True Range -- mismo cálculo (true range = max(high-low,
    |high-close_previo|, |low-close_previo|), promediado sobre `periodo`
    barras) y mismo periodo por defecto que ya usa wizards_bot.py para su
    stop de 2×ATR en el bot de Turtle Trading. Pública porque telegram_bot/
    trade_command.py la reutiliza como margen objetivo de volatilidad para
    los niveles de entrada/cancelación del Plan de acción de /trade --
    misma convención de riesgo ya validada en producción, no un margen
    inventado."""
    if len(b.close) < 2:
        return None
    trs = [
        max(b.high[i] - b.low[i], abs(b.high[i] - b.close[i - 1]), abs(b.low[i] - b.close[i - 1]))
        for i in range(1, len(b.close))
    ]
    ventana = trs[-periodo:]
    return sum(ventana) / len(ventana) if ventana else None


ATR_MULT_ENTRADA = 1.0
ATR_MULT_STOP = 2.0  # misma convención de wizards_bot.py: stop = entrada - 2×ATR


def niveles_precio(spot: float, atr_val: float | None, sma50: float | None) -> dict[str, float | None]:
    """Niveles de precio objetivos por reglas objetivas -- ATR (misma
    convención de wizards_bot.py) y SMA50 (soporte técnico real), nunca un
    número inventado. Pública porque tanto telegram_bot/trade_command.py
    como telegram_bot/report_command.py la reutilizan como el mismo motor
    de niveles de entrada/salida (vive aquí, no en ninguno de los dos
    comandos, para que ambos importen la misma implementación sin crear
    un import circular entre ellos). "entrada": primer pullback
    (spot - 1×ATR). "ideal": el más profundo entre ese pullback y la
    media móvil de 50 días (si está disponible) -- así "ideal" nunca
    queda por encima de "entrada". "cancelar": 2×ATR por debajo de
    "ideal" (mismo múltiplo de stop que ya usa el bot de Turtle Trading).
    Cualquier nivel que no se pueda calcular con los datos disponibles
    queda en None -- nunca se rellena con un valor inventado."""
    entrada = spot - ATR_MULT_ENTRADA * atr_val if atr_val is not None else None
    if sma50 is not None and entrada is not None:
        ideal = min(sma50, entrada)
    elif sma50 is not None:
        ideal = sma50
    else:
        ideal = entrada
    cancelar = ideal - ATR_MULT_STOP * atr_val if (ideal is not None and atr_val is not None) else None
    return {"entrada": entrada, "ideal": ideal, "cancelar": cancelar}
