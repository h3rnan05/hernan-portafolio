"""Formato de alerta -- rediseño completo (Prompts 3, 5, 6, 7). Ya no es
un reporte con campos etiquetados: es el mensaje que mandaría un trader
de momentum por Telegram, pensado para leerse en menos de 20 segundos.

Siete preguntas, en este orden, nunca más:
¿Por qué apareció? ¿Por qué ahora? ¿Qué patrón? ¿Qué espero?
¿Dónde entro? ¿Dónde salgo? ¿Qué invalida la operación?

Voz (Prompt 7): primera persona, presente, sin jerga de Wall Street.
"Entraría en...", "Cancelo si...", nunca "El precio presenta una
estructura técnica que sugiere...". Todo lo que se dice sale de datos ya
calculados en `evaluator.py`/`early_opportunity.py`/`factors/intradia.py`
-- nunca se inventa un número ni una razón."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_hunter import classification
from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.models import FactoresIntradia, Oportunidad

# Prompt 6: la urgencia depende de qué tan fresco sigue el patrón, no del
# tipo de patrón -- se calcula desde `velas_desde_ruptura`, la misma
# métrica que ya usa `early_opportunity.py` para "frescura". Los cortes
# son fracciones del techo configurado (`cfg.velas_maximas_desde_patron`)
# para que ambos módulos midan "tarde" con la misma vara.
_MOTIVO_URGENCIA = {
    "muy_alta": "El patrón acaba de activarse -- probablemente tengas pocos minutos.",
    "alta": "La entrada sigue siendo válida.",
    "media": "Todavía puede esperar, pero se está acercando el límite.",
    "baja": "Ya pasó la ventana en la que esta entrada tenía sentido -- solo para watchlist.",
}
_EMOJI_URGENCIA = {"muy_alta": "🔴", "alta": "🟠", "media": "🟡", "baja": "⚪"}
_NOMBRE_URGENCIA = {"muy_alta": "Muy Alta", "alta": "Alta", "media": "Media", "baja": "Baja"}

_TITULAR_CORTO = {
    "high_tight_flag": "bandera muy angosta -- movimiento explosivo",
    "gap_and_go": "rompiendo AHORA",
    "opening_range_breakout": "rompiendo el rango de apertura",
    "bull_flag": "rompiendo la bandera",
    "micro_pullback": "recuperando tras el pullback",
    "trend_continuation": "la tendencia sigue intacta",
}

_LINEA_PATRON = {
    "high_tight_flag": "Formó una bandera muy angosta después de un impulso fuerte -- consolidación mínima.",
    "gap_and_go": "Gap de apertura y ya está extendiendo el movimiento.",
    "opening_range_breakout": "Rompió el rango de los primeros minutos de la sesión.",
    "bull_flag": "Hizo una bandera alcista: impulso, pausa, y ahora vuelve a intentar romper.",
    "micro_pullback": "Hizo un Micro Pullback y ya está recuperando.",
    "trend_continuation": "La tendencia de las últimas velas se mantiene intacta.",
}

_QUE_ESPERO = {
    "high_tight_flag": "Que rompa el máximo de la bandera sin perder la parte alta de la consolidación.",
    "gap_and_go": "Que aguante sobre el máximo del premarket en el próximo respiro.",
    "opening_range_breakout": "Que no vuelva a meterse dentro del rango de apertura.",
    "bull_flag": "Que rompa el máximo de la bandera con volumen.",
    "micro_pullback": "Que aguante sobre la EMA9 en el próximo pullback.",
    "trend_continuation": "Que se mantenga sobre VWAP y EMA9.",
}


def _nivel_invalidacion(candidato: CandidatoIntradia) -> tuple[float | None, str]:
    """El nivel que, de perderse, cancela la tesis -- el máximo del
    premarket para patrones de ruptura de apertura, el rango de apertura
    para ORB, VWAP/EMA9 para el resto. Nunca un número inventado: sale
    de los mismos factores ya calculados."""
    f = candidato.factores
    patron = candidato.resultado.patron
    if patron in ("gap_and_go",) and f.maximo_premarket is not None:
        return f.maximo_premarket, "el máximo del premarket"
    if patron == "opening_range_breakout" and f.rango_apertura_max is not None:
        return f.rango_apertura_max, "el rango de apertura"
    if f.ema9 is not None:
        return f.ema9, "la EMA9"
    if f.vwap is not None:
        return f.vwap, "el VWAP"
    return None, "el nivel de entrada"


def niveles_entrada_salida(factores: FactoresIntradia, atr_diario: float | None) -> dict[str, float | None]:
    """Stop por debajo del ancla intradía más cercana (VWAP/EMA9) --
    nunca un porcentaje fijo inventado. Si ninguna de las dos está
    disponible, cae al ATR diario de la etapa 1 como margen aproximado.
    Objetivo = 2R, la misma referencia de riesgo/recompensa que ya usa
    `early_opportunity._score_riesgo_recompensa`. Pública porque
    `run.py` necesita estos mismos niveles ANTES de construir la
    `Oportunidad` final -- para pasárselos a `evaluator.evaluar` (la
    pregunta 5 los usa para calcular riesgo/recompensa) -- y no debe
    haber dos fórmulas de niveles distintas en el pipeline."""
    precio = factores.precio_actual
    if precio is None:
        return {"entrada": None, "stop": None, "objetivo": None}
    anclas = [a for a in (factores.vwap, factores.ema9) if a is not None and a < precio]
    if anclas:
        stop = max(anclas) * 0.995
    elif atr_diario is not None:
        stop = precio - atr_diario * 0.5
    else:
        stop = None
    objetivo = None
    if stop is not None and precio > stop:
        objetivo = precio + (precio - stop) * 2.0
    return {"entrada": precio, "stop": stop, "objetivo": objetivo}


def _urgencia(candidato: CandidatoIntradia, techo_velas: int) -> str:
    if not candidato.resultado.temprano:
        return "baja"
    velas = candidato.factores.velas_desde_ruptura
    if velas is None:
        return "media"
    if velas <= 1:
        return "muy_alta"
    if velas <= max(1, techo_velas // 2):
        return "alta"
    return "media"


def _por_que_aparecio(candidato: CandidatoIntradia) -> list[str]:
    """Prompt 5: la secuencia cronológica de por qué esta alerta aparece
    AHORA -- máximo 5 líneas, cada una trazable a un dato real."""
    lineas: list[str] = []
    c = candidato.catalizador
    if c is not None:
        resumen = c.tipo.replace("_", " ")
        if candidato.minutos_desde_catalizador is not None:
            lineas.append(
                f"Hace {int(candidato.minutos_desde_catalizador)} min: {resumen} -- "
                f'"{c.titular}" ({c.fuente}).'
            )
        else:
            lineas.append(f'Catalizador: {resumen} -- "{c.titular}" ({c.fuente}).')

    f = candidato.factores
    if f.rvol_actual is not None:
        lineas.append(f"El volumen se aceleró: RVOL de {f.rvol_actual:.1f}x ahora mismo.")
    if f.maximo_premarket is not None and f.precio_actual is not None and f.precio_actual > f.maximo_premarket:
        lineas.append(f"Rompió el máximo del premarket (${f.maximo_premarket:.2f}).")

    patron = candidato.resultado.patron
    if patron in _LINEA_PATRON:
        lineas.append(_LINEA_PATRON[patron])

    return lineas[:5]


def construir_oportunidad(candidato: CandidatoIntradia, techo_velas: int) -> Oportunidad:
    """Ensambla la `Oportunidad` final -- ya se decidió que se manda
    (`candidato.resultado.accionable`); esto solo decide CÓMO se
    presenta."""
    patron = candidato.resultado.patron or "trend_continuation"
    urgencia_clave = _urgencia(candidato, techo_velas)
    niveles = niveles_entrada_salida(candidato.factores, candidato.atr_diario)
    nivel_inval, nombre_nivel_inval = _nivel_invalidacion(candidato)

    invalidacion = (
        f"Se cancela si vuelve a meterse debajo de ${nivel_inval:.2f} ({nombre_nivel_inval})."
        if nivel_inval is not None
        else "Se cancela si pierde el nivel que activó la entrada."
    )

    early = candidato.resultado.early
    veredicto_texto = ""
    if early is not None:
        prefijo = "temprano" if candidato.resultado.temprano else "tarde"
        veredicto_texto = f"Vamos {prefijo}: {early.motivo_veredicto}"

    return Oportunidad(
        ticker=candidato.ticker, nombre=candidato.nombre,
        urgencia=_NOMBRE_URGENCIA[urgencia_clave], urgencia_emoji=_EMOJI_URGENCIA[urgencia_clave],
        titular_corto=_TITULAR_CORTO.get(patron, "en movimiento"),
        por_que_aparecio=_por_que_aparecio(candidato),
        patron=classification.etiqueta(patron),
        veredicto_temprano=candidato.resultado.temprano, veredicto_texto=veredicto_texto,
        entrada=niveles["entrada"] or 0.0, stop=niveles["stop"], objetivo=niveles["objetivo"],
        invalidacion=invalidacion, que_espero=_QUE_ESPERO.get(patron, "Que confirme la tesis en las próximas velas."),
        score=candidato.resultado.score_ajustado, catalizador=candidato.catalizador,
        fecha=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def formatear(o: Oportunidad) -> str:
    lineas = [f"{o.urgencia_emoji} {o.ticker} -- {o.titular_corto}", ""]
    if o.nombre:
        lineas.append(o.nombre)
        lineas.append("")

    lineas += o.por_que_aparecio
    lineas.append("")
    lineas.append(f"Patrón: {o.patron}")
    lineas.append(o.veredicto_texto)

    lineas += ["", f"Entro: ${o.entrada:,.2f}"]
    if o.stop is not None:
        lineas.append(f"Salgo (stop): ${o.stop:,.2f}")
    if o.objetivo is not None:
        lineas.append(f"Objetivo: ${o.objetivo:,.2f} -- reevalúo ahí, no es venta automática.")

    lineas += ["", f"Qué invalida esto: {o.invalidacion}"]
    lineas.append(f"Qué espero: {o.que_espero}")

    return "\n".join(l for l in lineas if l is not None)
