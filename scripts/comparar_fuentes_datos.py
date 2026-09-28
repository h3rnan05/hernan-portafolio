"""Compara el feed SIP (o el que diga ALPACA_DATA_FEED) contra Yahoo.

Solo lectura: barras y, si responde, el snapshot. No coloca órdenes ni
escribe la watchlist. Sirve para mirar la última vela, el volumen y el
VWAP que el hunter calcularía con cada fuente, y cuánto de ese volumen
y de ese VWAP es la subasta de apertura o de cierre que SIP no trae
dentro de la vela de minuto.

Uso (con las claves paper en el entorno, las mismas de /etc/momentum/paper.env):

    python scripts/comparar_fuentes_datos.py SPY AAPL NTLA

Sale 2 si el feed no se puede leer (sin claves, auth, red). No imprime
las claves ni un volumen de relleno.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# `python scripts/...` mete scripts/ en sys.path, no la raíz del repo.
_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from momentum_hunter.data.alpaca_datos import AlpacaProvider, ErrorDatosAlpaca
from momentum_hunter.data.comparar_fuentes import formatear
from momentum_hunter.data.provider import YahooProvider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compara barras del feed contra Yahoo. Solo lectura.")
    ap.add_argument("simbolos", nargs="+", help="tickers, como los pediría el hunter")
    ap.add_argument("--feed", default=None, help="sip o iex; si se omite, ALPACA_DATA_FEED o sip")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    import os
    feed = (args.feed or os.environ.get("ALPACA_DATA_FEED", "sip")).strip().lower()
    if feed not in ("sip", "iex"):
        print("ALPACA_DATA_FEED tiene que ser sip o iex", file=sys.stderr)
        return 2

    yahoo = YahooProvider(pausa=0.0)
    alpaca = AlpacaProvider(feed=feed, pausa=0.0)
    simbolos = [s.strip() for s in args.simbolos if s.strip()]
    try:
        intra_a = alpaca.barras_intradia(simbolos, "1m", "5d")
        diarias_a = alpaca.barras(simbolos, dias=40)
    except ErrorDatosAlpaca as ex:
        print(f"el feed no respondió ({ex.codigo}). No hay cifras de reemplazo.", file=sys.stderr)
        return 2
    try:
        snaps = alpaca.snapshots(simbolos)
    except ErrorDatosAlpaca as ex:
        print(f"snapshot no disponible ({ex.codigo}); la tabla sigue con las barras.", file=sys.stderr)
        snaps = {}
    intra_y = yahoo.barras_intradia(simbolos, "1m", "5d")
    diarias_y = yahoo.barras(simbolos, dias=40)
    print(formatear(
        simbolos, intra_a, intra_y, diarias_a, diarias_y, snaps, datetime.now(UTC),
        aportes_subasta=alpaca.aportes_subasta,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
