"""Formato de alerta -- pivote 2026-07-26: "cuando yo abra Telegram no
quiero recibir un reporte técnico... quiero recibir la misma
información que me daría un trader experimentado". Prohibido mostrar
cualquier indicador crudo (RVOL, EMA9, VWAP, ATR, MACD, RSI) o el nombre
del patrón en jerga -- todo se calcula igual que antes
(`factors/intradia.py`, `classification.py`, `early_opportunity.py`,
`evaluator.py`), pero esta capa lo TRADUCE a una historia de 4 pasos:

  1) ¿Qué pasó?              -- el catalizador, en una frase.
  2) ¿Qué hizo el mercado?    -- volumen/gap, sin números de indicadores.
  3) ¿Qué está pasando ahora? -- el patrón, descrito en lenguaje llano.
  4) ¿Todavía vale la pena?   -- el veredicto del Early Opportunity Engine.

Más "¿qué cancelaría la idea?" y "¿por qué esta alerta y no otra?" --
las cinco preguntas que pidió el dueño del producto. Prueba antes de
escribir cualquier frase nueva aquí: "si mi papá, que nunca ha hecho
trading, leyera esto, ¿entendería por qué vale la pena?" -- si la
respuesta es no, la frase está mal escrita, no le faltan más datos."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_hunter import classification
from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.models import FactoresIntradia, Oportunidad

_EMOJI_URGENCIA = {"muy_alta": "🔴", "alta": "🟠", "media": "🟡", "baja": "⚪"}
_NOMBRE_URGENCIA = {"muy_alta": "Muy Alta", "alta": "Alta", "media": "Media", "baja": "Baja"}

# --- 1) ¿Qué pasó? -- el catalizador, sin el nombre técnico del tipo ---
_CATALIZADOR_HUMANO = {
    "earnings": "Reportó resultados y el mercado reaccionó fuerte.",
    "fda": "La FDA le aprobó algo importante.",
    "contrato": "Ganó un contrato importante.",
    "nuevo_cliente": "Consiguió un cliente nuevo importante.",
    "guidance": "La empresa dijo que le va a ir mejor de lo que se esperaba.",
    "adquisicion": "Anunciaron una compra o fusión importante.",
    "patente": "Le aprobaron una patente importante.",
    "buyback": "La empresa anunció que va a recomprar sus propias acciones.",
    "insider_buying": "Alguien de adentro de la empresa está comprando acciones -- suelen saber algo que el resto no.",
    "regulatorio": "Un regulador le dio luz verde a algo importante para el negocio.",
    "upgrade_analista": "Un analista importante dijo que la acción debería subir.",
    "rumor": "Corre un rumor fuerte sobre la empresa, y ya lo confirmaron varias fuentes.",
}

# --- 3) ¿Qué está pasando ahora? -- el patrón, en una oración completa ---
_QUE_PASA_AHORA = {
    "high_tight_flag": "Subió muy rápido y ahora casi no se mueve -- está tomando aire antes de "
                       "decidir para dónde sigue.",
    "gap_and_go": "Sigue subiendo sin parar desde que abrió el mercado.",
    "opening_range_breakout": "Acaba de romper el techo que tenía en los primeros minutos de la sesión.",
    "bull_flag": "Subió fuerte, descansó un poco, y está a punto de intentarlo de nuevo.",
    "micro_pullback": "Está haciendo su primer descanso antes de intentar subir otra vez.",
    "trend_continuation": "Sigue subiendo de forma constante, sin señales de que se esté deteniendo.",
}

# --- 4) ¿Todavía vale la pena? -- según early_opportunity.razon. Sin el
# "Sí"/"No" al inicio: `formatear()` ya antepone esa respuesta, así que
# aquí solo va la razón (si no, queda "Sí. Sí. Apenas..." duplicado). ---
_VALE_LA_PENA = {
    "ok": "Apenas lleva unos minutos formando este movimiento -- todavía se puede entrar a buen precio.",
    "extension": "Ya subió demasiado rápido -- entrar ahora sería perseguir el precio.",
    "velas": "El movimiento principal ya pasó hace rato.",
}

# --- Ventana estimada de vigencia (refinamiento "Head Trader", punto 4).
# Minutos editoriales por tipo de jugada, acotados por el tiempo que
# queda de sesión; None = "hasta el cierre". HONESTIDAD: hoy son valores
# editoriales fijos y documentados -- el pedido pide basarlos en el
# comportamiento histórico de patrones similares, y ese historial
# todavía no existe; cuando el tracker acumule suficientes casos, la
# calibración será una propuesta medida que el humano aprueba (Principio
# 8), nunca un ajuste silencioso. El propio texto del mensaje lo dice
# ("estimación, no una promesa"). ---
_VENTANA_MINUTOS: dict[str, int | None] = {
    "gap_and_go": 30,
    "opening_range_breakout": 30,
    "bull_flag": 30,
    "micro_pullback": 15,
    "high_tight_flag": 15,
    "trend_continuation": None,
}
_HORA_CIERRE_UTC = 20.0


def _ventana_texto(patron: str, hora_utc: float | None) -> str:
    if hora_utc is None:
        return ""
    minutos_a_cierre = max(0.0, (_HORA_CIERRE_UTC - hora_utc) * 60.0)
    base = _VENTANA_MINUTOS.get(patron)
    minutos = minutos_a_cierre if base is None else min(float(base), minutos_a_cierre)
    if minutos <= 0:
        return "La sesión ya está cerrando -- casi no queda ventana."
    if base is None and minutos_a_cierre > 60:
        etiqueta = "hasta el cierre"
    elif minutos <= 15:
        etiqueta = "≈15 minutos"
    elif minutos <= 30:
        etiqueta = "≈30 minutos"
    elif minutos <= 60:
        etiqueta = "≈1 hora"
    else:
        etiqueta = "hasta el cierre"
    return f"Ventana estimada: {etiqueta} (estimación por tipo de jugada, no una promesa)."


# --- Qué espero ver después (punto 6): las señales que confirmarían la
# tesis en los próximos minutos, y las que dirían que está fallando.
# Sin jerga -- "su precio promedio del día" en vez de VWAP. ---
_SEÑALES_CONFIRMAN = {
    "gap_and_go": ["que aguante arriba del nivel que rompió", "nuevos máximos en las próximas velas",
                   "volumen creciendo, no apagándose"],
    "opening_range_breakout": ["que no vuelva a meterse en el rango de los primeros minutos",
                               "nuevos máximos", "volumen acompañando"],
    "bull_flag": ["que rompa el techo de la pausa con volumen", "que el respiro no se convierta en caída"],
    "micro_pullback": ["que aguante arriba de su promedio de corto plazo", "un nuevo intento de máximo",
                       "volumen regresando en la subida"],
    "high_tight_flag": ["que rompa el techo de la consolidación", "que la pausa siga siendo angosta"],
    "trend_continuation": ["que siga haciendo máximos y mínimos ascendentes",
                           "que se mantenga arriba de su precio promedio del día"],
}
_SEÑALES_FALLAN = {
    "gap_and_go": ["que vuelva a caer debajo del nivel que rompió", "volumen muriéndose",
                   "varias velas rojas seguidas"],
    "opening_range_breakout": ["que regrese dentro del rango de apertura", "volumen apagándose"],
    "bull_flag": ["que la pausa se rompa hacia abajo", "volumen vendedor creciendo"],
    "micro_pullback": ["que pierda su promedio de corto plazo", "que el rebote no llegue con volumen"],
    "high_tight_flag": ["que la consolidación se rompa hacia abajo", "que el rango se ensanche con velas rojas"],
    "trend_continuation": ["que pierda su precio promedio del día", "varias velas rojas seguidas"],
}


def _por_que_unica(candidato: CandidatoIntradia, stop: float | None) -> list[str]:
    """Qué reunió esta candidata que las demás no (punto 3) -- cada
    línea existe solo si la condición REALMENTE se verificó en el
    evaluador/niveles; nunca es una lista fija de marketing."""
    r = candidato.resultado
    razones: list[str] = []
    if candidato.catalizador is not None and candidato.catalizador.confirmado:
        razones.append("una noticia real y confirmada detrás del movimiento")
    if r.dinero_entrando:
        razones.append("el dinero está entrando ahora mismo, no hace horas")
    if r.desequilibrio:
        razones.append("pocas acciones disponibles para tanta demanda")
    elif candidato.es_large_cap and r.patron is not None:
        # Large-cap (2026-08-07): no hay desequilibrio de float que
        # explicar -- la razón honesta es otra: el precio de una empresa
        # grande y conocida ya está confirmando la noticia con un
        # movimiento real, no solo la noticia sola.
        razones.append("una empresa grande ya reaccionando con fuerza real en el precio, no solo la noticia sola")
    if r.patron is not None and r.temprano:
        razones.append("el movimiento apenas está empezando, no lo estamos persiguiendo")
    if stop is not None:
        razones.append("una salida cercana y clara si se equivoca")
    return razones


# --- ¿Por qué esta alerta y no otra? -- Principio 4: la competencia
# relativa es un hecho verificable (cuántas candidatas se evaluaron en
# esta corrida), no una frase de entusiasmo. ---
def _por_que_esta_alerta(score_ajustado: float, n_evaluados: int) -> str:
    if n_evaluados > 1:
        return (f"Hoy evalué {n_evaluados} candidatas e intenté descartarlas todas. "
                "Esta fue la que mejor sobrevivió.")
    if n_evaluados == 1:
        return ("Fue la única candidata que llegó a la evaluación final hoy -- y aun así "
                "tuvo que sobrevivir todos mis intentos de descartarla.")
    # Sin conteo disponible (llamada directa en pruebas/preview): se cae
    # al texto por nivel de exigencia, sin inventar una competencia.
    if score_ajustado >= 95.0:
        return "De todo lo que vi hoy, esta es de las mejores oportunidades."
    return "Cumple todo lo que exijo antes de avisarte."


def _que_paso(candidato: CandidatoIntradia) -> str:
    c = candidato.catalizador
    if c is None:
        return "Algo llamó la atención del mercado."
    frase = _CATALIZADOR_HUMANO.get(c.tipo, "Pasó algo importante con la empresa.")
    if candidato.minutos_desde_catalizador is not None:
        return f"Hace {int(candidato.minutos_desde_catalizador)} min: {frase}"
    return frase


def _que_hizo_mercado(f: FactoresIntradia) -> str:
    if f.gap_pct is not None and f.gap_pct >= 0.08:
        base = "Abrió mucho más arriba de lo normal"
    else:
        base = "Empezaron a entrar compradores"
    if f.aceleracion_volumen is not None and f.aceleracion_volumen >= 1.3:
        return f"{base} y el dinero está entrando cada vez con más fuerza."
    if f.rvol_actual is not None and f.rvol_actual >= 3.0:
        return f"{base} con muchísimo más volumen de lo normal."
    return f"{base}."


def _nivel_invalidacion(candidato: CandidatoIntradia) -> float | None:
    """El precio que, de perderse, cancela la tesis -- solo el número,
    nunca el nombre técnico del nivel (VWAP/EMA9/etc.)."""
    f = candidato.factores
    patron = candidato.resultado.patron
    if patron == "gap_and_go" and f.maximo_premarket is not None:
        return f.maximo_premarket
    if patron == "opening_range_breakout" and f.rango_apertura_max is not None:
        return f.rango_apertura_max
    if f.ema9 is not None:
        return f.ema9
    return f.vwap


def niveles_entrada_salida(factores: FactoresIntradia, atr_diario: float | None) -> dict[str, float | None]:
    """Stop por debajo del ancla intradía más cercana (VWAP/EMA9) --
    nunca un porcentaje fijo inventado. Si ninguna de las dos está
    disponible, cae al ATR diario de la etapa 1 como margen aproximado.
    Objetivo = 2R, la misma referencia de riesgo/recompensa que ya usa
    `early_opportunity._score_riesgo_recompensa`. Pública porque
    `run.py` necesita estos mismos niveles ANTES de construir la
    `Oportunidad` final -- para pasárselos a `evaluator.evaluar` (la
    pregunta 5 los usa para calcular riesgo/recompensa)."""
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


def construir_oportunidad(
    candidato: CandidatoIntradia, techo_velas: int,
    probabilidad_historica: str = "", advertencias: list[str] | None = None,
    n_evaluados: int = 0, rank: int = 0, n_universo: int = 0,
    confianza_texto: str = "", calidad_historica: str = "",
    hora_utc: float | None = None,
) -> Oportunidad:
    """Ensambla la `Oportunidad` final -- ya se decidió que se manda
    (accionable + sobrevivió al abogado del diablo + la última
    pregunta); esto solo decide CÓMO se presenta, traduciendo todo a
    lenguaje humano.

    `probabilidad_historica` viene de `memoria.frase_probabilidad`
    (Principio 3), `advertencias` son las objeciones NO fatales que
    sobrevivieron del debate de `skeptic.py` (Principios 2/11 -- el
    usuario decide con los riesgos a la vista), `n_evaluados` es contra
    cuántas candidatas compitió (Principio 4), `rank`/`n_universo` es su
    lugar del día contra el universo escaneado, `confianza_texto`/
    `calidad_historica` vienen de `memoria.confianza`/`memoria.
    linea_calidad`, y `hora_utc` alimenta la ventana estimada de
    vigencia."""
    patron = candidato.resultado.patron or "trend_continuation"
    urgencia_clave = _urgencia(candidato, techo_velas)
    niveles = niveles_entrada_salida(candidato.factores, candidato.atr_diario)
    nivel_inval = _nivel_invalidacion(candidato)

    early = candidato.resultado.early
    razon = early.razon if early is not None else "ok"

    invalidacion = (
        f"Si vuelve a caer por debajo de ${nivel_inval:.2f}, se cancela la idea."
        if nivel_inval is not None
        else "Si pierde el nivel que activó la entrada, se cancela la idea."
    )

    ahora = datetime.now(UTC)
    return Oportunidad(
        ticker=candidato.ticker, nombre=candidato.nombre,
        urgencia=_NOMBRE_URGENCIA[urgencia_clave], urgencia_emoji=_EMOJI_URGENCIA[urgencia_clave],
        titular_corto=classification.DESCRIPCION_HUMANA.get(patron, "en movimiento"),
        que_paso=_que_paso(candidato), que_hizo_mercado=_que_hizo_mercado(candidato.factores),
        que_pasa_ahora=_QUE_PASA_AHORA.get(patron, "Sigue moviéndose con fuerza."),
        vale_la_pena=candidato.resultado.temprano,
        por_que_vale_la_pena=_VALE_LA_PENA.get(razon, _VALE_LA_PENA["ok"]),
        por_que_esta_alerta=_por_que_esta_alerta(candidato.resultado.score_ajustado, n_evaluados),
        entrada=niveles["entrada"] or 0.0, stop=niveles["stop"], objetivo=niveles["objetivo"],
        invalidacion=invalidacion, catalizador=candidato.catalizador,
        score=candidato.resultado.score_ajustado, fecha=ahora.isoformat(timespec="seconds"),
        patron_clave=patron, hora_utc=ahora.hour,
        catalizador_tipo=candidato.catalizador.tipo if candidato.catalizador else None,
        float_acciones=candidato.meta.shares_float, gap_pct=candidato.factores.gap_pct,
        rvol=candidato.factores.rvol_actual,
        probabilidad_historica=probabilidad_historica,
        advertencias=list(advertencias or []), n_evaluados=n_evaluados,
        rank=rank, n_universo=n_universo,
        por_que_unica=_por_que_unica(candidato, niveles["stop"]),
        confianza_texto=confianza_texto, calidad_historica=calidad_historica,
        ventana_texto=_ventana_texto(patron, hora_utc),
        señales_confirman=_SEÑALES_CONFIRMAN.get(patron, []),
        señales_fallan=_SEÑALES_FALLAN.get(patron, []),
    )


def formatear(o: Oportunidad) -> str:
    lineas = [f"{o.urgencia_emoji} {o.ticker} -- {o.titular_corto}"]
    if o.rank and o.n_universo:
        lineas.append(f"La #{o.rank} del día entre {o.n_universo:,} acciones escaneadas.")
    lineas.append("")
    if o.nombre:
        lineas.append(o.nombre)
        lineas.append("")

    lineas.append(f"1) {o.que_paso}")
    lineas.append(f"2) {o.que_hizo_mercado}")
    lineas.append(f"3) {o.que_pasa_ahora}")
    respuesta = "Sí" if o.vale_la_pena else "No"
    lineas.append(f"4) ¿Todavía vale la pena? {respuesta}. {o.por_que_vale_la_pena}")

    lineas += ["", o.por_que_esta_alerta]
    if o.por_que_unica:
        lineas.append("Fue la que reunió todo a la vez:")
        lineas += [f"✔ {r}" for r in o.por_que_unica]

    if o.confianza_texto:
        lineas += ["", o.confianza_texto]
        if o.calidad_historica:
            lineas.append(o.calidad_historica)
    elif o.probabilidad_historica:
        lineas += ["", o.probabilidad_historica]

    if o.ventana_texto:
        lineas += ["", o.ventana_texto]

    lineas += ["", f"Si decides entrar: cerca de ${o.entrada:,.2f}."]
    if o.stop is not None:
        lineas.append(f"Si te equivocas, sal cerca de ${o.stop:,.2f}.")
    if o.objetivo is not None:
        lineas.append(f"Si funciona, la primera meta es ${o.objetivo:,.2f}.")

    lineas += ["", o.invalidacion]

    if o.señales_confirman:
        lineas += ["", "Si esto es bueno, en los próximos minutos espero ver:"]
        lineas += [f"✔ {s}" for s in o.señales_confirman[:3]]
    if o.señales_fallan:
        lineas.append("Y me preocuparía ver:")
        lineas += [f"✘ {s}" for s in o.señales_fallan[:3]]

    if o.advertencias:
        lineas += ["", "Qué podría salir mal:"]
        lineas += [f"• {a}" for a in o.advertencias[:3]]

    if o.catalizador is not None:
        lineas += ["", f'Fuente: "{o.catalizador.titular}" ({o.catalizador.fuente})']

    lineas += ["", "La decisión de operar siempre es tuya -- yo solo investigo y aviso."]
    return "\n".join(lineas)
