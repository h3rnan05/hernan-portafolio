#!/usr/bin/env python3
"""Manda UN /trade TICKER de muestra a Telegram, reusando
telegram_bot.trade_command.generar_trade() real -- mismos datos, mismo
código que produce /trade en producción. No depende del webhook de
Telegram desplegado en Render (que corre como servicio aparte): esto
genera el tablero directo y lo manda vía notify(), igual que
preview_options.py hizo con /options.

USO
  python preview_trade.py TICKER

VARIABLES DE ENTORNO
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (requeridas para que se envíe de verdad)
  ANTHROPIC_API_KEY                      (opcional: sin ella, la explicación dice "no disponible")
  DISCORD_WEBHOOK                        (opcional)
"""

from __future__ import annotations

import argparse

from telegram_bot.trade_command import generar_trade
from wizards_bot import notify


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    args = ap.parse_args()

    texto = generar_trade(args.ticker)
    print("\n" + texto)
    for i in range(0, len(texto), 3900):
        notify(texto[i : i + 3900])


if __name__ == "__main__":
    main()
