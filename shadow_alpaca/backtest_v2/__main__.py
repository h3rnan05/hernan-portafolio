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

from shadow_alpaca.backtest_v2 import datos, informe, motor, variantes
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
    ap.add_argument("--variante", default="base",
                    help="cambios contados sobre la config base, p. ej. e1 o e1_u1 (ver variantes.py)")
    ap.add_argument("--metricas", type=Path, default=None, help="JSON con las métricas para el comparativo")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    exigir_directorio_aislado(args.almacen)
    exigir_directorio_aislado(args.salida.parent)
    cfg = variantes.aplicar(cargar(), args.variante)
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
    params = motor.Parametros(equity_inicial=args.equity, slippage=args.slippage,
                              entrada=variantes.entrada_de(args.variante))
    partes = variantes.partes(args.variante)
    if "pead" in partes or "peadnoticia" in partes or "seguimiento" in partes:
        from shadow_alpaca.backtest_v2 import planb
        if "pead" in partes or "peadnoticia" in partes:
            res = planb.correr_pead(cfg, ClienteDatos(), datos.Cache(args.almacen), universo, args.desde, args.hasta, params,
                                    evento="titular" if "peadnoticia" in partes else "edgar")
        else:
            res = planb.correr_seguimiento(cfg, ClienteDatos(), datos.Cache(args.almacen), universo, args.desde, args.hasta,
                                           clasificador, params)
        notas = list(planb.LIMITACIONES_SEGUIMIENTO if "seguimiento" in partes else planb.LIMITACIONES_PEAD)
        if "peadnoticia" in partes:
            notas.insert(0, "Evento = titular de resultados de Benzinga entre el cierre previo y la apertura (sin IA), "
                            "porque sec.gov responde 403 a los runners de GitHub Actions. Es un proxy más ruidoso que el 8-K 2.02.")
    else:
        res = motor.correr(cfg, ClienteDatos(), datos.Cache(args.almacen), universo, args.desde, args.hasta,
                           clasificador, params)
        notas = list(informe.LIMITACIONES)
    if args.sin_ia:
        notas.insert(0, "CORRIDA DE DIAGNÓSTICO --sin-ia: el catalizador no se clasificó; no es el resultado de la v2.")
    if args.simbolos:
        notas.insert(0, f"Universo recortado a: {args.simbolos}.")
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    if "diagnostico" in variantes.partes(args.variante):
        from shadow_alpaca.backtest_v2 import diagnostico
        filas = diagnostico.analizar(res, cfg)
        args.salida.write_text(diagnostico.markdown(filas, args.desde, args.hasta), encoding="utf-8")
        if args.metricas is not None:
            ruta = args.metricas.with_name("diagnostico.json")
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(diagnostico.a_json(filas), encoding="utf-8")
        log.info("diagnóstico: %s · señales=%d", args.salida, len(filas))
        return 0
    args.salida.write_text(informe.markdown(res, cfg, args.desde, args.hasta, params, informe.Criterios(), notas, res.sesiones,
                                            args.variante, variantes.descripcion(args.variante)), encoding="utf-8")
    if args.metricas is not None:
        import json
        exigir_directorio_aislado(args.metricas.parent)
        args.metricas.parent.mkdir(parents=True, exist_ok=True)
        args.metricas.write_text(json.dumps(informe.metricas_json(res, args.variante, variantes.descripcion(args.variante)),
                                            ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("informe: %s · trades=%d · llamadas IA=%d · reintentos=%d · inválidas=%d", args.salida, len(res.trades),
             clasificador.llamadas, clasificador.reintentos, clasificador.invalidas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
