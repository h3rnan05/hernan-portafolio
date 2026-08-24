"""Capa de decisión con IA -- el ÚNICO lugar de todo el repo donde un LLM
decide si un trade se ejecuta o no.

Reversión deliberada y explícita del principio original de
`momentum_hunter` ("ninguna IA decide, solo genera texto") -- el usuario
pidió puntualmente que el bot actúe con criterio de trader, no solo
mecánico, y confirmó por escrito que entiende que sigue siendo 100% paper
trading. Ver `README.md` de este módulo para el detalle completo de la
decisión y sus guardarraíles.

La IA decide con la MISMA evidencia que tendría un trader mirando la
pantalla, toda reunida por sistemas deterministas ya probados:
  - lo que la entrada TRIGGERED trae congelado (catalizador, float,
    short interest, niveles),
  - la lectura intradía más reciente de la auditoría del día
    (`momentum_hunter/auditoria/` -- rvol, VWAP, aceleración, y el
    veredicto del evaluador en esa lectura),
  - el historial REAL del sistema con este tipo de catalizador
    (`momentum_hunter.memoria` sobre el tracker -- win rate medido, o la
    admisión honesta de que no hay muestra),
  - la historia de transiciones de esta entrada (cuánto tardó en
    disparar, qué la confirmó),
  - el estado actual de la cuenta paper (efectivo, posiciones abiertas)
    que le pasa el executor.

Guardarraíles que esta capa NUNCA puede saltarse (por construcción, no por
promesa del prompt):
  - Solo se llama para entradas que YA están en TRIGGERED -- el veto fatal
    del escéptico de `momentum_hunter/evaluator.py` ya se aplicó antes de
    llegar acá; esta capa no puede reabrir esa decisión.
  - Nunca inventa ni ajusta precios: entrada/stop/objetivo siempre son los
    que ya cacheó `EntradaWatchlist` (`ultima_entrada`/`ultimo_stop`/
    `ultimo_objetivo`). Puede REDUCIR el tamaño de la posición (`fraccion`
    entre 0.25 y 1.0, convicción parcial = riesgo parcial), pero jamás
    aumentarlo por encima del riesgo configurado -- el código recorta
    cualquier valor fuera de ese rango.
  - Fail-closed: cualquier respuesta inválida, no parseable, o cualquier
    excepción de red/API, se traduce en "no entrar" -- nunca en "entrar
    de todos modos". Mismo principio que `telegram_bot/idea_evaluator.py`.
  - Sin `ANTHROPIC_API_KEY`, no se opera -- mismo principio que
    `momentum_hunter.run.enviar_telegram` con los secrets de Telegram:
    falta de secret nunca es un error fatal, solo deja de operar."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from anthropic import Anthropic

from momentum_hunter import memoria, tracker
from momentum_hunter.audit import DIR_AUDITORIA
from momentum_hunter.watchlist import EntradaWatchlist

log = logging.getLogger("momentum_paper_trader.ia_decision")

MODEL = "claude-sonnet-5"

FRACCION_MINIMA = 0.25   # por debajo de esto, la convicción es tan baja que lo honesto es no entrar

SYSTEM_PROMPT = """\
Eres el trader que revisa la última señal antes de ejecutarla, dentro de \
un sistema de PAPER TRADING (dinero simulado, nunca real). Un pipeline \
mecánico (momentum_hunter) ya filtró miles de candidatas por catalizador, \
float, volumen relativo y ruptura de nivel, y ya aplicó su propio veto de \
escéptico -- lo que ves acá ya pasó ese filtro y está en estado TRIGGERED, \
con entrada/stop/objetivo YA CALCULADOS por ese pipeline (nunca los \
cambies ni propongas otros).

