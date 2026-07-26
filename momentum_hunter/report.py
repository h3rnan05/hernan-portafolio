"""Ensambla la `Oportunidad` final y la formatea para Telegram --
Prompt 8 (Ticker/Empresa/Convicción/Catalizador/Qué ocurrió/Por qué
puede seguir subiendo/Entrada/Stop/Objetivos/Riesgo/Capital
mínimo/Urgencia/Qué espero/Qué invalida/Tiempo esperado/Alertas/Plan de
acción), más la clasificación con emoji del módulo `classification.py`.

Niveles de precio por ATR (mismo principio que `screener/factors/
technical.niveles_precio`, pero con un múltiplo de stop más ajustado:
1.5×ATR en vez de 2×ATR. Es una decisión deliberada, no un descuido: las
posiciones de este bot se sostienen 1-10 días, no semanas -- un stop más
ajustado es coherente con un horizonte más corto y con nombres de mayor
volatilidad estructural (penny stocks/low float)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from momentum_hunter.alerts import Candidato
from momentum_hunter.classification import ETIQUETAS, tipo_oportunidad
from momentum_hunter.config import MomentumConfig
from momentum_hunter.models import Oportunidad
from momentum_hunter.strategy import decidir_estrategia, tiene_opciones

ATR_MULT_STOP = 1.5
RR_OBJETIVO_1 = 2.0   # primer objetivo a 2R
RR_OBJETIVO_2 = 4.0   # segundo objetivo a 4R

_URGENCIA_POR_TIPO = {
    "short_squeeze": "Alta", "breakout": "Alta", "earnings_play": "Alta",
    "news_momentum": "Alta", "trend_continuation": "Media", "reversal": "Baja",
}
_URGENCIA_EMOJI = {"Alta": "🔴", "Media": "🟡", "Baja": "⚪"}

_TIEMPO_ESPERADO = {
    "short_squeeze": "1-3 días (los short squeezes se resuelven rápido)",
    "breakout": "3-7 días",
    "earnings_play": "1-3 días",
    "news_momentum": "1-5 días",
    "trend_continuation": "5-10 días",
    "reversal": "5-10 días",
}

_POR_QUE_SEGUIR = {
    "short_squeeze": "Float bajo + interés en corto elevado: cualquier fuerza compradora "
                     "adicional obliga a los vendedores en corto a recomprar, lo que empuja "
                     "el precio más -- un mecanismo mecánico, no solo sentimiento.",
    "breakout": "Rompe una resistencia real con volumen muy por encima del promedio: los "
               "compradores atrapados en corto y el momentum técnico suelen extender el "
               "movimiento en los días siguientes.",
    "earnings_play": "El mercado está re-precificando la acción tras un resultado que superó "
                     "(o decepcionó) las expectativas -- ese ajuste de precio rara vez ocurre "
                     "en un solo día.",
    "news_momentum": "El catalizador es reciente y todavía se está difundiendo -- el volumen "
                     "inusual de hoy sugiere que no todo el mercado lo ha descontado aún.",
    "trend_continuation": "La tendencia de fondo sigue intacta y este es un retroceso sano "
                          "hacia la media de 20 días, no una reversión.",
    "reversal": "El RSI sale de sobreventa y el MACD ya cruzó al alza -- las dos señales "
               "juntas sugieren que la presión vendedora se está agotando.",
}

_QUE_INVALIDA = {
    "short_squeeze": "Si el volumen cae por debajo del promedio o el precio pierde el mínimo "
                     "del día, el squeeze pierde presión.",
    "breakout": "Si vuelve a cerrar por debajo del nivel de ruptura, la ruptura se invalida.",
    "earnings_play": "Si el precio revierte y cierra por debajo del precio de apertura del "
                     "día del gap, la reacción inicial se está revirtiendo.",
    "news_momentum": "Si el volumen vuelve a su promedio sin que el precio haya avanzado, el "
                     "mercado ya descontó la noticia.",
    "trend_continuation": "Si rompe con fuerza por debajo de la media de 20 días, la "
                          "corrección deja de ser sana.",
    "reversal": "Si el RSI vuelve a caer por debajo de 30 o el MACD cruza de nuevo a la baja, "
               "la reversión se invalida.",
}

_QUE_ESPERO = {
    "short_squeeze": "Que el volumen se mantenga muy por encima del promedio en las próximas "
                     "sesiones, confirmando que el squeeze sigue en marcha.",
    "breakout": "Que el precio se mantenga sobre el nivel de ruptura en los próximos días.",
    "earnings_play": "Que el precio se mantenga por encima del rango de apertura del día del "
                     "reporte.",
    "news_momentum": "Que el mercado siga reaccionando a la noticia en las próximas sesiones.",
    "trend_continuation": "Que el rebote confirme el soporte de la media de 20 días.",
    "reversal": "Que el MACD y el RSI sigan confirmando la reversión en las próximas sesiones.",
}


def _niveles(spot: float, atr_val: float | None) -> dict[str, float | None]:
    if atr_val is None or atr_val <= 0:
        return {"stop": None, "objetivo1": None, "objetivo2": None}
    stop = spot - ATR_MULT_STOP * atr_val
    riesgo = spot - stop
    return {
        "stop": stop,
        "objetivo1": spot + riesgo * RR_OBJETIVO_1,
        "objetivo2": spot + riesgo * RR_OBJETIVO_2,
    }


def _riesgo_texto(c: Candidato, stop: float | None) -> str:
    partes = []
    if stop is not None and c.precio > 0:
        pct = (c.precio - stop) / c.precio * 100
        partes.append(f"Stop a {pct:.1f}% de la entrada (definido por ATR).")
    if c.meta.shares_float is not None:
        partes.append(f"Float: {c.meta.shares_float:,.0f} acciones.")
    if c.meta.short_pct_float is not None:
        partes.append(f"Interés en corto: {c.meta.short_pct_float:.0%} del float.")
    partes.append("Borrow fee: no disponible con datos gratis -- no se puede confirmar el "
                   "costo de mantener un short, lo cual no afecta una posición larga pero se "
                   "reporta por transparencia.")
    return " ".join(partes)


def _que_ocurrio(c: Candidato) -> str:
    base = f"{c.ticker} presenta RVOL de {c.factores.rvol:.1f}x el promedio" \
        if c.factores.rvol is not None else f"{c.ticker} presenta actividad inusual"
    if c.factores.gap_pct is not None:
        base += f" con un gap de {c.factores.gap_pct:+.1%}"
    if c.catalizador is not None:
        base += f", tras: \"{c.catalizador.titular}\" ({c.catalizador.fuente})."
    else:
        base += "."
    return base


def construir_oportunidad(
    c: Candidato, cfg: MomentumConfig, tiene_opciones_fn: Callable[[str], bool] = tiene_opciones,
) -> Oportunidad:
    """Ensambla la `Oportunidad` completa a partir de un `Candidato` que
    ya pasó el filtro de `alerts.filtrar_alertas` -- ningún cálculo aquí
    decide SI se manda (eso ya se decidió), solo CÓMO se presenta.
    `tiene_opciones_fn` es inyectable (por defecto `strategy.tiene_opciones`,
    que llama a red) para que las pruebas no dependan de la red."""
    tipo = tipo_oportunidad(c.precio, c.factores, c.catalizador, c.meta)
    niveles = _niveles(c.precio, c.factores.atr)
    try:
        opciones_ok = tiene_opciones_fn(c.ticker)
    except Exception:
        opciones_ok = False
    estrategia_nombre, estrategia_justificacion = decidir_estrategia(
        c.ticker, tipo, c.puntuacion.score_total,
        catalizador_confirmado=c.catalizador is not None and c.catalizador.confirmado,
        opciones_disponibles=opciones_ok, cfg=cfg,
    )
    niveles_alerta = sorted({
        round(x, 2) for x in (c.precio, niveles["stop"], niveles["objetivo1"], niveles["objetivo2"])
        if x is not None
    })

    return Oportunidad(
        ticker=c.ticker, nombre=c.nombre,
        clasificacion=ETIQUETAS[tipo], score=c.puntuacion.score_total,
        catalizador=c.catalizador, que_ocurrio=_que_ocurrio(c),
        por_que_puede_seguir=_POR_QUE_SEGUIR[tipo],
        entrada=c.precio, stop=niveles["stop"],
        primer_objetivo=niveles["objetivo1"], segundo_objetivo=niveles["objetivo2"],
        riesgo_texto=_riesgo_texto(c, niveles["stop"]),
        capital_minimo=c.precio * 100,
        urgencia=_URGENCIA_POR_TIPO[tipo],
        que_espero=_QUE_ESPERO[tipo], que_invalida=_QUE_INVALIDA[tipo],
        tiempo_esperado=_TIEMPO_ESPERADO[tipo],
        niveles_alerta=niveles_alerta,
        estrategia_nombre=estrategia_nombre, estrategia_justificacion=estrategia_justificacion,
        fecha=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def _fmt(x: float | None) -> str:
    return f"${x:,.2f}" if x is not None else "No disponible"


def formatear(o: Oportunidad) -> str:
    lineas = ["🚨 Momentum Detectado", "", f"Ticker: {o.ticker}"]
    if o.nombre:
        lineas.append(f"Empresa: {o.nombre}")
    lineas += ["", f"Clasificación: {o.clasificacion}", f"Convicción: {o.score:.0f}/100"]

    lineas += ["", "Catalizador:"]
    if o.catalizador is not None:
        lineas.append(f"{o.catalizador.tipo.replace('_', ' ').title()} -- \"{o.catalizador.titular}\" "
                      f"({o.catalizador.fuente})")
        if o.catalizador.fuentes_adicionales:
            lineas.append(f"Confirmado también por: {', '.join(o.catalizador.fuentes_adicionales)}")
    else:
        lineas.append("Sin catalizador verificable (esta alerta no debería haberse generado).")

    lineas += ["", "Qué ocurrió:", o.que_ocurrio]
    lineas += ["", "Por qué puede seguir subiendo:", o.por_que_puede_seguir]

    lineas += ["", f"Entrada: {_fmt(o.entrada)}", f"Stop: {_fmt(o.stop)}",
              f"Primer objetivo: {_fmt(o.primer_objetivo)}",
              f"Segundo objetivo: {_fmt(o.segundo_objetivo)}"]

    lineas += ["", "Riesgo:", o.riesgo_texto]
    lineas += ["", f"Capital mínimo (comprando acciones): {_fmt(o.capital_minimo)}",
              f"Estrategia recomendada: {o.estrategia_nombre}"]
    if o.estrategia_justificacion:
        lineas += ["¿Por qué esta estrategia?"]
        lineas += [f"✔ {b}" for b in o.estrategia_justificacion]

    lineas += ["", f"Nivel de urgencia: {_URGENCIA_EMOJI[o.urgencia]} {o.urgencia}"]
    lineas += ["", "Qué espero que ocurra:", o.que_espero]
    lineas += ["", "Qué invalidaría la tesis:", o.que_invalida]
    lineas += ["", f"Tiempo esperado: {o.tiempo_esperado}"]

    if o.niveles_alerta:
        lineas += ["", "Crear alertas en estos niveles: " + ", ".join(_fmt(x) for x in o.niveles_alerta)]

    lineas += ["", "Plan de acción:"]
    if o.estrategia_nombre == "No Operar":
        lineas.append("No abrir posición hoy -- el score o el catalizador no son suficientes.")
    else:
        lineas.append(
            f"Entrar cerca de {_fmt(o.entrada)} con {o.estrategia_nombre}, stop en "
            f"{_fmt(o.stop)}, tomar parte de la posición en {_fmt(o.primer_objetivo)} y el "
            f"resto en {_fmt(o.segundo_objetivo)} o antes si la tesis se invalida."
        )

    return "\n".join(lineas)
