"""Orchestrator del screener — el 'main' que ata todo con inyección de
dependencias: universo → provider de datos → factores/scoring → reporte →
entrega a Telegram + archivo.

USO
  python -m screener.run                 # corrida completa (S&P 500)
  python -m screener.run --limit 30      # subconjunto rápido (pruebas)
  python -m screener.run --no-fund       # solo factores de precio (sin yfinance)

VARIABLES DE ENTORNO
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (opcionales: manda la shortlist)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import requests

from screener import universe
from screener.config import CONFIG, ScreenerConfig
from screener.data.provider import DataProvider, Fundamentales, YahooProvider
from screener.report import markdown, texto_telegram
from screener.scoring import Puntuacion, puntuar

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("screener.run")

SALIDA = Path(__file__).resolve().parent / "shortlist_hoy.json"
SALIDA_MD = Path(__file__).resolve().parent / "shortlist_hoy.md"


def enviar_telegram(texto: str) -> None:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.info("sin secrets de Telegram: no envío (solo guardo archivo)")
        return
    # Telegram corta en 4096 chars; la shortlist entra de sobra, pero por si
    # acaso se parte en trozos.
    for i in range(0, len(texto), 3900):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": texto[i:i + 3900]}, timeout=15,
            )
        except Exception as e:
            log.warning("envío a Telegram falló: %s", e)


def correr(cfg: ScreenerConfig, provider: DataProvider,
           limite: int | None = None, con_fund: bool = True) -> list[Puntuacion]:
    """Pipeline puro y testeable: recibe el provider inyectado."""
    tickers = universe.cargar(cfg.universe)
    if limite:
        tickers = tickers[:limite]
    log.info("universo: %d tickers", len(tickers))

    barras = provider.barras(tickers, dias=400)
    # Filtro de calidad de datos: liquidez, precio e historia mínimos.
    validos = {
        t: b for t, b in barras.items()
        if len(b) >= cfg.min_barras and b.close[-1] >= cfg.min_price
        and (b.close[-1] * (sum(b.volume[-21:]) / 21) >= cfg.min_dollar_volume)
    }
    log.info("tras filtros de liquidez/precio/historia: %d", len(validos))

    fund = (provider.fundamentales(list(validos)) if con_fund
            else {t: Fundamentales(t) for t in validos})

    ranking = puntuar(validos, fund, cfg)
    return ranking


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="escanear solo los primeros N tickers (pruebas)")
    ap.add_argument("--no-fund", action="store_true",
                    help="omitir fundamentales (solo factores de precio)")
    args = ap.parse_args()

    ranking = correr(CONFIG, YahooProvider(), args.limit, con_fund=not args.no_fund)
    if not ranking:
        log.warning("ranking vacío (¿Yahoo bloqueó los datos?). No envío nada.")
        return

    universo_n = len(ranking)
    txt = texto_telegram(ranking, CONFIG, universo_n)
    print("\n" + txt)

    SALIDA_MD.write_text(markdown(ranking, CONFIG, universo_n))
    SALIDA.write_text(json.dumps({
        "fecha": datetime.now(UTC).isoformat(timespec="seconds"),
        "universo_escaneado": universo_n,
        "shortlist": [
            {"ticker": p.ticker, "score": p.score_total,
             "sector": p.sector, "sub_scores": p.sub}
            for p in ranking[:CONFIG.top_n]
        ],
    }, indent=2, ensure_ascii=False))
    enviar_telegram(txt)


if __name__ == "__main__":
    main()
