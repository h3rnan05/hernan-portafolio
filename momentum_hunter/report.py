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

# --- ¿Por qué esta alerta y no otra? -- por nivel de exigencia cumplido ---
def _por_que_esta_alerta(score_ajustado: float) -> str:
    if score_ajustado >= 95.0:
        return "De todo lo que vi hoy, esta es de las mejores oportunidades."
    if score_ajustado >= 90.0:
        return "Es una oportunidad sólida -- cumple todo lo que exijo antes de avisarte."
    return "Cumple lo mínimo que exijo para avisarte, pero no es excepcional."


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


def construir_oportunidad(candidato: CandidatoIntradia, techo_velas: int) -> Oportunidad:
    """Ensambla la `Oportunidad` final -- ya se decidió que se manda
    (`candidato.resultado.accionable`); esto solo decide CÓMO se
    presenta, traduciendo todo a lenguaje humano."""
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
        por_que_esta_alerta=_por_que_esta_alerta(candidato.resultado.score_ajustado),
        entrada=niveles["entrada"] or 0.0, stop=niveles["stop"], objetivo=niveles["objetivo"],
        invalidacion=invalidacion, catalizador=candidato.catalizador,
        score=candidato.resultado.score_ajustado, fecha=ahora.isoformat(timespec="seconds"),
        patron_clave=patron, hora_utc=ahora.hour,
        catalizador_tipo=candidato.catalizador.tipo if candidato.catalizador else None,
        float_acciones=candidato.meta.shares_float, gap_pct=candidato.factores.gap_pct,
        rvol=candidato.factores.rvol_actual,
    )


def formatear(o: Oportunidad) -> str:
    lineas = [f"{o.urgencia_emoji} {o.ticker} -- {o.titular_corto}", ""]
    if o.nombre:
        lineas.append(o.nombre)
        lineas.append("")

    lineas.append(f"1) {o.que_paso}")
    lineas.append(f"2) {o.que_hizo_mercado}")
    lineas.append(f"3) {o.que_pasa_ahora}")
    respuesta = "Sí" if o.vale_la_pena else "No"
    lineas.append(f"4) ¿Todavía vale la pena? {respuesta}. {o.por_que_vale_la_pena}")

    lineas += ["", o.por_que_esta_alerta]

    lineas += ["", f"Si decides entrar: cerca de ${o.entrada:,.2f}."]
    if o.stop is not None:
        lineas.append(f"Si te equivocas, sal cerca de ${o.stop:,.2f}.")
    if o.objetivo is not None:
        lineas.append(f"Si funciona, la primera meta es ${o.objetivo:,.2f}.")

    lineas += ["", o.invalidacion]

    if o.catalizador is not None:
        lineas += ["", f'Fuente: "{o.catalizador.titular}" ({o.catalizador.fuente})']

    return "\n".join(lineas)
