#!/usr/bin/env python3
"""Manda UN /report TICKER de muestra a Telegram, reusando
telegram_bot.report_command.generar_reporte() real -- mismos datos, mismo
código que produce /report en producción. No depende del webhook de
Telegram desplegado en Render (que corre como servicio aparte): esto
genera el reporte directo y lo manda vía notify(), igual que
preview_noticia.py hizo con el digest de noticias.

USO
  python preview_reporte.py TICKER

VARIABLES DE ENTORNO
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (requeridas para que se envíe de verdad)
  ANTHROPIC_API_KEY                      (opcional: sin ella, noticias sin explicar)
  DISCORD_WEBHOOK                        (opcional)
"""

from __future__ import annotations

import argparse

from telegram_bot.report_command import generar_reporte
from wizards_bot import notify


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    args = ap.parse_args()

    texto = generar_reporte(args.ticker)
    print("\n" + texto)
    for i in range(0, len(texto), 3900):
        notify(texto[i : i + 3900])


if __name__ == "__main__":
    main()
