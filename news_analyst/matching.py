"""Cruce determinístico titular <-> shortlist. Cero LLM acá -- esto es
exactamente lo que Hernán pidió como la parte confiable de "Why Should I
Care?": o el titular menciona una empresa de la shortlist, o no, y eso se
puede verificar con texto plano, no con el juicio de un modelo.

Prioridad de matching: nombre de la empresa PRIMERO (más específico, menos
falsos positivos: "Apple anuncia..." es inequívoco). El ticker solo es un
respaldo, y solo para tickers de >=3 letras (config.LONGITUD_MIN_TICKER_SOLO)
-- tickers cortos como "C" o "F" son palabras/siglas comunes en inglés y
generarían demasiado ruido.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from news_analyst.config import LONGITUD_MIN_TICKER_SOLO
from news_analyst.models import EntradaShortlist, Mencion


def cargar_shortlist(path: Path) -> list[EntradaShortlist]:
    """Lee shortlist_hoy.json (screener/run.py). Tolerante a versiones
    viejas del archivo sin nombre/industria (quedan en None)."""
    data = json.loads(Path(path).read_text())
    return [
        EntradaShortlist(
            ticker=fila["ticker"], posicion=i + 1, score=fila["score"],
            sector=fila.get("sector"), nombre=fila.get("nombre"),
            industria=fila.get("industria"), sub_scores=fila.get("sub_scores", {}),
        )
        for i, fila in enumerate(data.get("shortlist", []))
    ]


def _nombre_corto(nombre: str) -> str:
    """'Apple Inc.' -> 'Apple': quita sufijos corporativos comunes para
    matchear contra cómo la prensa realmente nombra a las empresas."""
    sufijos = r"\s+(Inc\.?|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|PLC|N\.V\.|S\.A\.|Group|Holdings?)\.?$"
    return re.sub(sufijos, "", nombre, flags=re.IGNORECASE).strip()


def _menciona(titular: str, texto: str) -> bool:
    """Match de palabra completa, case-insensitive."""
    if not texto:
        return False
    return re.search(rf"\b{re.escape(texto)}\b", titular, flags=re.IGNORECASE) is not None


def detectar_mencion(titular: str, shortlist: list[EntradaShortlist]) -> Mencion | None:
    """Devuelve la PRIMERA coincidencia (nombre antes que ticker, y dentro
    de eso, el orden de la shortlist -- ya viene rankeada por el modelo).
    None si el titular no menciona a nadie de la shortlist."""
    for entrada in shortlist:
        if entrada.nombre and _menciona(titular, _nombre_corto(entrada.nombre)):
            return Mencion(entrada=entrada, coincidio_por="nombre")
    for entrada in shortlist:
        if len(entrada.ticker) >= LONGITUD_MIN_TICKER_SOLO and _menciona(titular, entrada.ticker):
            return Mencion(entrada=entrada, coincidio_por="ticker")
    return None
