#!/usr/bin/env python3
"""Manda UN mensaje de muestra a Telegram con titulares reales (Google
News RSS), en el mismo formato que el digest informativo de
wizards_bot.py (noticias_digest) -- pero completamente aislado: no toca
wizards_state.json, no evalúa nada con LLM, no ejecuta ninguna lógica de
trading. Solo sirve para ver cómo se ve el formato con datos reales.

USO
  python preview_noticia.py

VARIABLES DE ENTORNO
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (requeridas para que se envíe de verdad)
  DISCORD_WEBHOOK                        (opcional)
"""

from __future__ import annotations

from wizards_bot import ahora, notify, titulares_google_news


def main() -> None:
    titulares = titulares_google_news("stock market today", maximo=5)
    if not titulares:
        notify(
            f"[{ahora()}] 📰 (muestra) No se pudieron obtener titulares ahora "
            f"mismo -- probablemente Google bloqueó la IP del datacenter, "
            f"como ya ha pasado antes con este mismo feed."
        )
        return
    mensaje = (
        f"[{ahora()}] 📰 Noticias -- mensaje de muestra, NO es un aviso real "
        f"del bot:\n" + "\n".join(f"  • {t}" for t in titulares)
    )
    notify(mensaje)


if __name__ == "__main__":
    main()
