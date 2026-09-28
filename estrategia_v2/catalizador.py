"""Catalizador v2: veto fijo + formato de la clasificación de la IA.

Lógica pura: arma el prompt, valida la respuesta y aplica el veto. NO
llama a ningún modelo (quien clasifica inyecta la llamada), así que el
hunter puede leer resultados sin importar el SDK de la IA.

La IA solo CLASIFICA. Entrada: titular + resumen. Salida, exactamente:
    {"nivel": 0|1|2, "tipo": str, "direccion": "alcista"|"bajista"|"neutral",
     "confianza": número en [0, 1]}
Una respuesta que no cumple (JSON roto, claves de más o de menos, nivel
fuera de 0/1/2, texto alrededor) es "sin clasificar": nunca se adivina
un nivel. La IA no decide entradas: eso lo hacen `reglas.py` y el riesgo.

El VETO no pasa por la IA: una lista fija de frases (en el YAML) sobre
titular + resumen en minúsculas. Si una noticia vigente dispara el
veto, el símbolo no se opera, diga lo que diga la clasificación.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from estrategia_v2.config import Catalizador as CfgCatalizador

DIRECCIONES = ("alcista", "bajista", "neutral")
CLAVES = ("nivel", "tipo", "direccion", "confianza")


@dataclass(frozen=True)
class Clasificacion:
    valida: bool
    nivel: int | None = None
    tipo: str | None = None
    direccion: str | None = None
    confianza: float | None = None
    motivo: str | None = None        # por qué no es válida


def veto(titular: str, resumen: str | None, cfg: CfgCatalizador) -> str | None:
    """Categoría del veto que dispara el texto, o None."""
    texto = f"{titular or ''} {resumen or ''}".lower()
    for categoria, frases in cfg.veto.items():
        if any(f in texto for f in frases):
            return categoria
    return None


def prompts(titular: str, resumen: str | None, cfg: CfgCatalizador) -> tuple[str, str]:
    """(system, user). El texto de cada nivel sale del YAML: cambiar una
    definición es cambiar `prompt_version`."""
    def lista(nivel: int) -> str:
        return "\n".join(f"  - {t}" for t in cfg.niveles[nivel])

    system = (
        "Clasificas UNA noticia bursátil. No recomiendas operar ni opinas del precio.\n"
        "Responde SOLO un objeto JSON, sin texto alrededor, con exactamente estas claves:\n"
        '{"nivel": 0|1|2, "tipo": "<etiqueta corta>", '
        '"direccion": "alcista"|"bajista"|"neutral", "confianza": <número entre 0 y 1>}\n'
        f"Nivel 1 (hecho fuerte y confirmado):\n{lista(1)}\n"
        f"Nivel 2 (hecho moderado):\n{lista(2)}\n"
        f"Nivel 0 (no es catalizador):\n{lista(0)}\n"
        "Si la noticia no encaja claramente en 1 o 2, es 0. "
        "direccion es el efecto probable sobre el precio de la acción de la noticia."
    )
    user = f"Titular: {titular.strip()}\nResumen: {(resumen or '').strip() or '(sin resumen)'}"
    return system, user


def interpretar(respuesta: str) -> Clasificacion:
    """Validación estricta de la salida del modelo."""
    try:
        obj = json.loads((respuesta or "").strip())
    except (json.JSONDecodeError, TypeError):
        return Clasificacion(False, motivo="json_invalido")
    if not isinstance(obj, dict) or set(obj) != set(CLAVES):
        return Clasificacion(False, motivo="claves")
    nivel, tipo, direccion, confianza = (obj[k] for k in CLAVES)
    if isinstance(nivel, bool) or nivel not in (0, 1, 2):
        return Clasificacion(False, motivo="nivel")
    if not isinstance(tipo, str) or not tipo.strip():
        return Clasificacion(False, motivo="tipo")
    if direccion not in DIRECCIONES:
        return Clasificacion(False, motivo="direccion")
    if isinstance(confianza, bool) or not isinstance(confianza, (int, float)) or not 0 <= confianza <= 1:
        return Clasificacion(False, motivo="confianza")
    return Clasificacion(True, int(nivel), tipo.strip(), direccion, float(confianza))


def operable(c: Clasificacion, cfg: CfgCatalizador) -> bool:
    return (c.valida and c.nivel in cfg.niveles_operables
            and c.direccion in cfg.direcciones_operables)


def mejor_nivel(clasificaciones: list[Clasificacion], cfg: CfgCatalizador) -> int | None:
    """El nivel operable más fuerte (1 gana a 2), o None si no hay."""
    niveles = [c.nivel for c in clasificaciones if operable(c, cfg)]
    return min(niveles) if niveles else None
