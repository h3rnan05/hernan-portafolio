"""Factores intradía -- lo que un trader mira en la pantalla en tiempo
real, no el cierre de ayer. Todo se calcula desde `BarraIntradia`
(genérica, ver `models.py`) -- ninguna función aquí sabe de dónde
vinieron los datos.

Con velas intradía reales, `vwap_real` deja de ser una aproximación: es
el VWAP de verdad de la sesión de hoy (ver la limitación que
`factors/momentum.vwap_proxy` documentaba honestamente sobre no tener
ticks -- esto la resuelve para los candidatos que llegan a esta etapa).

Convención de sesión (aprox., mismo caveat que ya usan los cron de
`.github/workflows/*.yml`): apertura regular 13:30 UTC, cierre 20:00 UTC
(horario de verano ET). "Hoy" se define como la fecha de la ÚLTIMA vela
recibida, no la fecha del sistema -- así el módulo funciona igual en
producción y en pruebas con datos fabricados."""

from __future__ import annotations

from datetime import datetime

from momentum_hunter.models import BarraIntradia, FactoresIntradia

HORA_APERTURA_UTC = 13.5   # 9:30am ET (verano)
HORA_CIERRE_UTC = 20.0     # 4:00pm ET (verano)


def _hora_utc(timestamp_iso: str) -> float:
    dt = datetime.fromisoformat(timestamp_iso)
    return dt.hour + dt.minute / 60.0


def _fecha(timestamp_iso: str) -> str:
    return timestamp_iso[:10]


def es_premarket(timestamp_iso: str) -> bool:
    return _hora_utc(timestamp_iso) < HORA_APERTURA_UTC


def es_sesion_regular(timestamp_iso: str) -> bool:
    return HORA_APERTURA_UTC <= _hora_utc(timestamp_iso) < HORA_CIERRE_UTC


def barras_de_hoy(bi: BarraIntradia) -> BarraIntradia:
    """Solo las velas de la fecha de la ÚLTIMA vela -- filtra días
    anteriores que Yahoo devuelve de más (se pide `periodo="5d"` para
    tener el cierre de ayer disponible, pero la sesión de "hoy" es una
    sola fecha)."""
    if not bi.timestamps:
        return bi
    hoy = _fecha(bi.timestamps[-1])
    idxs = [i for i, t in enumerate(bi.timestamps) if _fecha(t) == hoy]
    return BarraIntradia(
        bi.ticker, [bi.timestamps[i] for i in idxs], [bi.open[i] for i in idxs],
        [bi.close[i] for i in idxs], [bi.high[i] for i in idxs],
        [bi.low[i] for i in idxs], [bi.volume[i] for i in idxs],
    )


def maximo_premarket(bi_hoy: BarraIntradia) -> float | None:
    highs = [h for t, h in zip(bi_hoy.timestamps, bi_hoy.high, strict=True) if es_premarket(t)]
    return max(highs) if highs else None


def maximo_dia(bi_hoy: BarraIntradia) -> float | None:
    return max(bi_hoy.high) if bi_hoy.high else None


def rango_apertura(bi_hoy: BarraIntradia, minutos: int = 5) -> tuple[float, float] | None:
    """High/low de los primeros `minutos` de la sesión regular -- el
    nivel de un Opening Range Breakout."""
    regulares = [
        (t, h, low) for t, h, low in zip(bi_hoy.timestamps, bi_hoy.high, bi_hoy.low, strict=True)
        if es_sesion_regular(t)
    ]
    if not regulares:
        return None
    inicio = _hora_utc(regulares[0][0])
    ventana = [(h, low) for t, h, low in regulares if _hora_utc(t) < inicio + minutos / 60.0]
    if not ventana:
        return None
    return max(h for h, _ in ventana), min(low for _, low in ventana)


def vwap_real(bi_hoy: BarraIntradia) -> float | None:
    """VWAP de la sesión regular de hoy hasta la última vela -- excluye
    premarket (convención estándar: el VWAP que sigue un trader durante
    el día es el de la sesión regular)."""
    regulares = [
        (h, low, c, v) for t, h, low, c, v in
        zip(bi_hoy.timestamps, bi_hoy.high, bi_hoy.low, bi_hoy.close, bi_hoy.volume, strict=True)
        if es_sesion_regular(t)
    ]
    if not regulares:
        return None
    vol_total = sum(v for *_, v in regulares)
    if vol_total <= 0:
        return None
    return sum(((h + low + c) / 3) * v for h, low, c, v in regulares) / vol_total


