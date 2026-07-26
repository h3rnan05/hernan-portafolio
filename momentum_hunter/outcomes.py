"""Actualiza `AlertaRegistrada.resultados_pct` con datos de mercado
reales -- la mitad de Prompt 10 que sí necesita red (por eso vive
separada de `tracker.py`, que es persistencia pura, mismo split que
`journal/store.py` vs. `journal/stats.py`).

Encuentra la barra diaria más cercana (en o después) a la fecha de la
alerta y mide el retorno de cierre a `+1/+3/+5/+10` sesiones (los
horizontes de `cfg.horizontes_seguimiento`) -- sesiones de MERCADO
(barras), no días calendario, porque son las que de verdad importan
para "¿tocó el objetivo en 5 días de trading?". También lleva el mejor
y peor movimiento intradía (high/low) visto dentro de la ventana del
horizonte más largo, para poder auditar después si el stop se hubiera
activado antes de llegar al resultado final."""

from __future__ import annotations

from datetime import UTC, date, datetime

from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Barras
from momentum_hunter.tracker import AlertaRegistrada


def _fecha_de_barra(epoch_str: str) -> str | None:
    try:
        return datetime.fromtimestamp(int(epoch_str), tz=UTC).date().isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _indice_entrada(b: Barras, fecha_alerta: str) -> int | None:
    """Primera barra en o después de la fecha de la alerta -- si la
    alerta se mandó intradía, el cierre de ESE mismo día ya cuenta como
    la referencia de "día 0"."""
    objetivo = fecha_alerta[:10]
    for i, f in enumerate(b.fechas):
        fecha_barra = _fecha_de_barra(f)
        if fecha_barra is not None and fecha_barra >= objetivo:
            return i
    return None


def actualizar_resultados(
    alertas: list[AlertaRegistrada], provider: DataProvider, cfg: MomentumConfig,
    hoy: date | None = None,
) -> list[AlertaRegistrada]:
    """Muta y devuelve la misma lista -- solo toca las alertas no
    resueltas, y dentro de esas, solo los horizontes que todavía no
    tienen resultado (nunca recalcula uno ya conocido con datos nuevos)."""
    pendientes = [a for a in alertas if not a.resuelta]
    if not pendientes:
        return alertas

    tickers = sorted({a.ticker for a in pendientes})
    max_horizonte = max(cfg.horizontes_seguimiento)
    barras = provider.barras(tickers, dias=max_horizonte + 30)

    for a in pendientes:
        b = barras.get(a.ticker)
        if b is None or a.precio_entrada <= 0:
            continue
        idx = _indice_entrada(b, a.fecha)
        if idx is None:
            continue

        for h in cfg.horizontes_seguimiento:
            clave = f"{h}d"
            if a.resultados_pct.get(clave) is not None:
                continue
            idx_h = idx + h
            if idx_h < len(b.close):
                a.resultados_pct[clave] = (b.close[idx_h] - a.precio_entrada) / a.precio_entrada

        fin = min(idx + max_horizonte, len(b.close) - 1)
        ventana_high = b.high[idx: fin + 1]
        ventana_low = b.low[idx: fin + 1]
        if ventana_high:
            favor = (max(ventana_high) - a.precio_entrada) / a.precio_entrada
            a.precio_maximo_pct = favor if a.precio_maximo_pct is None else max(a.precio_maximo_pct, favor)
        if ventana_low:
            adverso = (min(ventana_low) - a.precio_entrada) / a.precio_entrada
            a.precio_minimo_pct = adverso if a.precio_minimo_pct is None else min(a.precio_minimo_pct, adverso)

        if all(a.resultados_pct.get(f"{h}d") is not None for h in cfg.horizontes_seguimiento):
            a.resuelta = True

    return alertas
