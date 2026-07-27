"""Seguimiento post-alerta -- refinamiento "Head Trader" (2026-07-27),
punto 8: "el trabajo no termina cuando manda Telegram. Debe seguir
respondiendo: sigue válida / empieza a debilitarse / rompió el stop /
alcanzó objetivo / volumen desapareció. Como un operador sentado frente
a la pantalla."

Cada corrida del pipeline (cada ~30 min en horario de mercado) revisa
las alertas de HOY que todavía no llegaron a un estado terminal, evalúa
su estado con las mismas velas intradía que ya sabe pedir, y avisa SOLO
cuando el estado CAMBIA -- nunca un "sigue todo bien" cada media hora
(ese sería el mismo ruido que el resto del sistema existe para evitar).

Estados y su prioridad (el primero que aplica gana):

1. `rompio_stop`     -- el mínimo posterior a la alerta tocó el stop.
2. `alcanzo_objetivo` -- el máximo posterior a la alerta tocó el objetivo.
3. `volumen_desaparecio` -- se negocia menos que el promedio reciente.
4. `debilitandose`   -- perdió su precio promedio del día (los
   compradores ya no defienden el nivel).
5. `sigue_valida`    -- nada de lo anterior.

El stop se revisa ANTES que el objetivo a propósito: si en la misma
ventana el precio tocó ambos, asumir que tocó el stop primero es la
lectura conservadora (Principio 5: preservar capital; con velas de 1
minuto agregadas no se puede saber el orden real dentro de la ventana).

Los máximos/mínimos se miden SOLO sobre velas posteriores al timestamp
de la alerta -- el rango de la mañana previo a la alerta no cuenta como
"tocó el objetivo". 100% determinista; los textos van en lenguaje
humano, sin jerga (misma regla que report.py)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.factors import intradia as fi
from momentum_hunter.models import BarraIntradia, FactoresIntradia
from momentum_hunter.tracker import AlertaRegistrada

log = logging.getLogger("momentum_hunter.vigilancia")

ESTADOS_TERMINALES = frozenset({"rompio_stop", "alcanzo_objetivo"})
UMBRAL_VOLUMEN_DESAPARECIO = 1.0   # RVOL inmediato < 1 = menos volumen que el promedio reciente

_TEXTOS = {
    "rompio_stop": "tocó la salida que marcamos. Si entraste, la idea era salir ahí -- "
                   "sin excepciones.",
    "alcanzo_objetivo": "alcanzó la primera meta. Si entraste, es momento de asegurar "
                        "algo de la ganancia.",
    "volumen_desaparecio": "el volumen desapareció -- ya casi nadie está negociando. "
                           "Sin combustible, la idea pierde fuerza.",
    "debilitandose": "empieza a debilitarse: perdió su precio promedio del día y los "
                     "compradores ya no defienden el nivel.",
    "sigue_valida": "sigue válida otra vez -- recuperó el nivel y el interés.",
}
_EMOJIS = {
    "rompio_stop": "🛑", "alcanzo_objetivo": "🎯",
    "volumen_desaparecio": "🥀", "debilitandose": "⚠️", "sigue_valida": "✅",
}


def _velas_despues_de(bi_hoy: BarraIntradia, fecha_alerta_iso: str) -> BarraIntradia:
    """Solo las velas con timestamp >= al de la alerta -- comparación
    lexicográfica válida porque ambos son ISO 8601 UTC."""
    marca = fecha_alerta_iso[:19]
    idxs = [i for i, t in enumerate(bi_hoy.timestamps) if t[:19] >= marca]
    return BarraIntradia(
        bi_hoy.ticker, [bi_hoy.timestamps[i] for i in idxs], [bi_hoy.open[i] for i in idxs],
        [bi_hoy.close[i] for i in idxs], [bi_hoy.high[i] for i in idxs],
        [bi_hoy.low[i] for i in idxs], [bi_hoy.volume[i] for i in idxs],
    )


def evaluar_estado(
    a: AlertaRegistrada, bi_hoy: BarraIntradia, factores: FactoresIntradia,
) -> str | None:
    """El estado actual de una alerta -- None si no hay datos suficientes
    para opinar (nunca se inventa un estado)."""
    despues = _velas_despues_de(bi_hoy, a.fecha)
    if not despues.close or factores.precio_actual is None:
        return None

    if a.stop is not None and min(despues.low) <= a.stop:
        return "rompio_stop"
    if a.objetivo1 is not None and max(despues.high) >= a.objetivo1:
        return "alcanzo_objetivo"
    if factores.rvol_actual is not None and factores.rvol_actual < UMBRAL_VOLUMEN_DESAPARECIO:
        return "volumen_desaparecio"
    if factores.vwap is not None and factores.precio_actual < factores.vwap:
        return "debilitandose"
    return "sigue_valida"


def vigilar(
    alertas: list[AlertaRegistrada], provider: DataProvider, cfg: MomentumConfig,
    ahora: datetime | None = None,
    barras_intradia: dict[str, BarraIntradia] | None = None,
) -> list[str]:
    """Revisa las alertas de HOY pendientes y devuelve los mensajes de
    los CAMBIOS de estado (mutando `alertas[i].ultimo_estado` -- quien
    llama decide persistir con `tracker.guardar`). La primera lectura
    "sigue_valida" no genera mensaje: recién alertada, "todo bien" es lo
    esperado, no una novedad.

    `barras_intradia` es inyectable para pruebas; en producción se piden
    al provider SOLO para los tickers vigilados (nunca el universo)."""
    ahora = ahora or datetime.now(UTC)
    hoy = ahora.date().isoformat()
    pendientes = [
        a for a in alertas
        if a.fecha[:10] == hoy and a.ultimo_estado not in ESTADOS_TERMINALES
    ]
    if not pendientes:
        return []

    if barras_intradia is None:
        tickers = sorted({a.ticker for a in pendientes})
        barras_intradia = provider.barras_intradia(tickers, cfg.intervalo_intradia, cfg.periodo_intradia)

    mensajes: list[str] = []
    for a in pendientes:
        bi = barras_intradia.get(a.ticker)
        if bi is None:
            continue
        try:
            bi_hoy = fi.barras_de_hoy(bi)
            factores = fi.calcular(bi)
            estado = evaluar_estado(a, bi_hoy, factores)
        except Exception as e:
            log.warning("vigilancia de %s falló: %s", a.ticker, e)
            continue
        if estado is None or estado == a.ultimo_estado:
            continue
        primera_lectura_sana = a.ultimo_estado is None and estado == "sigue_valida"
        a.ultimo_estado = estado
        if primera_lectura_sana:
            continue
        mensajes.append(f"{_EMOJIS[estado]} {a.ticker}: {_TEXTOS[estado]}")
    return mensajes