Tu trabajo es el último criterio antes de arriesgar el dinero (simulado) \
de la cuenta: ¿esta oportunidad concreta, con esta evidencia concreta, es \
una que un trader disciplinado tomaría? Decides tres cosas: SI entrar, \
con cuánta CONVICCIÓN, y con qué FRACCIÓN del tamaño normal de posición \
(entre 0.25 y 1.0 -- convicción parcial = riesgo parcial; nunca puedes \
aumentar el tamaño por encima del normal).

Evalúa con el rigor de un trader experimentado de momentum/catalizadores:

1. CALIDAD DEL CATALIZADOR: ¿es un catalizador real y medible (noticia \
concreta, con fuente), o ruido/especulación? ¿La ventana temporal todavía \
tiene sentido o la noticia ya está vieja?
2. LECTURA INTRADÍA ACTUAL: si hay un snapshot de auditoría, úsalo -- \
¿el volumen relativo sigue alto, el precio sigue sobre el VWAP, o la \
señal ya se enfrió desde que disparó? Una TRIGGERED de hace una hora con \
el momentum apagado es una trampa clásica.
3. HISTORIAL REAL DEL SISTEMA: si el sistema tiene resultados medidos con \
este tipo de catalizador, pésalos. Si dice honestamente que no hay \
muestra, trátala como no probada (más razón para fracción reducida si \
entras).
4. ASIMETRÍA RIESGO/BENEFICIO: con la entrada/stop/objetivo dados, ¿la \
distancia al objetivo justifica claramente el riesgo al stop?
5. CALIDAD ESTRUCTURAL: float, interés en corto, large cap -- ¿el perfil \
encaja con un movimiento explosivo sostenible?
6. CLIMA DEL MERCADO GENERAL: si viene "debil", el mercado entero está \
remando en contra y las rupturas al alza fallan más seguido -- exige más \
convicción o entra con fracción reducida. Si viene "favorable", no es una \
razón para relajar el resto de los criterios. "desconocido" no es ni bueno \
ni malo: simplemente no pesa.
7. CONTEXTO DE LA CUENTA: si la cuenta ya está cargada de posiciones o el \
efectivo está justo, sé MÁS selectivo, no menos. No perseguir euforia: si \
la evidencia huele a FOMO tardío más que a ruptura temprana, rechaza.

Responde SOLO con JSON válido, sin markdown, con este esquema exacto:
{
  "entrar": true | false,
  "confianza": 1-10,
  "fraccion": 0.25-1.0,
  "razonamiento": "2-4 frases en español explicando la decisión -- esto se \
le muestra directo al usuario en el mensaje de Telegram de la orden, así \
que debe ser claro y concreto sobre el PORQUÉ (qué viste en la evidencia \
que te hizo entrar o no, y por qué ese tamaño)"
}

Regla dura: "entrar": true requiere confianza >= 7. Si dudas, no entres -- \
proteger el capital (aunque sea simulado) es el trabajo. Rechazar una \
señal mediocre también es una decisión de trader, y de las buenas."""


SYSTEM_PROMPT_CIERRE = """\
Eres el trader decidiendo qué hacer con una posición abierta a diez \
minutos del cierre del mercado, dentro de un sistema de PAPER TRADING \
(dinero simulado, nunca real). La posición se abrió HOY sobre un \
catalizador del día y no ha tocado ni su stop ni su objetivo.

Tienes dos opciones y ninguna es gratis:

CERRAR: tomas el resultado que haya, bueno o malo, y duermes tranquilo. \
Es lo coherente con la estrategia de este bot, que analiza movimientos \
INTRADÍA -- nada en el sistema evalúa el riesgo de mantener algo abierto \
de un día para otro.

AGUANTAR: la posición sigue viva hasta mañana con un stop de protección \
nuevo. Solo tiene sentido si la tesis del día SIGUE INTACTA y el \
movimiento simplemente va lento -- no si "va a recuperarse". Aguantar \
esperando que un perdedor se dé la vuelta es el error clásico que arruina \
cuentas: por eso existía el stop.

