"""Orchestrator del AI Analyst de noticias (agente #9 del AIOS aplicado a
noticias, ver ROADMAP.md). Cruza titulares reales contra la shortlist del
día (screener/shortlist_hoy.json) y solo gasta LLM en explicar los que de
verdad la mencionan -- el resto no genera ni ruido ni costo. "Sin noticias
relevantes hoy" es una salida válida y se comunica explícitamente.

USO
  python -m news_analyst.run

VARIABLES DE ENTORNO
  ANTHROPIC_API_KEY                     (sin ella: se listan las menciones sin explicar)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (opcionales: manda el mensaje)
"""

from __future__ import annotations

import logging
from pathlib import Path

from news_analyst.config import MAX_EXPLICACIONES
from news_analyst.explicador import generar_explicacion
from news_analyst.formato import texto_telegram
from news_analyst.matching import cargar_shortlist, detectar_mencion
from news_analyst.models import EntradaShortlist, NoticiaRelevante
from wizards_bot import notify, titulares_google_news

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("news_analyst.run")

SHORTLIST_PATH = Path(__file__).resolve().parent.parent / "screener" / "shortlist_hoy.json"

_CONSULTAS_BASE = ["stock market news today", "company earnings news today"]
_TOP_N_PARA_CONSULTAS = 5  # solo las mejores puntuadas sesgan la búsqueda


def _consultas(shortlist: list[EntradaShortlist]) -> list[str]:
    extra = [f"{e.nombre or e.ticker} stock" for e in shortlist[:_TOP_N_PARA_CONSULTAS]]
    return _CONSULTAS_BASE + extra


def recolectar_titulares(shortlist: list[EntradaShortlist]) -> list[str]:
    """Titulares reales (Google News RSS), deduplicados dentro de la
    misma corrida -- no hace falta dedup entre corridas: consume la
    shortlist fresca del día, una vez al día."""
    vistos: set[str] = set()
    titulares: list[str] = []
    for q in _consultas(shortlist):
        for t in titulares_google_news(q, maximo=6):
            if t not in vistos:
                vistos.add(t)
                titulares.append(t)
    return titulares


def analizar(shortlist: list[EntradaShortlist], titulares: list[str]) -> list[NoticiaRelevante]:
    relevantes: list[NoticiaRelevante] = []
    explicados = 0
    for titular in titulares:
        mencion = detectar_mencion(titular, shortlist)
        if not mencion:
            continue
        explicacion = None
        if explicados < MAX_EXPLICACIONES:
            explicacion = generar_explicacion(titular, mencion.entrada)
            explicados += 1
        relevantes.append(NoticiaRelevante(titular=titular, mencion=mencion, explicacion=explicacion))
    return relevantes


def main() -> None:
    if not SHORTLIST_PATH.exists():
        log.warning("no existe %s -- corre el screener primero.", SHORTLIST_PATH)
        return
    shortlist = cargar_shortlist(SHORTLIST_PATH)
    if not shortlist:
        log.warning("shortlist vacía, nada que cruzar.")
        return

    titulares = recolectar_titulares(shortlist)
    log.info("titulares recolectados: %d", len(titulares))

    relevantes = analizar(shortlist, titulares)
    log.info("titulares relevantes para la shortlist: %d", len(relevantes))

    texto = texto_telegram(relevantes, len(titulares))
    print("\n" + texto)
    notify(texto)


if __name__ == "__main__":
    main()
