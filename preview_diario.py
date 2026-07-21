#!/usr/bin/env python3
"""Manda una muestra del mensaje diario corto (con diff/emoji/delta/Top N)
Y de /list, reusando el código real de screener/run.py y
screener/report.py -- mismos datos, mismo código que produce el mensaje
diario y /list en producción.

A diferencia de `screener.run.main()`, este script NUNCA escribe
shortlist_hoy.json ni hace commits: solo lee el archivo persistido real
(la última corrida de producción) como "anterior" para el diff, y manda
los dos mensajes a Telegram vía notify(). No toca el estado compartido.

USO
  python preview_diario.py --limit 40

VARIABLES DE ENTORNO
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (requeridas para que se envíe de verdad)
"""

from __future__ import annotations

import argparse
import json

from screener.config import CONFIG
from screener.data.provider import YahooProvider
from screener.report import calcular_diff, texto_telegram_corto, texto_telegram_lista_completa
from screener.run import SALIDA, correr
from wizards_bot import notify


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                     help="escanear solo los primeros N tickers (preview rápido)")
    args = ap.parse_args()

    ranking = correr(CONFIG, YahooProvider(), args.limit, con_fund=True)
    if not ranking:
        print("ranking vacío -- Yahoo pudo haber bloqueado los datos.")
        return

    universo_n = len(ranking)
    top = ranking[:CONFIG.top_n]

    anterior = None
    if SALIDA.exists():
        try:
            anterior = json.loads(SALIDA.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"no pude leer shortlist_hoy.json real para el diff: {e}")
    diff = calcular_diff(anterior, top)

    txt_corto = texto_telegram_corto(ranking, CONFIG, universo_n, diff)
    print("\n--- mensaje diario corto ---\n" + txt_corto)
    for i in range(0, len(txt_corto), 3900):
        notify(txt_corto[i:i + 3900])

    txt_lista = texto_telegram_lista_completa(top, universo_n)
    print("\n--- /list ---\n" + txt_lista)
    for i in range(0, len(txt_lista), 3900):
        notify(txt_lista[i:i + 3900])


if __name__ == "__main__":
    main()