Riesgo que debes pesar al aguantar: durante la noche pueden salir \
noticias y la acción puede abrir con un hueco muy por debajo del stop. \
El stop reduce el riesgo nocturno, NO lo elimina -- si abre en hueco, la \
venta se ejecuta al precio de apertura, no al del stop.

Criterios para aguantar (deben cumplirse casi todos, no solo uno):
1. El catalizador sigue vigente y no se ha agotado en el precio.
2. La posición está en ganancia o cerca de plano -- una pérdida abierta \
al cierre casi siempre es la tesis diciendo que se equivocó.
3. El mercado general no viene débil.
4. El movimiento se ve pausado/consolidando, no revertido.

Si dudas, CIERRA. Es la opción cuyo peor caso es conocido.

Responde SOLO con JSON válido, sin markdown:
{
  "cerrar": true | false,
  "confianza": 1-10,
  "razonamiento": "2-3 frases en español, claras y concretas sobre el \
PORQUÉ -- esto se le muestra directo al usuario en Telegram"
}

Regla dura: "cerrar": false (aguantar) requiere confianza >= 7."""


# Presupuesto de tokens de la respuesta. Subido de 500/400 a estos
# valores tras el fallo REAL del 2026-08-24: la primera señal que llegó
# a la IA en la historia del bot (LLY, tras la corrección del umbral)
# recibió HTTP 200 pero con texto VACÍO, y el fail-closed la descartó.
# Un presupuesto ajustado es la causa más probable de una respuesta sin
# bloque de texto -- si el modelo gasta el cupo antes de emitir el JSON,
# `msg.content` puede no traer ningún bloque "text". Duplicar el margen
# cuesta céntimos por llamada; perder la señal cuesta el día entero.
MAX_TOKENS_ENTRADA = 2000
MAX_TOKENS_CIERRE = 1500


def _texto_de(msg, ticker: str) -> str:
    """Concatena los bloques de texto de la respuesta.

    Cuando sale vacío deja constancia de POR QUÉ -- `stop_reason` y los
    tipos de bloque que sí vinieron. El 2026-08-24 esa información no se
    registraba y hubo que deducir la causa desde fuera; con esto, la
    próxima vez el log lo dice directamente."""
    partes = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    texto = "".join(partes).strip()
    if not texto:
        tipos = [getattr(b, "type", "?") for b in msg.content]
        log.warning(
            "%s: la IA respondió sin texto -- stop_reason=%s, bloques=%s",
            ticker, getattr(msg, "stop_reason", "?"), tipos or "ninguno")
    return texto


def _pedir_texto(client, *, model, max_tokens, system, contenido, ticker: str) -> str:
    """Una llamada, y UN reintento si la respuesta viene vacía.

    El reintento es barato y cubre el caso transitorio; si el segundo
    intento también vuelve vacío, el llamador falla cerrado como siempre
    -- reintentar en bucle sería peor que no operar."""
    for intento in (1, 2):
        msg = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": contenido}],
        )
        texto = _texto_de(msg, ticker)
        if texto:
            return texto
        if intento == 1:
            log.info("%s: respuesta vacía de la IA, reintentando una vez", ticker)
    return ""


@dataclass(frozen=True)
class DecisionCierre:
    cerrar: bool
    confianza: int
    razonamiento: str


_CIERRE_POR_DEFECTO = DecisionCierre(
    cerrar=True, confianza=0,
    razonamiento=("No se pudo obtener el criterio de la IA para esta posición -- se cierra "
                  "por seguridad: aguantar sin una decisión es el único desenlace que nadie eligió."),
)


def decidir_cierre(contexto: str) -> DecisionCierre:
    """¿Cerrar esta posición antes del cierre, o aguantarla hasta mañana?

    Fail-closed hacia CERRAR en todos los caminos (sin credencial, error
    de red, respuesta no parseable, confianza insuficiente). La dirección
    importa y es deliberada: de las dos opciones, aguantar es la que
    tiene el peor caso desconocido -- un hueco de apertura nocturno.
    Cuando no hay una decisión de verdad, se toma la de riesgo acotado."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.info("sin ANTHROPIC_API_KEY -- se cierra la posición por defecto")
        return _CIERRE_POR_DEFECTO

    try:
        client = Anthropic(api_key=api_key)
        crudo = _pedir_texto(
            client, model=MODEL, max_tokens=MAX_TOKENS_CIERRE, system=SYSTEM_PROMPT_CIERRE,
            contenido=contexto, ticker="cierre")
    except Exception as ex:
        log.warning("falló la consulta de cierre a la IA: %s", ex)
        return _CIERRE_POR_DEFECTO

    if crudo.startswith("```"):
        crudo = crudo.strip("`").removeprefix("json").strip()
    try:
        v = json.loads(crudo)
        cerrar = bool(v["cerrar"])
        confianza = int(v["confianza"])
        razonamiento = str(v["razonamiento"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        log.warning("la IA no devolvió un veredicto de cierre usable: %r", crudo)
        return _CIERRE_POR_DEFECTO

    # Cinturón y tirantes: aguantar exige convicción alta. Un "aguantar"
    # con confianza 4 se convierte en cerrar, igual que en `decidir`.
    if not cerrar and confianza < 7:
        return DecisionCierre(
            cerrar=True, confianza=confianza,
            razonamiento=(razonamiento + " (Convicción insuficiente para aguantar "
                          "de un día para otro -- se cierra.)"))
    return DecisionCierre(cerrar=cerrar, confianza=confianza, razonamiento=razonamiento)


@dataclass(frozen=True)
class DecisionIA:
    entrar: bool
    confianza: int
    razonamiento: str
    fraccion: float = 1.0   # 0.25-1.0 -- SOLO reduce el tamaño, nunca lo aumenta
    # True = no se pudo OBTENER una decisión (sin credencial, red caída,
    # respuesta ilegible). Distinto de que la IA dijera que no.
    #
    # Importa porque el executor registra cada revisión para no repetirla:
    # un "no" de la IA debe quemar la señal (ya se juzgó), pero un fallo
    # técnico NO -- si no, un problema de infraestructura de un minuto
    # descarta la oportunidad del día entero. Pasó de verdad el
    # 2026-08-24 con LLY: la API respondió vacío, se registró como
    # revisada, y la señal quedó muerta aunque el arreglo llegara diez
    # minutos después. Mismo principio que el executor ya aplicaba a un
    # fallo de Alpaca ("no se registra como revisada").
    fallo_tecnico: bool = False


_DECISION_FALLBACK_SIN_CLAVE = DecisionIA(
    entrar=False, confianza=0,
    razonamiento="Sin ANTHROPIC_API_KEY configurada -- no se puede pedir el criterio de la IA, así que no se opera.",
    fallo_tecnico=True,
)
_DECISION_FALLBACK_ERROR = DecisionIA(
    entrar=False, confianza=0,
    razonamiento="La revisión de la IA falló o no devolvió un veredicto usable -- por seguridad, no se opera (fail-closed).",
    fallo_tecnico=True,
)


def _snapshot_intradia(ticker: str) -> str | None:
    """La lectura MÁS RECIENTE de este ticker en la auditoría de hoy --
    mismo archivo que `momentum_hunter/audit.py` ya escribe en cada
    corrida, ningún dato nuevo se pide. Mejor esfuerzo: cualquier
    problema leyendo/parseando devuelve None (la IA decide sin esta
    sección, nunca se cae la corrida por esto)."""
    try:
        path = DIR_AUDITORIA / f"{datetime.now(UTC).date().isoformat()}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        for corrida in reversed(data.get("corridas", [])):
            for c in corrida.get("candidatos", []):
                if c.get("ticker") != ticker:
                    continue
                fi = c.get("factores_intradia") or {}
                ev = c.get("evaluacion") or {}
                early = ev.get("early") or {}

                def _f(v, fmt="{:.2f}"):
                    return fmt.format(v) if isinstance(v, (int, float)) else "desconocido"

                lineas = [
                    f"(lectura de {corrida.get('timestamp', '?')})",
                    f"Precio: ${_f(fi.get('precio_actual'))} -- VWAP: ${_f(fi.get('vwap'))} -- EMA9: ${_f(fi.get('ema9'))}",
                    f"RVOL actual: {_f(fi.get('rvol_actual'))} -- aceleración de volumen: {_f(fi.get('aceleracion_volumen'))}",
                    f"Veredicto del evaluador en esa lectura: {early.get('veredicto', 'desconocido')}"
                    + (f" -- {early.get('motivo_veredicto')}" if early.get("motivo_veredicto") else ""),
                ]
                if ev.get("penalizaciones"):
                    lineas.append("Penalizaciones: " + "; ".join(str(p) for p in ev["penalizaciones"][:3]))
                return "\n".join(lineas)
        return None
    except Exception as ex:
        log.warning("%s: no se pudo leer el snapshot de auditoría: %s", ticker, ex)
        return None


def _historial_catalizador(tipo: str | None) -> str | None:
    """El historial REAL del sistema con este tipo de catalizador --
    reutiliza `momentum_hunter.memoria` tal cual (misma frase honesta que
    va en las alertas: porcentaje medido con muestra suficiente, o la
    admisión de que no existe). Mejor esfuerzo, igual que el snapshot."""
    try:
        ctx = memoria.contexto_catalizador(tracker.cargar(), tipo)
        return memoria.frase_probabilidad(ctx)
    except Exception as ex:
        log.warning("no se pudo leer el historial del tracker: %s", ex)
        return None


def _resumen_transiciones(e: EntradaWatchlist) -> str | None:
    """Cómo llegó esta entrada hasta TRIGGERED -- la historia que un
    trader tendría en la cabeza por haberla estado mirando."""
    if not e.transiciones:
        return None
    return "\n".join(
        f"- {t.estado} @ {t.timestamp}: {t.motivo}" for t in e.transiciones[-6:])


def construir_paquete_evidencia(e: EntradaWatchlist, contexto_cuenta: str | None = None) -> str:
    """Toda la evidencia disponible, cada sección de un sistema
    determinista ya probado -- esta capa solo la REÚNE, nunca pide datos
    nuevos ni recalcula nada (ver docstring del módulo)."""
    secciones = [
        f"Ticker: {e.ticker} ({e.nombre or 'nombre no disponible'})\n"
        f"Catalizador: {e.catalizador_tipo or 'desconocido'} -- "
        f"\"{e.catalizador_titular or 'sin titular'}\"\n"
        f"Fuente: {e.catalizador_fuente or 'desconocida'} "
        f"({e.catalizador_fecha or 'fecha desconocida'})\n"
        f"Score base del pipeline: {e.score_base:.1f}\n"
        f"Float: {e.shares_float if e.shares_float is not None else 'desconocido'} acciones\n"
        f"% del float en corto: {e.short_pct_float if e.short_pct_float is not None else 'desconocido'}\n"
        f"Large cap: {'sí' if e.es_large_cap else 'no'}\n"
        f"ATR diario: {e.atr_diario if e.atr_diario is not None else 'desconocido'}\n"
        f"Gap de apertura: {e.gap_pct_congelado if e.gap_pct_congelado is not None else 'desconocido'}\n\n"
        f"Niveles ya calculados por el pipeline (no se pueden modificar):\n"
        f"  Entrada: ${e.ultima_entrada:.2f}\n"
        f"  Stop: ${e.ultimo_stop:.2f}\n"
        f"  Objetivo: ${e.ultimo_objetivo:.2f}"
    ]

    transiciones = _resumen_transiciones(e)
    if transiciones:
        secciones.append(f"Cómo llegó hasta acá (transiciones de la watchlist):\n{transiciones}")

    snapshot = _snapshot_intradia(e.ticker)
    secciones.append(
        f"Lectura intradía más reciente de la auditoría:\n{snapshot}" if snapshot
        else "Lectura intradía más reciente de la auditoría: no disponible en esta corrida.")

    historial = _historial_catalizador(e.catalizador_tipo)
    if historial:
        secciones.append(
            f"Historial real del sistema con catalizadores tipo "
            f"'{e.catalizador_tipo}':\n{historial}")

    # Cómo venía el mercado GENERAL en el último chequeo (ver
    # `momentum_hunter/mercado.py`). Decisión explícita del usuario
    # (2026-08-21): esto NO bloquea la operación -- llega como un dato
    # más para que la IA lo pese, igual que lo pesaría un trader antes
    # de comprar una ruptura en un día en que todo se está cayendo.
    if e.clima_mercado:
        secciones.append(f"Clima del mercado general: {e.clima_mercado}")

    if contexto_cuenta:
        secciones.append(f"Estado actual de la cuenta paper:\n{contexto_cuenta}")

    return "\n\n".join(secciones)


def _parsear_fraccion(v: dict) -> float:
    """SOLO puede reducir el tamaño: fuera de [FRACCION_MINIMA, 1.0] o no
    numérico -> 1.0 (el riesgo configurado en `config.py` sigue siendo el
    techo absoluto; un valor raro del modelo nunca puede aumentarlo)."""
    try:
        f = float(v.get("fraccion", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if not (FRACCION_MINIMA <= f <= 1.0):
        return 1.0
    return f


def decidir(e: EntradaWatchlist, contexto_cuenta: str | None = None) -> DecisionIA:
    """Fail-closed en TODOS los caminos: sin credencial, error de red,
    respuesta no parseable, o confianza insuficiente -> `entrar=False`.
    Nunca una excepción se propaga hacia `executor.ejecutar` -- una falla
    acá debe significar "no se opera esta señal", no tumbar la corrida."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.info("%s: sin ANTHROPIC_API_KEY -- no se opera", e.ticker)
        return _DECISION_FALLBACK_SIN_CLAVE

    if e.ultima_entrada is None or e.ultimo_stop is None or e.ultimo_objetivo is None:
        return _DECISION_FALLBACK_ERROR

    try:
        client = Anthropic(api_key=api_key)
        crudo = _pedir_texto(
            client, model=MODEL, max_tokens=MAX_TOKENS_ENTRADA, system=SYSTEM_PROMPT,
            contenido=construir_paquete_evidencia(e, contexto_cuenta), ticker=e.ticker)
    except Exception as ex:
        log.warning("%s: falló la consulta a la IA: %s", e.ticker, ex)
        return _DECISION_FALLBACK_ERROR

    if crudo.startswith("```"):
        crudo = crudo.strip("`").removeprefix("json").strip()
    try:
        v = json.loads(crudo)
    except json.JSONDecodeError:
        log.warning("%s: la IA no devolvió JSON válido: %r", e.ticker, crudo)
        return _DECISION_FALLBACK_ERROR

    try:
        entrar = bool(v["entrar"])
        confianza = int(v["confianza"])
        razonamiento = str(v["razonamiento"])
    except (KeyError, TypeError, ValueError):
        log.warning("%s: la IA devolvió un JSON con forma inesperada: %r", e.ticker, v)
        return _DECISION_FALLBACK_ERROR

    # Cinturón y tirantes sobre el propio LLM (mismo principio que
    # `telegram_bot/idea_evaluator.py`): la regla dura del prompt se
    # re-valida en código, nunca se confía ciegamente en que el modelo la
    # haya aplicado bien.
    if entrar and confianza < 7:
        entrar = False

    return DecisionIA(
        entrar=entrar, confianza=confianza, razonamiento=razonamiento,
        fraccion=_parsear_fraccion(v),
    )
