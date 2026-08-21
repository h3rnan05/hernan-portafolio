"""CLI del paper trader -- consume las señales TRIGGERED que ya resolvió
`momentum_hunter/run.py` y coloca órdenes bracket en una cuenta de
PRÁCTICA de Alpaca. Sistema completamente separado de momentum_hunter:
nunca modifica su watchlist, nunca re-evalúa una señal, nunca toca dinero
real (ver `alpaca_client.py`).

USO
  python -m momentum_paper_trader.run              # coloca órdenes paper reales (cuenta de práctica)
  python -m momentum_paper_trader.run --dry-run     # calcula y muestra, no coloca nada ni requiere credenciales
  python -m momentum_paper_trader.run --verificar-conexion  # GET /v2/account -- confirma que las
                                                      # credenciales conectan, nunca coloca una orden

VARIABLES DE ENTORNO
  ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET
      Credenciales del entorno PAPER de Alpaca (nunca live). Sin ellas,
      el comando termina sin hacer nada -- mismo principio que
      `momentum_hunter.run.enviar_telegram`: falta de secrets nunca es
      un error fatal, solo deja de operar."""

from __future__ import annotations

import argparse
import logging
import os

from momentum_paper_trader import seguimiento
from momentum_paper_trader.alpaca_client import AlpacaPaperClient
from momentum_paper_trader.config import CONFIG
from momentum_paper_trader.executor import ejecutar

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("momentum_paper_trader.run")


def _avisar_falla(ex: Exception) -> None:
    """Autonomía real significa que el sistema reporta sus PROPIAS fallas
    -- si el trader se rompe en silencio, el usuario vuelve a tener que
    revisar logs a mano, que es exactamente lo que esto elimina. Mejor
    esfuerzo: si hasta Telegram falla, al menos queda el log."""
    from momentum_hunter.run import enviar_telegram
    try:
        enviar_telegram(
            f"⚠️ [PAPER] El paper trader falló en esta corrida:\n"
            f"{type(ex).__name__}: {ex}\n\n"
            f"No se colocó nada nuevo. La próxima corrida del cron lo reintenta sola; "
            f"si este aviso se repite varias veces seguidas, algo necesita arreglo."
        )
    except Exception:
        log.exception("tampoco se pudo avisar la falla por Telegram")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                     help="calcula y muestra qué órdenes colocaría, sin llamar a Alpaca ni requerir credenciales")
    ap.add_argument("--verificar-conexion", action="store_true",
                     help="GET /v2/account de solo lectura -- confirma que las credenciales conectan, "
                          "nunca coloca una orden ni requiere que exista una señal TRIGGERED")
    args = ap.parse_args()

    api_key = os.getenv("ALPACA_PAPER_API_KEY")
    api_secret = os.getenv("ALPACA_PAPER_API_SECRET")
    if not args.dry_run and (not api_key or not api_secret):
        log.info("sin credenciales de Alpaca paper -- no hay nada que hacer")
        return

    client = AlpacaPaperClient(api_key or "", api_secret or "")

    if args.verificar_conexion:
        cuenta = client.info_cuenta()
        # `buying_power` puede ser un múltiplo del efectivo real si la
        # cuenta tiene margen habilitado (día-trading 4x es el default
        # de Alpaca) -- `cash`/`equity`/`portfolio_value` son los que de
        # verdad reflejan el capital depositado, para no confundir una
        # cosa con la otra al confirmar qué cuenta es esta.
        log.info(
            "conexión OK -- cuenta paper %s, estado=%s -- efectivo=$%s, equity=$%s, "
            "valor de cartera=$%s, poder de compra=$%s",
            cuenta.get("account_number", "?"), cuenta.get("status", "?"),
            cuenta.get("cash", "?"), cuenta.get("equity", "?"),
            cuenta.get("portfolio_value", "?"), cuenta.get("buying_power", "?"),
        )
        return

    try:
        # Primero el seguimiento de los trades YA colocados (entradas
        # llenadas, objetivos, stops -- con su aviso por Telegram cada
        # uno): las novedades de lo que ya está en juego importan aunque
        # esta corrida no traiga ninguna señal nueva. Solo con
        # credenciales reales -- en dry-run no hay órdenes que seguir.
        if not args.dry_run:
            cambiadas = seguimiento.revisar(client)
            if cambiadas:
                log.info("%d trade(s) cambiaron de estado", len(cambiadas))

        nuevas = ejecutar(client, CONFIG, dry_run=args.dry_run)
        log.info("%d orden(es) paper colocada(s)", len(nuevas))
    except Exception as ex:
        # El workflow corre este paso con continue-on-error -- la falla
        # se AVISA por Telegram (autonomía: el sistema reporta sus
        # propios problemas) y se relanza para que quede el traceback
        # completo en los logs de Actions, sin bloquear el persist de
        # momentum_hunter.
        _avisar_falla(ex)
        raise


if __name__ == "__main__":
    main()
