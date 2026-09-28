"""`python -m shadow_alpaca.backtest_v2 --almacen DIR --salida docs/backtests/x.md`

Por defecto: los últimos 12 meses hasta ayer, equity $5.000, slippage
0,15 % por lado, comisión cero, clasificación con el modelo del YAML.
`--sin-ia` corre el embudo sin clasificar (diagnóstico: ninguna señal
pasa el paso 7). La salida debe caer fuera de la ruta de producción.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from estrategia_v2.config import cargar

from shadow_alpaca.backtest_v2 import datos, informe, motor
from shadow_alpaca.backtest_v2.clasificador import Clasificador, llamada_anthropic
from shadow_alpaca.cliente import ClienteDatos
from shadow_alpaca.jsonl_log import exigir_directorio_aislado

log = logging.getLogger("shadow_alpaca.backtest_v2")

# Muy por debajo de NYSE+NASDAQ+AMEX (~7.500) y muy por encima de la semilla
# de `universe.py`: es la frontera entre "universo real" y "descarga caída".
MINIMO_UNIVERSO = 1000


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ayer = datetime.now(UTC).date() - timedelta(days=1)
    ap.add_argument("--desde", type=date.fromisoformat, default=ayer - timedelta(days=365))
    ap.add_argument("--hasta", type=date.fromisoformat, default=ayer)
    ap.add_argument("--almacen", type=Path, required=True)
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--equity", type=float, default=motor.Parametros.equity_inicial)
    ap.add_argument("--slippage", type=float, default=motor.Parametros.slippage)
    ap.add_argument("--max-llamadas-ia", type=int, default=None)
    ap.add_argument("--sin-ia", action="store_true")
    ap.add_argument("--refrescar-universo", action="store_true")
    ap.add_argument("--simbolos", default=None, help="limitar a estos tickers (pruebas)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    exigir_directorio_aislado(args.almacen)
    exigir_directorio_aislado(args.salida.parent)
    cfg = cargar()
    from momentum_hunter import universe
    universo = universe.cargar(refrescar=args.refrescar_universo, excluir_etf=False)
    if args.simbolos:
        pedidos = {s.strip().upper() for s in args.simbolos.split(",")}
        universo = [u for u in universo if u.ticker in pedidos]
    elif len(universo) < MINIMO_UNIVERSO:
        # `universe.cargar` cae a una semilla de una docena de símbolos si
        # la descarga o su caché fallan. Un backtest sobre eso no es "sin
        # señales": es otro experimento. Se aborta en vez de informar.
        log.error("universo de %d símbolos (< %d): la descarga falló; no se corre", len(universo),
                  MINIMO_UNIVERSO)
        return 2
    llamar = None if args.sin_ia else llamada_anthropic(cfg.catalizador.modelo)
    clasificador = Clasificador(args.almacen, llamar, args.max_llamadas_ia)
    params = motor.Parametros(equity_inicial=args.equity, slippage=args.slippage)
    res = motor.correr(cfg, ClienteDatos(), datos.Cache(args.almacen), universo, args.desde, args.hasta,
                       clasificador, params)
    notas = list(informe.LIMITACIONES)
    if args.sin_ia:
        notas.insert(0, "CORRIDA DE DIAGNÓSTICO --sin-ia: el catalizador no se clasificó; no es el resultado de la v2.")
    if args.simbolos:
        notas.insert(0, f"Universo recortado a: {args.simbolos}.")
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(informe.markdown(res, cfg, args.desde, args.hasta, params, informe.Criterios(), notas),
                           encoding="utf-8")
    log.info("informe: %s · trades=%d · llamadas IA=%d", args.salida, len(res.trades), clasificador.llamadas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
