"""Opportunity Hunter -- el screener deja de ser "reportar qué pasó el
filtro" y pasa a "cazar oportunidades excepcionales". Corre inmediatamente
después del screener diario (`screener/run.py`), reutilizando los mismos
datos ya calculados ese día (barras, sub_scores cross-sectional del
ranking completo, fundamentales, opciones) -- ningún cálculo nuevo salvo
la detección de patrones en sí.

Filosofía (pedido explícito, 2026-07-23): "Quiero que elimines el 99% del
ruido... una oportunidad solo debe enviarse cuando múltiples factores
independientes estén alineados... Prefiero recibir 2 oportunidades
excelentes por semana que 20 mediocres por día." Por eso cada patrón
exige 3-4 condiciones INDEPENDIENTES a la vez -- nunca un score alto
solo -- y si nada dispara, la respuesta del día es explícitamente "no
encontré ninguna oportunidad", un no-resultado válido y esperado (mismo
espíritu que el Decision Engine del ROADMAP: "no trades today" es una
salida legítima, nunca se fuerza una).

100% determinístico, sin LLM: cada patrón es una combinación fija de
umbrales sobre datos ya reales -- los mismos percentiles cross-sectional
0-100 que ya calcula `scoring.puntuar()` (momentum/tendencia/calidad/
valor/liquidez), y ATR/SMA/RSI/máximo de 52 semanas/volumen real de
`screener.factors.technical` sobre las barras ya descargadas. "Convicción
del modelo" reusa esos mismos sub_scores -- nunca un número inventado.

Patrones de fase 1 -- los que son honestamente calculables HOY con datos
gratis de yfinance (deliberadamente NO incluidos todavía, anotados para
una fase 2: "earnings sorpresa + guía al alza" porque no hay historial
estructurado de guidance, y "volumen de opciones inusual" porque no hay
histórico de IV/volumen de opciones recolectado -- ver el docstring de
`screener/options_ideas.py` sobre por qué IV Rank tampoco existe todavía):

- **Ruptura confirmada**: precio en/cerca del máximo de 52 semanas +
  volumen inusual + tendencia alcista fuerte (medias alineadas) +
  valuación no exigente.
- **Pullback sano**: tendencia alcista fuerte + precio corrigiendo hacia
  la media de 50 días SIN romperla + RSI en zona saludable (ni
  sobrecomprado ni en pánico) + negocio de calidad o creciendo.
- **Infravalorada con impulso**: barata + calidad + momentum ya positivo
  -- los tres percentiles cross-sectional del día, sin depender de
  historial de días anteriores (evita el problema de que
  `shortlist_hoy.json` solo persiste el Top N, no el universo completo).

Cada ticker dispara COMO MÁXIMO un patrón (el primero que aplica, en
orden ruptura > pullback > valor_impulso) para no mandar dos alertas del
mismo ticker el mismo día.

Refinamiento tras el primer envío real (feedback directo, 2026-07-25):
"Comprar hoy" ya no puede quedar sin explicar su relación con el precio
-- el mensaje ahora dice explícitamente que el precio de hoy YA está
dentro de la zona que activó el patrón (ver `_precio_actual_texto` y el
bloque de decisión en `formatear_oportunidad`); esa reconciliación es
posible porque, en el pipeline actual (una sola corrida por día que
calcula y envía en el mismo paso), el precio con el que se detectó el
patrón es siempre el mismo que se muestra en el mensaje. Si en el futuro
existe un segundo proceso que vigile el mercado en vivo (alertas
intradía, explícitamente NO construido todavía -- ver el "próximo gran
paso" que describió el dueño del producto), ahí sí puede haber una
brecha real entre "cuándo se detectó" y "cuándo se lee el mensaje", y
esta lógica tendría que revisarse. "Liquidez baja" y "esperar por
resultados" ahora explican el POR QUÉ en prosa en vez de una frase
telegráfica. Cada oportunidad también dice qué está esperando el modelo
para confirmar la tesis (`que_espero`), qué cambió desde ayer usando las
mismas barras ya descargadas menos el último día (`_que_cambio` -- nunca
un dato nuevo), y por qué eligió esa estrategia de opciones sobre
comprar acciones directamente (`_por_que_estrategia`, mismo espíritu que
el diccionario fijo `_VS_ACCIONES` de `telegram_bot/trade_command.py`,
pero definido aquí mismo para no invertir la dependencia natural
screener → telegram_bot).

Segundo refinamiento (feedback directo, 2026-07-25): un tope diario de
`LIMITE_DIARIO` oportunidades (las de mayor Convicción, si hubiera más
detectadas) -- pedido explícito: "prefiero recibir 2 oportunidades
excelentes por semana que 20 mediocres por día", y en la práctica un
día llegó a mandar 4. Esto es un tope DIARIO, no semanal: un tope
semanal de verdad requeriría persistir qué tickers ya se mandaron esta
semana para no repetirlos -- explícitamente NO construido todavía (el
"próximo gran paso" que describió el dueño del producto es un segundo
proceso que vigile el mercado en vivo; un tracking semanal iría de la
mano de eso). Para "Comprar hoy" el mensaje ahora incluye un plan
ejecutable ("🎯 Mi plan": tipo de estrategia, precio máximo que pagaría
-- spot + un cuarto del ATR real del papel, nunca un número fijo en
dólares -- y qué haría si mañana abre por encima, ver `_precio_maximo`)
y un checklist de confirmación (`_checklist`). El checklist es
DELIBERADAMENTE un resumen de lo que la detección del patrón y
`_decision()` YA verificaron -- nunca un filtro paralelo con sus
propios criterios. Aplicar, por ejemplo, "valoración atractiva" como
condición universal habría rechazado rupturas legítimas (un breakout
fuerte no tiene por qué ser barato) -- por eso el checklist es
específico por patrón. "Sin noticias negativas de alta relevancia"
aparece siempre marcado como no disponible: verificarlo de verdad
exigiría o bien un clasificador de sentimiento no-LLM (no existe) o
invocar el `news_analyst` existente (que SÍ usa LLM) por cada
oportunidad, lo que rompería la garantía de este pipeline de correr
100% sin LLM -- deliberadamente no implementado, anotado en vez de
fingir que se revisó. "Riesgo por operación" reusa el 1% ya configurado
en `risk_manager.config.RiskLimits.riesgo_por_trade_pct` (el mismo
límite real del Risk Manager) en vez de calcular un monto en dólares
atado a un capital específico -- la idea de adaptar montos al capital
real del usuario sigue explícitamente fuera de alcance (ver
`telegram_bot/trade_command.py`).

Tercer refinamiento (feedback directo, 2026-07-25): el tope diario se
movió de `buscar_oportunidades` (que ahora devuelve TODAS las
detectadas, ordenadas por Convicción, sin recortar) a
`mensaje_oportunidades` -- separa detección/ranking de presentación, y
permite que el "🏁 Resumen del día" final mencione las oportunidades que
no entraron en el detalle en vez de descartarlas en silencio ("Las
demás las vigilaría, pero no abriría posición hoy"). "Qué cambió hoy"
ahora aparece SIEMPRE (con "Sin cambios importantes. La tesis sigue
intacta." cuando no hay deltas reales que reportar) en vez de omitirse
-- pedido explícito: "así sabes inmediatamente por qué apareció". El
emoji de urgencia baja pasó de 🟢 a ⚪: verde sugería "alta urgencia,
actúa ya" (como un semáforo), justo lo contrario de lo que "Baja"
significa aquí. "Capital" se reorganizó en Acciones/Opciones en vez de
"Capital mínimo... o $X". Deliberadamente NO implementado (pedido
explícito del dueño del producto de dejar de agregar indicadores y
enfocarse en calidad de señal + medir desempeño): un índice de
calidad/ranking con letras (A/B/C) -- sería redundante con Convicción +
el checklist, que ya muestran exactamente los mismos factores."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from risk_manager.config import RiskLimits
from screener.data.provider import Barras, Fundamentales
from screener.factors import technical as tech
from screener.options_ideas import obtener_cadena, proxima_fecha_resultados
from screener.options_strategies import construir_estrategias, direccion_estrategia, rankear
from screener.scoring import Puntuacion

log = logging.getLogger("screener.opportunity_hunter")

SIN_OPORTUNIDADES = (
    "Hoy no encontré ninguna oportunidad que cumpla mis estándares. "
    "No abriría ninguna posición."
)

SEP = "━━━━━━━━━━━━━━━━━━"

# Umbrales de cada patrón -- fijos y documentados, nunca ajustados por
# ticker ni por un LLM. Cambiarlos es una decisión de producto explícita,
# no un parámetro que el modelo "aprende".
UMBRAL_RUPTURA_PROXIMIDAD = 0.98        # % del máximo de 52 semanas
UMBRAL_VOLUMEN_INUSUAL = 1.5            # x el promedio de 20 días
UMBRAL_PULLBACK_BANDA = 0.03            # ±3% de la media de 50 días
RSI_PULLBACK_MIN, RSI_PULLBACK_MAX = 40.0, 60.0
UMBRAL_VALOR_BARATA = 70.0              # percentil cross-sectional
UMBRAL_CALIDAD_SOLIDA = 60.0
UMBRAL_MOMENTUM_FUERTE = 70.0
UMBRAL_LIQUIDEZ_MINIMA = 20.0
DIAS_EARNINGS_EVITAR = 7
UMBRAL_CAMBIO_RSI = 3.0                 # puntos de RSI para considerarlo "cambió"
UMBRAL_CAMBIO_DISTANCIA_SMA50 = 0.002   # 0.2pp de acercamiento mínimo para reportarlo
BUFFER_PRECIO_MAXIMO_ATR = 0.25         # un cuarto del ATR real del papel, no un $ fijo
LIMITE_DIARIO = 3                       # tope diario -- "2-3 excelentes, no 20 mediocres"

_URGENCIA = {"ruptura": "Alta", "pullback": "Media", "valor_impulso": "Baja"}
_URGENCIA_EMOJI = {"Alta": "🔴", "Media": "🟡", "Baja": "⚪"}
_URGENCIA_MOTIVO = {
    "ruptura": "Los breakouts con volumen fuerte tienden a extenderse rápido -- "
               "esperar unos días podría significar perderse la mayor parte del movimiento.",
    "pullback": "Una corrección sana suele dar unos días de margen antes de que la "
                "tendencia retome fuerza.",
    "valor_impulso": "Una acción barata con momentum ya positivo no suele revertirse "
                     "de un día para otro.",
}
_DECISION_EMOJI = {"Comprar hoy": "🟢", "Esperar": "🟡", "No operar": "❌"}

# Por qué esta estrategia de opciones y no otra -- mismo espíritu que
# `telegram_bot/trade_command._VS_ACCIONES` (ventajas/desventajas reales
# frente a comprar la acción directamente), copia local para no invertir
# la dependencia natural screener → telegram_bot. Solo cubre estrategias
# no-bajistas: son las únicas que `_estrategia_recomendada` puede elegir
# (los 3 patrones de fase 1 son estructuralmente alcistas).
_POR_QUE_ESTRATEGIA = {
    "Long Call": [
        "Necesita mucho menos capital que comprar {ticker} directamente "
        "(${capital_estrategia} vs. ${capital_acciones}).",
        "La pérdida máxima está limitada a la prima pagada.",
        "Si {ticker} sube con fuerza, el rendimiento puede ser proporcionalmente "
        "mayor que comprando acciones.",
    ],
    "Bull Call Spread": [
        "Necesita menos capital que comprar {ticker} directamente "
        "(${capital_estrategia} vs. ${capital_acciones}).",
        "La pérdida máxima está limitada a lo que se paga por el spread.",
        "El objetivo esperado no requiere una subida enorme para que el spread "
        "gane el máximo.",
    ],
    "Bull Put Spread": [
        "No requiere comprar acciones -- cobras una prima al abrir en vez de pagarla.",
        "Gana incluso si {ticker} se queda igual o sube, no solo si sube con fuerza.",
        "El riesgo máximo (${capital_estrategia}) puede superar varias veces la "
        "prima cobrada si {ticker} cae con fuerza.",
    ],
    "Covered Call": [
        "Requiere poseer (o comprar) 100 acciones -- capital similar a comprarlas "
        "directamente (${capital_acciones}).",
        "Genera ingreso extra (la prima cobrada) mientras esperas.",
        "Limita cuánto puedes ganar si {ticker} sube con mucha fuerza.",
    ],
    "Cash Secured Put": [
        "Requiere reservar casi el mismo capital que comprar las acciones "
        "(${capital_estrategia} vs. ${capital_acciones}).",
        "Cobras una prima incluso si {ticker} no se mueve o sube.",
        "Si {ticker} cae con fuerza, terminas comprando por encima del precio de mercado.",
    ],
    "Iron Condor": [
        "No requiere tener una posición direccional en {ticker}.",
        "Gana si {ticker} se queda dentro de un rango en vez de necesitar que suba.",
        "El riesgo máximo (${capital_estrategia}) puede superar varias veces la "
        "prima cobrada si se mueve fuerte en cualquier dirección.",
    ],
}


@dataclass(frozen=True)
class Oportunidad:
    ticker: str
    nombre: str | None
    patron: str                 # "ruptura" | "pullback" | "valor_impulso"
    conviccion: int             # 0-100, reusa los sub_scores del día
    urgencia: str                # "Alta" | "Media" | "Baja"
    decision: str                 # "Comprar hoy" | "Esperar" | "No operar"
    motivo_decision: str | None
    que_ocurrio: str
    que_invalida: str
    que_espero: str
    que_cambio: list[str]
    entrada: float
    precio_actual_texto: str
    stop: float | None
    objetivo: float | None
    horizonte_dias: int | None
    capital_acciones: float
    capital_estrategia: float | None
    estrategia_nombre: str | None
    estrategia_por_que: list[str]
    precio_maximo: float | None
    checklist: list[str]


def _volumen_ratio(b: Barras, ventana: int = 20) -> float | None:
    """Volumen de la última barra vs. el promedio de las `ventana`
    anteriores -- de las mismas barras ya descargadas, ningún dato nuevo."""
    if len(b.volume) < ventana + 1:
        return None
    anteriores = b.volume[-ventana - 1:-1]
    promedio = sum(anteriores) / len(anteriores)
    return b.volume[-1] / promedio if promedio > 0 else None


def _bars_sin_ultima(b: Barras) -> Barras | None:
    """Las mismas barras ya descargadas, sin el último día -- una
    aproximación honesta de "cómo se veía esto ayer" sin pedir ningún
    dato nuevo. None si no hay suficiente historia para que tenga sentido."""
    if len(b.close) < 20:
        return None
    return Barras(b.ticker, b.fechas[:-1], b.close[:-1], b.high[:-1], b.low[:-1], b.volume[:-1])


def _detectar_ruptura(b: Barras) -> bool:
    prox = tech.proximidad_maximo_52s(b)
    vol_ratio = _volumen_ratio(b)
    tendencia = tech.score_tendencia(b)
    return (
        prox is not None and prox >= UMBRAL_RUPTURA_PROXIMIDAD
        and vol_ratio is not None and vol_ratio >= UMBRAL_VOLUMEN_INUSUAL
        and tendencia == 3.0
    )


def _detectar_pullback(b: Barras, sub: dict[str, float], fund: Fundamentales) -> bool:
    tendencia = tech.score_tendencia(b)
    sma50 = tech.sma(b.close, 50)
    rsi = tech.rsi(b)
    spot = b.close[-1] if b.close else None
    if tendencia != 3.0 or sma50 is None or rsi is None or spot is None or sma50 <= 0:
        return False
    dentro_de_banda = abs(spot - sma50) / sma50 <= UMBRAL_PULLBACK_BANDA
    rsi_sano = RSI_PULLBACK_MIN <= rsi <= RSI_PULLBACK_MAX
    calidad = sub.get("calidad")
    negocio_solido = (calidad is not None and calidad >= UMBRAL_CALIDAD_SOLIDA) or \
        (fund.crecimiento_ingresos is not None and fund.crecimiento_ingresos >= 0.10)
    return dentro_de_banda and rsi_sano and negocio_solido


def _detectar_valor_impulso(sub: dict[str, float]) -> bool:
    valor, calidad, momentum = sub.get("valor"), sub.get("calidad"), sub.get("momentum")
    return (
        valor is not None and valor >= UMBRAL_VALOR_BARATA
        and calidad is not None and calidad >= UMBRAL_CALIDAD_SOLIDA
        and momentum is not None and momentum >= UMBRAL_MOMENTUM_FUERTE
    )


def detectar_patron(b: Barras, sub: dict[str, float], fund: Fundamentales) -> str | None:
    """Único punto de entrada de detección -- como máximo un patrón por
    ticker, en orden de prioridad (ruptura > pullback > valor_impulso)."""
    if _detectar_ruptura(b):
        return "ruptura"
    if _detectar_pullback(b, sub, fund):
        return "pullback"
    if _detectar_valor_impulso(sub):
        return "valor_impulso"
    return None


def _conviccion(patron: str, sub: dict[str, float]) -> int:
    """Reusa los mismos sub_scores 0-100 cross-sectional que ya calcula
    scoring.puntuar() para ESTE patrón -- nunca un número inventado.
    Pondera los factores más relevantes para por qué este patrón en
    particular importa."""
    pesos = {
        "ruptura": (("momentum", 0.5), ("tendencia", 0.3), ("liquidez", 0.2)),
        "pullback": (("tendencia", 0.4), ("calidad", 0.3), ("valor", 0.3)),
        "valor_impulso": (("valor", 0.5), ("calidad", 0.3), ("momentum", 0.2)),
    }[patron]
    disponibles = [(sub.get(f), w) for f, w in pesos if sub.get(f) is not None]
    if not disponibles:
        return 50
    peso_total = sum(w for _, w in disponibles)
    return round(sum(v * w for v, w in disponibles) / peso_total)


def _que_ocurrio_invalida_y_espero(
    patron: str, ticker: str, b: Barras, sub: dict[str, float],
) -> tuple[str, str, str]:
    """Tercer elemento nuevo (feedback 2026-07-25): "que_espero" -- qué
    confirmación concreta busca el modelo para creer que la tesis sigue
    viva, distinto de "que_invalida" (qué la mataría)."""
    if patron == "ruptura":
        max52 = tech.maximo_52s(b)
        vol_ratio = _volumen_ratio(b)
        return (
            f"{ticker} rompe su máximo de 52 semanas (${max52:,.0f}) con un volumen "
            f"{vol_ratio:.1f}x el promedio de los últimos 20 días, dentro de una tendencia "
            f"alcista confirmada.",
            f"Si vuelve a cerrar debajo de ${max52:,.0f} en los próximos días, la ruptura se invalida.",
            f"Que {ticker} se mantenga sobre ${max52:,.0f} en los próximos días, confirmando "
            "que la ruptura tiene fuerza real y no es una falsa alarma.",
        )
    if patron == "pullback":
        sma50 = tech.sma(b.close, 50)
        rsi = tech.rsi(b)
        return (
            f"{ticker} corrige hacia su media de 50 días (${sma50:,.0f}) dentro de una "
            f"tendencia alcista fuerte, con RSI en zona saludable ({rsi:.0f}).",
            f"Si rompe con fuerza por debajo de ${sma50:,.0f}, o el RSI cae debajo de 30, "
            f"la corrección deja de ser sana.",
            "Que el rebote confirme el soporte de la media de 50 días en vez de romperla.",
        )
    valor, calidad, momentum = sub.get("valor"), sub.get("calidad"), sub.get("momentum")
    return (
        f"{ticker} está entre las más baratas del universo analizado hoy (percentil valor "
        f"{valor:.0f}/100), con negocio de calidad (percentil {calidad:.0f}/100) y momentum "
        f"ya positivo (percentil {momentum:.0f}/100).",
        "Si el momentum se revierte o deja de estar entre las más baratas del universo, "
        "la tesis pierde base.",
        "Que el momentum se mantenga mientras siga entre las más baratas del universo.",
    )


def _que_cambio(
    b: Barras, ayer: Barras | None, en_shortlist_ayer: bool | None,
) -> list[str]:
    """Por qué apareció HOY y no otro día -- deltas reales contra "ayer"
    (las mismas barras sin el último día, ver `_bars_sin_ultima`; y la
    shortlist persistida del día anterior para "entró al Top 20", cuando
    se conoce). Nunca inventa un cambio: cada línea exige una diferencia
    numérica real por encima de un umbral mínimo, y si no hay suficiente
    historia para comparar, la lista queda vacía (se omite la sección
    entera en el mensaje, nunca se rellena)."""
    cambios: list[str] = []
    if en_shortlist_ayer is False:
        cambios.append("Entró al Top 20 del screener hoy.")
    if ayer is None:
        return cambios
    rsi_hoy, rsi_ayer = tech.rsi(b), tech.rsi(ayer)
    if rsi_hoy is not None and rsi_ayer is not None and abs(rsi_hoy - rsi_ayer) >= UMBRAL_CAMBIO_RSI:
        direccion = "bajó" if rsi_hoy < rsi_ayer else "subió"
        cambios.append(f"El RSI {direccion} de {rsi_ayer:.0f} a {rsi_hoy:.0f}.")
    sma50_hoy, sma50_ayer = tech.sma(b.close, 50), tech.sma(ayer.close, 50)
    if sma50_hoy and sma50_ayer and sma50_hoy > 0 and sma50_ayer > 0:
        dist_hoy = abs(b.close[-1] - sma50_hoy) / sma50_hoy
        dist_ayer = abs(ayer.close[-1] - sma50_ayer) / sma50_ayer
        if dist_hoy < dist_ayer - UMBRAL_CAMBIO_DISTANCIA_SMA50:
            cambios.append("Se acercó a su media de 50 días.")
    tendencia_hoy, tendencia_ayer = tech.score_tendencia(b), tech.score_tendencia(ayer)
    if tendencia_hoy == 3.0 and tendencia_ayer == 3.0:
        cambios.append("La tendencia de fondo se mantiene fuerte.")
    elif tendencia_hoy == 3.0 and tendencia_ayer != 3.0:
        cambios.append("La tendencia de fondo se confirmó hoy.")
    return cambios


def _objetivo(spot: float, max52: float | None, cancelar: float | None) -> float | None:
    """El máximo de 52 semanas si todavía queda meaningfully arriba del
    spot (nivel técnico real); si no (ej. ya estamos ahí, como en una
    ruptura), una extensión de igual distancia que el riesgo real hasta
    el stop -- la misma convención de "measured move" que ya usa
    /trade -- nunca un número inventado."""
    if max52 is not None and max52 > spot * 1.02:
        return max52
    if cancelar is not None:
        riesgo = spot - cancelar
        if riesgo > 0:
            return spot + riesgo
    return None


def _decision(sub_liquidez: float | None, dias_a_resultados: int | None) -> tuple[str, str | None]:
    """Explica el POR QUÉ en prosa (feedback 2026-07-25: "así entiendes
    inmediatamente por qué la descarta"), no una frase telegráfica."""
    if sub_liquidez is not None and sub_liquidez < UMBRAL_LIQUIDEZ_MINIMA:
        return "No operar", (
            "Aunque la empresa parece atractiva, las opciones tienen poca liquidez. "
            "Eso puede hacer difícil entrar o salir sin pagar un spread alto."
        )
    if dias_a_resultados is not None and 0 <= dias_a_resultados <= DIAS_EARNINGS_EVITAR:
        return "Esperar", (
            f"Resultados en {dias_a_resultados} días -- prefiero evitar la volatilidad "
            "que trae el reporte antes de abrir la posición."
        )
    return "Comprar hoy", None


def _dias_a_resultados(ticker: str) -> int | None:
    try:
        import yfinance as yf
        fecha = proxima_fecha_resultados(yf.Ticker(ticker))
    except Exception as e:
        log.debug("fecha de resultados de %s falló: %s", ticker, e)
        return None
    if not fecha:
        return None
    try:
        return (date.fromisoformat(fecha) - date.today()).days
    except ValueError:
        return None


def _estrategia_recomendada(ticker: str, spot: float) -> tuple[str | None, float | None, int | None]:
    """Cadena de opciones SOLO para el ticker que ya disparó un patrón
    (nunca para el universo completo -- son caras y frágiles). Filtra a
    estrategias de dirección no-bajista (los 3 patrones son
    estructuralmente alcistas) y toma la mejor por el ranking real de
    options_strategies.rankear() -- nunca cambia ese ranking."""
    try:
        cadena = obtener_cadena(ticker)
    except Exception as e:
        log.debug("cadena de opciones de %s falló: %s", ticker, e)
        return None, None, None
    if cadena is None:
        return None, None, None
    estrategias = rankear(construir_estrategias(cadena, spot))
    alineadas = [e for e in estrategias if direccion_estrategia(e.nombre) != "bajista"]
    if not alineadas:
        return None, None, None
    top = alineadas[0]
    return top.nombre, top.riesgo_maximo, cadena.dias_a_vencimiento


def _por_que_estrategia(
    nombre: str | None, ticker: str, capital_estrategia: float | None, capital_acciones: float,
) -> list[str]:
    """Ventajas reales de la estrategia elegida frente a comprar la
    acción directamente -- responde "¿por qué esta y no otra?" en vez de
    dejar el nombre de la estrategia parado sin explicación (feedback
    2026-07-25: "eso hace que la estrategia deje de parecer arbitraria")."""
    if not nombre or capital_estrategia is None:
        return []
    plantillas = _POR_QUE_ESTRATEGIA.get(nombre)
    if not plantillas:
        return []
    return [p.format(ticker=ticker, capital_estrategia=f"{capital_estrategia:,.0f}",
                     capital_acciones=f"{capital_acciones:,.0f}") for p in plantillas]


_CHECKLIST_PATRON = {
    "ruptura": ["Tendencia alcista fuerte confirmada", "Volumen inusual confirmando la ruptura"],
    "pullback": ["Tendencia alcista fuerte de fondo", "Negocio de calidad o creciendo ingresos"],
    "valor_impulso": ["Valoración atractiva frente al universo", "Negocio de calidad", "Momentum ya positivo"],
}


def _checklist(patron: str) -> list[str]:
    """Solo se arma para "Comprar hoy". Cada ítem ya fue exigido por la
    detección del patrón (los específicos, distintos por patrón) o por
    `_decision()` (liquidez, earnings, universales para los 3) ANTES de
    llegar aquí -- nunca puede aparecer una casilla sin marcar junto a
    "Comprar hoy". Es un resumen honesto de lo ya verificado, no un
    filtro paralelo con sus propios criterios: aplicar "valoración
    atractiva" como condición universal, por ejemplo, rechazaría rupturas
    legítimas -- un breakout fuerte no tiene por qué ser barato."""
    return (
        ["Precio dentro de la zona de entrada"]
        + _CHECKLIST_PATRON[patron]
        + ["Liquidez suficiente", f"Sin earnings en los próximos {DIAS_EARNINGS_EVITAR} días"]
    )


def _precio_maximo(spot: float, atr_val: float | None) -> float | None:
    """Hasta dónde pagaría por esta misma oportunidad si el precio se
    mueve antes de poder entrar -- un cuarto del ATR diario REAL del
    papel (más volátil = más margen, menos volátil = menos), nunca un
    número fijo en dólares. None si no hay ATR disponible (se omite en
    vez de inventar)."""
    if atr_val is None:
        return None
    return spot + BUFFER_PRECIO_MAXIMO_ATR * atr_val


def _precio_actual_texto(patron: str, spot: float, ayer: Barras | None, sma50: float | None) -> str:
    """El precio de hoy, con la distancia a un ancla real y no inventada
    cuando existe una que tenga sentido para el patrón: para "ruptura",
    cuánto superó su máximo de 52 semanas ANTERIOR (excluyendo la barra de
    hoy, si no siempre daría ~0% porque hoy mismo es el máximo); para
    "pullback", la distancia a la media de 50 días que definió la
    corrección. "valor_impulso" no tiene un ancla de precio natural (es
    una señal de valuación, no técnica) -- se omite en vez de inventar
    una."""
    if patron == "ruptura" and ayer is not None:
        max52_previo = tech.maximo_52s(ayer)
        if max52_previo and max52_previo > 0:
            pct = (spot - max52_previo) / max52_previo * 100
            return (f"${spot:,.0f} ({pct:+.1f}% sobre su máximo de 52 semanas "
                    f"anterior de ${max52_previo:,.0f})")
    if patron == "pullback" and sma50 and sma50 > 0:
        pct = (spot - sma50) / sma50 * 100
        return f"${spot:,.0f} ({pct:+.1f}% vs. su media de 50 días, ${sma50:,.0f})"
    return f"${spot:,.0f}"


def construir_oportunidad(
    p: Puntuacion, b: Barras, fund: Fundamentales, patron: str,
    tickers_shortlist_anterior: set[str] | None = None,
) -> Oportunidad:
    """Ensambla una Oportunidad ya detectada -- reutiliza los mismos
    niveles de precio (ATR/SMA50) que /trade y /report
    (`screener.factors.technical.niveles_precio`), nunca un cálculo
    paralelo. `tickers_shortlist_anterior` (los tickers del Top N de
    AYER, ya persistidos en shortlist_hoy.json antes de sobrescribirlo)
    es opcional -- si no se pasa, simplemente se omite el dato de "entró
    al Top 20 hoy" en vez de asumir nada."""
    spot = b.close[-1]
    sma50 = tech.sma(b.close, 50)
    atr_val = tech.atr(b)
    niveles = tech.niveles_precio(spot, atr_val, sma50)
    max52 = tech.maximo_52s(b)
    objetivo = _objetivo(spot, max52, niveles.get("cancelar"))
    que_ocurrio, que_invalida, que_espero = _que_ocurrio_invalida_y_espero(patron, p.ticker, b, p.sub)
    conviccion = _conviccion(patron, p.sub)
    dias_resultados = _dias_a_resultados(p.ticker)
    decision, motivo_decision = _decision(p.sub.get("liquidez"), dias_resultados)
    estrategia_nombre, capital_estrategia, horizonte_dias = _estrategia_recomendada(p.ticker, spot)
    capital_acciones = spot * 100
    estrategia_por_que = _por_que_estrategia(estrategia_nombre, p.ticker, capital_estrategia, capital_acciones)

    ayer = _bars_sin_ultima(b)
    precio_actual_texto = _precio_actual_texto(patron, spot, ayer, sma50)
    en_shortlist_ayer = (
        p.ticker in tickers_shortlist_anterior if tickers_shortlist_anterior is not None else None
    )
    que_cambio = _que_cambio(b, ayer, en_shortlist_ayer)
    precio_maximo = _precio_maximo(spot, atr_val)
    checklist = _checklist(patron) if decision == "Comprar hoy" else []

    return Oportunidad(
        ticker=p.ticker, nombre=p.nombre, patron=patron, conviccion=conviccion,
        urgencia=_URGENCIA[patron], decision=decision, motivo_decision=motivo_decision,
        que_ocurrio=que_ocurrio, que_invalida=que_invalida, que_espero=que_espero,
        que_cambio=que_cambio, entrada=spot, precio_actual_texto=precio_actual_texto,
        stop=niveles.get("cancelar"), objetivo=objetivo, horizonte_dias=horizonte_dias,
        capital_acciones=capital_acciones, capital_estrategia=capital_estrategia,
        estrategia_nombre=estrategia_nombre, estrategia_por_que=estrategia_por_que,
        precio_maximo=precio_maximo, checklist=checklist,
    )


def buscar_oportunidades(
    ranking: list[Puntuacion], barras_por_ticker: dict[str, Barras], fund_por_ticker: dict[str, Fundamentales],
    tickers_shortlist_anterior: set[str] | None = None,
) -> list[Oportunidad]:
    """Punto de entrada del pipeline diario -- escanea el universo
    COMPLETO ya validado (no solo el Top N de la shortlist, que es la
    limitación real de shortlist_hoy.json: solo persiste el Top N).
    Nunca lanza por un ticker individual -- un fallo aislado no debe
    tumbar la corrida completa. Devuelve TODAS las detectadas, ordenadas
    por Convicción -- el tope diario a `LIMITE_DIARIO` se aplica en
    `mensaje_oportunidades` (capa de presentación), no aquí, para que el
    resumen final pueda mencionar las que no entraron en el detalle en
    vez de descartarlas en silencio."""
    encontradas = []
    for p in ranking:
        b = barras_por_ticker.get(p.ticker)
        if b is None or not b.close:
            continue
        fund = fund_por_ticker.get(p.ticker, Fundamentales(p.ticker))
        try:
            patron = detectar_patron(b, p.sub, fund)
            if patron is None:
                continue
            encontradas.append(construir_oportunidad(p, b, fund, patron, tickers_shortlist_anterior))
        except Exception as e:
            log.warning("detección de oportunidad falló para %s: %s", p.ticker, e)
    encontradas.sort(key=lambda o: o.conviccion, reverse=True)
    return encontradas


def _fmt(x: float) -> str:
    return f"${x:,.0f}"


def formatear_oportunidad(o: Oportunidad) -> str:
    lineas = [SEP, "", "🚨 Oportunidad detectada", "", o.ticker]
    if o.nombre:
        lineas.append(o.nombre)
    lineas += ["", f"Convicción del modelo: {o.conviccion}/100", "",
              f"Mi decisión: {_DECISION_EMOJI[o.decision]} {o.decision}"]
    if o.motivo_decision:
        lineas.append(o.motivo_decision)
    elif o.decision == "Comprar hoy":
        lineas.append(f"Precio actual dentro de la zona que activó este patrón ({_fmt(o.entrada)}).")

    lineas += ["", "¿Por qué?", "", o.que_ocurrio, o.que_invalida, f"Lo que estoy esperando: {o.que_espero}"]

    lineas += ["", "Qué cambió hoy:"]
    if o.que_cambio:
        lineas += [f"✔ {c}" for c in o.que_cambio]
    else:
        lineas.append("Sin cambios importantes. La tesis sigue intacta.")

    lineas += ["", f"Precio actual: {o.precio_actual_texto}"]
    if o.stop is not None:
        lineas.append(f"Stop: {_fmt(o.stop)}")
    if o.objetivo is not None:
        lineas.append(f"Objetivo: {_fmt(o.objetivo)}")
    if o.horizonte_dias is not None:
        lineas.append(f"Horizonte esperado: {o.horizonte_dias} días (vencimiento de la opción elegida)")

    lineas += ["", "💰 Capital", f"Acciones: {_fmt(o.capital_acciones)}"]
    if o.capital_estrategia is not None and o.estrategia_nombre:
        lineas.append(f"Opciones: Desde {_fmt(o.capital_estrategia)}")
        lineas += ["", f"Estrategia recomendada: {o.estrategia_nombre}"]
        if o.estrategia_por_que:
            lineas += ["", f"Elegí {o.estrategia_nombre} porque:"]
            lineas += [f"✔ {bullet}" for bullet in o.estrategia_por_que]
    else:
        lineas += ["", "Estrategia recomendada: Comprar acciones directamente (opciones no disponibles hoy)"]

    niveles_alerta = sorted({round(x) for x in (o.entrada, o.stop, o.objetivo) if x is not None})
    if niveles_alerta:
        lineas += ["", f"Crear alertas en estos niveles: {', '.join(_fmt(x) for x in niveles_alerta)}"]

    lineas += ["", f"Nivel de urgencia: {_URGENCIA_EMOJI[o.urgencia]} {o.urgencia}"]
    lineas.append(_URGENCIA_MOTIVO[o.patron])

    lineas += ["", "🎯 Mi plan"]
    if o.decision == "Comprar hoy":
        lineas.append("✅ Abriría posición hoy.")
        lineas.append(f"Tipo: {o.estrategia_nombre or 'Comprar acciones directamente'}")
        if o.precio_maximo is not None:
            lineas.append(f"Precio máximo que pagaría: {_fmt(o.precio_maximo)}")
            lineas.append(
                f"Si mañana abre arriba de {_fmt(o.precio_maximo)}: esperaría otro "
                "retroceso antes de entrar."
            )
        lineas.append(
            f"Riesgo por operación: no más del {RiskLimits().riesgo_por_trade_pct:.0%} del "
            "portafolio (regla fija del Risk Manager, no ajustada a tu capital real)."
        )
        if o.checklist:
            lineas += ["", "Checklist antes de comprar:"]
            lineas += [f"☑ {c}" for c in o.checklist]
            lineas.append("◻ Sin noticias negativas de alta relevancia (no disponible todavía)")
            lineas += ["", "🟢 Todo lo que puedo verificar está en orden -- ejecutaría la operación."]
    elif o.decision == "Esperar":
        lineas.append("🟡 Crearía la alerta y esperaría confirmación antes de entrar.")
    else:
        lineas.append("❌ No abriría posición hoy.")

    lineas += ["", SEP]
    return "\n".join(lineas)


def _resumen_del_dia(todas: list[Oportunidad], mostradas: list[Oportunidad], universo_n: int) -> str:
    """Cierre del mensaje -- pedido explícito: "eso te obliga a
    priorizar". Menciona TODAS las detectadas (no solo las mostradas en
    detalle), para no descartar en silencio las que el tope diario dejó
    afuera."""
    medallas = ["🥇", "🥈", "🥉"]
    plural = "oportunidad" if len(todas) == 1 else "oportunidades"
    lineas = [SEP, "", "🏁 Resumen del día", f"Analicé {universo_n} empresas.", f"Encontré {len(todas)} {plural}."]
    lineas.append(
        f"Pero solo compraría estas {len(mostradas)}:" if len(todas) > len(mostradas)
        else "Estas son las que sí compraría:"
    )
    for i, o in enumerate(mostradas):
        medalla = medallas[i] if i < len(medallas) else "•"
        lineas.append(f"{medalla} {o.ticker} ({o.conviccion}/100)")
    restantes = todas[len(mostradas):]
    if restantes:
        lineas.append(
            f"Las demás ({', '.join(o.ticker for o in restantes)}) las vigilaría, "
            "pero no abriría posición hoy."
        )
    lineas += ["", SEP]
    return "\n".join(lineas)


def mensaje_oportunidades(oportunidades: list[Oportunidad], universo_n: int) -> str:
    """Texto final a mandar por Telegram -- el no-resultado
    (SIN_OPORTUNIDADES) es una salida tan válida como encontrar varias.
    `oportunidades` ya viene ordenada por Convicción (ver
    buscar_oportunidades); el tope diario (mostrar el detalle de solo
    las mejores `LIMITE_DIARIO`) se aplica aquí, en la capa de
    presentación."""
    if not oportunidades:
        return SIN_OPORTUNIDADES
    mostrar = oportunidades[:LIMITE_DIARIO]
    cuerpo = "\n\n".join(formatear_oportunidad(o) for o in mostrar)
    resumen = _resumen_del_dia(oportunidades, mostrar, universo_n)
    return f"{cuerpo}\n\n{resumen}"
