#!/usr/bin/env python3
"""Manda UN /options TICKER [--full] de muestra a Telegram, reusando
telegram_bot.options_command.generar_options() real -- mismos datos,
mismo código que produce /options en producción. No depende del webhook
de Telegram desplegado en Render (que corre como servicio aparte): esto
genera el memo directo y lo manda vía notify(), igual que
preview_reporte.py hizo con /report.

USO
  python preview_options.py TICKER [--full]

VARIABLES DE ENTORNO
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (requeridas para que se envíe de verdad)
  ANTHROPIC_API_KEY                      (opcional: sin ella, la explicación dice "no disponible")
  DISCORD_WEBHOOK                        (opcional)
"""

from __future__ import annotations

import argparse

from telegram_bot.options_command import generar_options
from wizards_bot import notify


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    texto = generar_options(args.ticker, modo="full" if args.full else "simple")
    print("\n" + texto)
    for i in range(0, len(texto), 3900):
        notify(texto[i : i + 3900])


if __name__ == "__main__":
    main()
