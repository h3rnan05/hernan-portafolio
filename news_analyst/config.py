"""Config del AI Analyst de noticias (agente #9 del AIOS aplicado a
noticias, ver ROADMAP.md). Todo lo ajustable vive aquí."""

from __future__ import annotations

MODELO_LLM = "claude-sonnet-5"

# Tope de llamadas al LLM por corrida: solo se explican los primeros N
# titulares que SÍ mencionan algo de la shortlist -- controla el costo y
# evita saturar el mensaje. El resto de titulares relevantes quedan
# listados sin explicación ("+N más").
MAX_EXPLICACIONES = 5

# Bajo este umbral de longitud de ticker, no se intenta matchear el
# ticker solo (sin nombre de empresa) contra el texto del titular -- los
# tickers de 1-2 letras (ej. "C", "F") son palabras/siglas comunes en
# inglés y disparan demasiados falsos positivos.
LONGITUD_MIN_TICKER_SOLO = 3
