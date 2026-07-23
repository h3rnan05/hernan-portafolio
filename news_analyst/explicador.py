"""El LLM SOLO explica, nunca decide (Principio #3 del AIOS, ROADMAP.md).
Grounded estrictamente en el titular + el contexto de la shortlist (por
qué el modelo cuantitativo eligió esta empresa) -- nunca inventa datos
externos. Nunca sugiere comprar/vender: además del propio prompt, hay un
filtro de palabras como defensa adicional (mismo patrón de belt-and-
suspenders que telegram_bot/idea_evaluator.py y wizards_bot.PROMPT_EXPLORADOR).
"""

from __future__ import annotations

import json
import logging
import os

import requests

from news_analyst.config import MODELO_LLM
from news_analyst.models import EntradaShortlist, Explicacion, ResumenNoticias

log = logging.getLogger("news_analyst.explicador")

_TONOS_VALIDOS = {"positivo", "negativo", "neutral", "incierto"}

# Defensa adicional al prompt: si el LLM se desvía y sugiere una acción de
# todas formas, la explicación se descarta entera antes de mostrarse.
_PALABRAS_PROHIBIDAS = (
    "compra", "compren", "vende", "vendan", "recomiendo",
    "recomendación de compra", "recomendación de venta",
    "hay que comprar", "hay que vender", "momento de comprar", "momento de vender",
)

SYSTEM_PROMPT = """\
Eres el analista de noticias del AIOS. Tu ÚNICO trabajo es EXPLICAR por \
qué un titular podría importarle a alguien que tiene esta empresa en su \
shortlist de investigación -- NUNCA le dices qué hacer con la posición.

Reglas duras:
1. NUNCA sugieras comprar, vender, mantener, aumentar o reducir una \
posición. No uses palabras como "compra", "vende", "recomiendo". Tu \
trabajo es explicar, no decidir -- el diseño de este sistema lo prohíbe.
2. Solo puedes usar la información que se te da: el titular y el contexto \
de por qué el modelo cuantitativo eligió esta empresa. No inventes \
cifras, eventos ni datos externos que no estén ahí.
3. Si el titular es ambiguo o su relación con la empresa es débil, dilo \
explícitamente -- no fuerces una conexión que no existe.
4. "tono" describe el titular en sí para el NEGOCIO (positivo, negativo, \
neutral o incierto) -- nunca una predicción de precio.

Responde SOLO con JSON válido, sin markdown:
{"explicacion": "2-3 frases en español, lenguaje llano, sin jerga",
 "tono": "positivo|negativo|neutral|incierto",
 "nivel_importancia": 1}
nivel_importancia es un entero 1-5: 1 = mención tangencial, 5 = catalizador \
directo y concreto para el negocio."""


def _mensaje_usuario(titular: str, entrada: EntradaShortlist) -> str:
    razones = ", ".join(
        f"{f}={s:.0f}" for f, s in
        sorted(entrada.sub_scores.items(), key=lambda kv: kv[1], reverse=True)
    )
    return (
        f'Titular: "{titular}"\n\n'
        f"Contexto de la shortlist: {entrada.nombre or entrada.ticker} "
        f"({entrada.ticker}) está en la posición #{entrada.posicion} de la "
        f"shortlist de hoy, score {entrada.score:.0f}/100, sector "
        f"{entrada.sector or 'desconocido'}. Factores donde puntúa más alto: "
        f"{razones or 'sin datos'}."
    )