def ema9_intradia(bi_hoy: BarraIntradia) -> float | None:
    closes = bi_hoy.close
    if len(closes) < 9:
        return None
    k = 2.0 / (9 + 1)
    ema = sum(closes[:9]) / 9
    for v in closes[9:]:
        ema = v * k + ema * (1 - k)
    return ema


def rvol_actual(bi: BarraIntradia, ventana: int = 5) -> float | None:
    """Volumen de la vela actual / promedio de las `ventana` anteriores
    -- una lectura inmediata, no el promedio de 20 DÍAS que usa
    `factors/momentum.rvol` (ese responde '¿hay más volumen que lo
    normal hoy?'; este responde '¿está entrando dinero AHORA MISMO?')."""
    if len(bi.volume) < ventana + 1:
        return None
    anteriores = bi.volume[-ventana - 1:-1]
    promedio = sum(anteriores) / len(anteriores)
    return bi.volume[-1] / promedio if promedio > 0 else None


def aceleracion_volumen(bi: BarraIntradia, ventana: int = 3) -> float | None:
    """Promedio de las últimas `ventana` velas / promedio de las
    `ventana` anteriores a esas. > 1 significa que el volumen se está
    ACELERANDO, no solo que hay volumen alto -- distingue "está
    entrando dinero ahora" de "entró dinero hace un rato y ya se
    enfrió", que es exactamente la pregunta 2 de Prompt 4."""
    if len(bi.volume) < ventana * 2:
        return None
    recientes = bi.volume[-ventana:]
    previas = bi.volume[-ventana * 2:-ventana]
    prom_previo = sum(previas) / len(previas)
    if prom_previo <= 0:
        return None
    return (sum(recientes) / len(recientes)) / prom_previo


def gap_pct(bi_hoy: BarraIntradia, cierre_anterior: float | None) -> float | None:
    """Gap de la apertura REGULAR de hoy vs. el cierre regular de ayer
    (`cierre_anterior` viene de las barras diarias ya calculadas en la
    etapa 1 -- no se vuelve a pedir con otro dato)."""
    if cierre_anterior is None or cierre_anterior == 0:
        return None
    regulares = [o for t, o in zip(bi_hoy.timestamps, bi_hoy.open, strict=True) if es_sesion_regular(t)]
    if not regulares:
        return None
    return (regulares[0] - cierre_anterior) / cierre_anterior


def velas_desde_ruptura(bi: BarraIntradia, nivel: float) -> int | None:
    """Cuántas velas lleva el cierre sosteniéndose por encima de `nivel`
    (0 = la vela actual fue la primera en romperlo). None si el precio
    actual no está por encima del nivel -- no hay ruptura vigente que
    medir."""
    if not bi.close or bi.close[-1] <= nivel:
        return None
    i = len(bi.close) - 1
    while i > 0 and bi.close[i - 1] > nivel:
        i -= 1
    return len(bi.close) - 1 - i


def calcular(
    bi: BarraIntradia, cierre_anterior: float | None = None, nivel_ruptura: float | None = None,
) -> FactoresIntradia:
    """Punto de entrada único -- todos los factores intradía de un
    ticker en una sola pasada, sobre las velas de HOY únicamente."""
    hoy = barras_de_hoy(bi)
    apertura = rango_apertura(hoy)
    return FactoresIntradia(
        precio_actual=hoy.close[-1] if hoy.close else None,
        vwap=vwap_real(hoy),
        ema9=ema9_intradia(hoy),
        rvol_actual=rvol_actual(hoy),
        aceleracion_volumen=aceleracion_volumen(hoy),
        gap_pct=gap_pct(hoy, cierre_anterior),
        maximo_dia=maximo_dia(hoy),
        maximo_premarket=maximo_premarket(hoy),
        rango_apertura_max=apertura[0] if apertura else None,
        rango_apertura_min=apertura[1] if apertura else None,
        velas_desde_ruptura=(
            velas_desde_ruptura(hoy, nivel_ruptura) if nivel_ruptura is not None else None
        ),
    )
