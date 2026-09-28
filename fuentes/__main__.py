"""`python -m fuentes grabar <fuente> [args]`: graba respuestas reales.

Cada fuente registra su subcomando en `fuentes.cli.COMANDOS` al
agregarse (un PR por fuente); ver `cli.py` para por qué no vive aquí. Se corre desde el VPS, que sí llega a las fuentes; escribe en
`fuentes/tests/respuestas/<fuente>/` con `ficticio: false` y, cuando la
fuente tiene una verificación (zona horaria, historia disponible), la
imprime. No toca la caché de producción ni la watchlist.
"""

from __future__ import annotations

import argparse
import logging
import sys

from fuentes.cli import COMANDOS


def _cargar_comandos() -> None:
    # Import perezoso: cada módulo registra el suyo al importarse.
    import importlib
    for mod in ("fuentes.edgar", "fuentes.edgar_veto", "fuentes.edgar_form4", "fuentes.edgar_acciones",
                "fuentes.resultados", "fuentes.fda", "fuentes.finra_short", "fuentes.calendario_economico",
                "fuentes.halts_nasdaq"):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as ex:
            if ex.name != mod:
                raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fuentes", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="accion", required=True)
    g = sub.add_parser("grabar", help="graba respuestas reales de una fuente")
    g.add_argument("fuente")
    g.add_argument("resto", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _cargar_comandos()
    fn = COMANDOS.get(args.fuente)
    if fn is None:
        print(f"fuente desconocida: {args.fuente}. Disponibles: {', '.join(sorted(COMANDOS)) or '(ninguna)'}",
              file=sys.stderr)
        return 2
    return fn(args.resto)


if __name__ == "__main__":
    raise SystemExit(main())