def llamar_claude(system: str, user: str) -> str | None:
    """Una llamada simple a la API de Anthropic. Sin ANTHROPIC_API_KEY
    devuelve None y el módulo queda apagado (mismo patrón que
    wizards_bot.llamar_claude)."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODELO_LLM, "max_tokens": 300, "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=60,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"]
                       if b.get("type") == "text").strip()
    except Exception as e:
        log.debug("llamada a Claude falló: %s", e)
        return None


def _parsear(crudo: str) -> Explicacion | None:
    """Pura y testeable sin red: valida estructura + aplica el filtro de
    palabras prohibidas como defensa adicional al propio prompt."""
    texto = crudo.strip()
    if texto.startswith("```"):
        texto = texto.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(texto)
        explicacion = str(data["explicacion"]).strip()
        tono = str(data["tono"]).strip().lower()
        nivel = int(data["nivel_importancia"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if tono not in _TONOS_VALIDOS or not (1 <= nivel <= 5) or not explicacion:
        return None
    if any(p in explicacion.lower() for p in _PALABRAS_PROHIBIDAS):
        log.warning("explicación descartada: contenía lenguaje de recomendación")
        return None
    return Explicacion(texto=explicacion, tono=tono, nivel_importancia=nivel)


def generar_explicacion(titular: str, entrada: EntradaShortlist) -> Explicacion | None:
    """Punto de entrada. None si no hay ANTHROPIC_API_KEY, si la llamada
    falla, o si la respuesta no pasa la validación/filtro -- nunca lanza,
    nunca bloquea el resto de la corrida por una explicación que falló."""
    crudo = llamar_claude(SYSTEM_PROMPT, _mensaje_usuario(titular, entrada))
    if not crudo:
        return None
    return _parsear(crudo)


# ------------------------- resumen agregado de noticias -------------------------
# Para telegram_bot/report_command.py: en vez de una lista de titulares
# sueltos, un resumen de una frase + hasta 3 hechos concretos. Mismos
# guardrails que generar_explicacion (prompt + filtro de palabras
# prohibidas como defensa adicional) -- el LLM sigue sin decidir nada,
# solo condensa lo que ya está en los titulares.

SYSTEM_PROMPT_RESUMEN = """\
Eres el analista de noticias del AIOS. Tu ÚNICO trabajo es RESUMIR un \
grupo de titulares recientes sobre una empresa para alguien que decide \
si vale la pena seguir investigándola -- NUNCA le dices qué hacer con la \
posición.

Reglas duras:
1. NUNCA sugieras comprar, vender, mantener, aumentar o reducir una \
posición. No uses palabras como "compra", "vende", "recomiendo". Tu \
trabajo es resumir, no decidir -- el diseño de este sistema lo prohíbe.
2. Solo puedes usar la información de los titulares que se te dan. No \
inventes cifras, eventos ni datos externos que no estén ahí.
3. Si los titulares son contradictorios o poco concluyentes, dilo \
explícitamente -- no fuerces un tono único que no existe.

Responde SOLO con JSON válido, sin markdown:
{"resumen": "1 frase con el tono general en español (ej. 'La mayoría de \
las noticias fueron positivas.')",
 "puntos": ["hasta 3 frases cortas en español con hechos concretos de \
los titulares"]}"""


def _mensaje_usuario_resumen(titulares: list[str], entrada: EntradaShortlist | None) -> str:
    contexto = ""
    if entrada is not None:
        contexto = (f"\n\nContexto: {entrada.nombre or entrada.ticker} ({entrada.ticker}) "
                    f"está en la posición #{entrada.posicion} de la shortlist de hoy, "
                    f"score {entrada.score:.0f}/100.")
    titulares_txt = "\n".join(f"- {t}" for t in titulares)
    return f"Titulares recientes:\n{titulares_txt}{contexto}"


def _parsear_resumen(crudo: str) -> ResumenNoticias | None:
    """Misma defensa que _parsear(): valida estructura + filtro de
    palabras prohibidas, aplicado sobre el resumen Y los puntos juntos."""
    texto = crudo.strip()
    if texto.startswith("```"):
        texto = texto.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(texto)
        resumen = str(data["resumen"]).strip()
        puntos = [str(p).strip() for p in data["puntos"] if str(p).strip()][:3]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not resumen:
        return None
    texto_completo = (resumen + " " + " ".join(puntos)).lower()
    if any(p in texto_completo for p in _PALABRAS_PROHIBIDAS):
        log.warning("resumen de noticias descartado: contenía lenguaje de recomendación")
        return None
    return ResumenNoticias(resumen=resumen, puntos=puntos)


def resumir_noticias(titulares: list[str], entrada: EntradaShortlist | None) -> ResumenNoticias | None:
    """Punto de entrada. None si no hay titulares, no hay
    ANTHROPIC_API_KEY, la llamada falla, o la respuesta no pasa la
    validación/filtro -- telegram_bot/report_command.py degrada
    mostrando los titulares crudos en ese caso, nunca deja la sección
    vacía en silencio."""
    if not titulares:
        return None
    crudo = llamar_claude(SYSTEM_PROMPT_RESUMEN, _mensaje_usuario_resumen(titulares, entrada))
    if not crudo:
        return None
    return _parsear_resumen(crudo)
