"""CLI de la sombra. Ningún subcomando escribe la watchlist ni coloca órdenes.

    python -m shadow_alpaca noticias
    python -m shadow_alpaca screener
    python -m shadow_alpaca historia descargar --simbolos AAPL --almacen /var/lib/momentum/shadow_alpaca/sip
    python -m shadow_alpaca historia diff --simbolos AAPL --almacen /var/lib/momentum/shadow_alpaca/sip
    python -m shadow_alpaca informe --sesiones 5
    python -m shadow_alpaca tasa_captura --telegram
"""

from __future__ import annotations

import sys

from shadow_alpaca import historia, informe, noticias, screener, tasa_captura


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "sombra Alpaca (solo data.alpaca.markets)\n"
            "  noticias     B1: noticias Benzinga + Yahoo, el detector del hunter\n"
            "  screener     B2: most-actives y movers, solapes en JSONL\n"
            "  historia     B3: barras SIP a un almacén local, y diff contra Yahoo\n"
            "  informe      resumen en español de N sesiones de B1/B2\n"
            "  tasa_captura de los movers reales del día, cuántos vio el bot (CSV + resumen)\n",
        )
        return 0 if args else 2
    cmd, resto = args[0], args[1:]
    if cmd == "noticias":
        return noticias.main(resto)
    if cmd == "screener":
        return screener.main(resto)
    if cmd == "historia":
        return historia.main(resto)
    if cmd == "informe":
        return informe.main(resto)
    if cmd == "tasa_captura":
        return tasa_captura.main(resto)
    print(f"subcomando desconocido: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
