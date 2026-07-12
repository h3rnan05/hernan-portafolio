"""Evaluador de ideas de inversión — el "filtro Market Wizards".

Recibe una noticia o idea en texto libre (vía Telegram) y devuelve un
veredicto estructurado. El LLM solo PROPONE: la ejecución la hace el
wizards bot en GitHub Actions, que impone los límites de riesgo en código
(1.5% por trade, stop 2×ATR, calor ≤6%, máx. posiciones). Aunque este
evaluador dijera "sí" a una locura, el código de riesgo la recorta o rechaza.
"""

from __future__ import annotations

import json
import os
from typing import Any

from anthropic import AsyncAnthropic

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """\
Eres el filtro de ideas de inversión de un bot de trading que sigue los \
principios de "Los magos del mercado" (Market Wizards, Jack Schwager). El \
usuario te manda una noticia o una idea, y tú decides si merece una posición \
LARGA en el portafolio (virtual, $5,000, solo acciones/ETFs de EEUU con \
ticker en Yahoo Finance).

Evalúa con el rigor de los entrevistados del libro:
1. TESIS: ¿hay una razón concreta por la que esto debería subir DESDE AQUÍ?
   Una noticia ya publicada y corrida suele estar ya en el precio (si la
   noticia es de hace días o el activo ya voló, dilo y rechaza).
2. INVALIDACIÓN: ¿se puede definir dónde estaría mal la tesis? Sin nivel de
   invalidación no hay trade (el bot pondrá stop 2×ATR: asume eso).
3. NO PERSEGUIR EUFORIA: hype/memes/FOMO sin catalizador medible = rechazo.
   "Todo el mundo habla de X" es razón para sospechar, no para comprar.
4. ASIMETRÍA: ¿el potencial razonable supera claramente el riesgo del stop?
5. OPERABLE: necesita un ticker líquido de EEUU (NYSE/Nasdaq/ETF). Cripto,
   OTC, mercados extranjeros sin ADR o "ideas sin activo" = NO_ACCIONABLE.

Responde SOLO con JSON válido, sin markdown, con este esquema exacto:
{
  "veredicto": "INVERTIR" | "NO_INVERTIR" | "NO_ACCIONABLE",
  "ticker": "SÍMBOLO o null",
  "confianza": 1-10,
  "tesis": "una frase con la tesis si INVERTIR, si no null",
  "respuesta_usuario": "tu respuesta al usuario en español: veredicto y \
razonamiento en 3-6 frases, directo y honesto, sin tecnicismos innecesarios"
}

Regla dura: INVERTIR requiere confianza >= 7 Y ticker concreto. Si dudas,
NO_INVERTIR. Rechazar una idea mediocre es proteger el capital — el libro
entero trata de eso. En respuesta_usuario, si rechazas, explica qué tendría
que ser cierto para que la idea sí fuera operable."""


async def evaluar_idea(texto: str, contexto_portafolio: str) -> dict[str, Any]:
    """Un veredicto estructurado sobre una idea/noticia del usuario."""
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = await client.messages.create(
        model=MODEL,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Estado actual del portafolio:\n{contexto_portafolio}\n\n"
                f"Idea/noticia del usuario:\n{texto}"
            ),
        }],
    )
    crudo = "".join(b.text for b in msg.content if b.type == "text").strip()
    # El modelo a veces envuelve el JSON en ```; tolerarlo.
    if crudo.startswith("```"):
        crudo = crudo.strip("`").removeprefix("json").strip()
    try:
        v = json.loads(crudo)
    except json.JSONDecodeError:
        return {
            "veredicto": "NO_ACCIONABLE",
            "ticker": None,
            "confianza": 0,
            "tesis": None,
            "respuesta_usuario": (
                "No pude estructurar un veredicto para eso. ¿Me lo mandas "
                "de nuevo con el activo/ticker y por qué crees que sube?"
            ),
        }
    # Cinturón y tirantes sobre el propio LLM:
    if v.get("veredicto") == "INVERTIR" and (
        not v.get("ticker") or int(v.get("confianza", 0)) < 7
    ):
        v["veredicto"] = "NO_INVERTIR"
    return v
