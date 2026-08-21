"""Capa de decisión con IA -- el ÚNICO lugar de todo el repo donde un LLM
decide si un trade se ejecuta o no.

Reversión deliberada y explícita del principio original de
`momentum_hunter` ("ninguna IA decide, solo genera texto") -- el usuario
pidió puntualmente que el bot actúe con criterio de trader, no solo
mecánico, y confirmó por escrito que entiende que sigue siendo 100% paper
trading. Ver `README.md` de este módulo para el detalle completo de la
decisión y sus guardarraíles.

Guardarraíles que esta capa NUNCA puede saltarse (por construcción, no por
promesa del prompt):
  - Solo se llama para entradas que YA están en TRIGGERED -- el veto fatal
    del escéptico de `momentum_hunter/evaluator.py` ya se aplicó antes de
    llegar acá; esta capa no puede reabrir esa decisión.
  - Nunca inventa ni ajusta precios: entrada/stop/objetivo siempre son los
    que ya cacheó `EntradaWatchlist` (`ultima_entrada`/`ultimo_stop`/
    `ultimo_objetivo`) -- este módulo solo puede decidir SÍ/NO, nunca
    "a qué precio".
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

from anthropic import Anthropic

from momentum_hunter.watchlist import EntradaWatchlist

log = logging.getLogger("momentum_paper_trader.ia_decision")

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """\
Eres el trader que revisa la última señal antes de ejecutarla, dentro de \
un sistema de PAPER TRADING (dinero simulado, nunca real). Un pipeline \
mecánico (momentum_hunter) ya filtró miles de candidatas por catalizador, \
float, volumen relativo y ruptura de nivel, y ya aplicó su propio veto de \
escéptico -- lo que ves acá ya pasó ese filtro y está en estado TRIGGERED, \
con entrada/stop/objetivo YA CALCULADOS por ese pipeline (nunca los \
cambies ni propongas otros: tu única decisión es SÍ ejecutar o NO).

Tu trabajo es el último criterio humano antes de arriesgar el dinero \
(simulado) de la cuenta: ¿esta oportunidad concreta, con esta evidencia \
concreta, es una que un trader disciplinado tomaría, o es una donde el \
pipeline mecánico se quedó corto? Evalúa con el mismo rigor que un trader \
experimentado de momentum/catalizadores:

1. CALIDAD DEL CATALIZADOR: ¿es un catalizador real y medible (noticia \
concreta, con fuente), o es ruido/especulación? ¿La ventana temporal \
todavía tiene sentido o la noticia ya está vieja?
2. ASIMETRÍA RIESGO/BENEFICIO: con la entrada/stop/objetivo dados, ¿la \
distancia al objetivo justifica claramente el riesgo al stop?
3. CALIDAD ESTRUCTURAL: float, interés en corto, si es large cap -- \
¿el perfil encaja con un movimiento explosivo sostenible, o hay señales \
de que esto es frágil (float enorme, sin short interest que sostenga el \
squeeze, etc.)?
4. NO PERSEGUIR EUFORIA: si la evidencia huele a FOMO tardío más que a \
una ruptura temprana, rechaza.

Responde SOLO con JSON válido, sin markdown, con este esquema exacto:
{
  "entrar": true | false,
  "confianza": 1-10,
  "razonamiento": "2-4 frases en español explicando la decisión -- esto se \
le muestra directo al usuario en el mensaje de Telegram de la orden, así \
que debe ser claro y concreto sobre el PORQUÉ"
}

Regla dura: "entrar": true requiere confianza >= 7. Si dudas, no entres -- \
proteger el capital (aunque sea simulado) es el trabajo."""


@dataclass(frozen=True)
class DecisionIA:
    entrar: bool
    confianza: int
    razonamiento: str


_DECISION_FALLBACK_SIN_CLAVE = DecisionIA(
    entrar=False, confianza=0,
    razonamiento="Sin ANTHROPIC_API_KEY configurada -- no se puede pedir el criterio de la IA, así que no se opera.",
)
_DECISION_FALLBACK_ERROR = DecisionIA(
    entrar=False, confianza=0,
    razonamiento="La revisión de la IA falló o no devolvió un veredicto usable -- por seguridad, no se opera (fail-closed).",
)


def construir_paquete_evidencia(e: EntradaWatchlist) -> str:
    """Todo lo que la entrada TRIGGERED ya trae congelado/cacheado -- sin
    pedir ningún dato nuevo (ver docstring del módulo: esta capa solo
    decide SÍ/NO sobre la evidencia que `momentum_hunter` ya reunió)."""
    return (
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
    )


def decidir(e: EntradaWatchlist) -> DecisionIA:
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
        msg = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": construir_paquete_evidencia(e)}],
        )
        crudo = "".join(b.text for b in msg.content if b.type == "text").strip()
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

    return DecisionIA(entrar=entrar, confianza=confianza, razonamiento=razonamiento)
