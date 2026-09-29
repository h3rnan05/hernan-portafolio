"""Comparativo de variantes del backtest v2 a partir de los JSON de métricas.

`python -m shadow_alpaca.backtest_v2.comparar --salida docs/backtests/x.md base.json v1.json ...`

Cada JSON lo escribe `python -m shadow_alpaca.backtest_v2 --metricas`
(ver `informe.metricas_json`). Aquí solo se ordenan en tablas: no se
recalcula nada ni se interpreta; la lectura la hace una persona.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shadow_alpaca.backtest_v2.informe import RANGOS_PRECIO, SALIDAS, _f, _p, _r

MTY = ZoneInfo("America/Monterrey")


def _cargar(rutas: list[Path]) -> list[dict]:
    out = []
    for r in rutas:
        d = json.loads(r.read_text(encoding="utf-8"))
        if "variante" not in d or "metricas" not in d:
            raise ValueError(f"{r}: no es un JSON de métricas del backtest v2")
        out.append(d)
    orden = {"base": 0}
    return sorted(out, key=lambda d: (orden.get(d["variante"], 1), d["variante"]))


def _fila_metricas(nombre: str, m: dict) -> str:
    s = m.get("salidas", {})
    return (f"| {nombre} | {m['trades']} | {_p(m['acierto'])} | {_r(m['expectativa_r'])} | {_f(m['factor_beneficio'])} | "
            f"{_p(m['drawdown_max'])} | {s.get('stop', 0) + s.get('stop_gap', 0)} / {s.get('tiempo', 0)} / {s.get('objetivo', 0)} / "
            f"{s.get('breakeven', 0)} / {s.get('cierre', 0)} | {_r(m['mfe_r'])} / {_r(m['mfe_mediana_r'])} | "
            f"{_r(m['mae_r'])} / {_r(m['mae_mediana_r'])} |")


CABECERA = ("| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | "
            "MFE prom. / med. | MAE prom. / med. |")
SEPARADOR = "|---|---:|---:|---:|---:|---:|---|---|---|"


def markdown(datos: list[dict], desde: str, hasta: str) -> str:
    ahora = datetime.now(UTC)
    out = [f"# Backtest v2 — comparativo de variantes ({desde} a {hasta})", "",
           f"Generado {ahora:%Y-%m-%d %H:%M} UTC / {ahora.astimezone(MTY):%H:%M} Monterrey (UTC−6). "
           "Cada variante es UN cambio sobre la base; misma caché de clasificaciones, mismos datos. "
           "Salidas: stop / tiempo / objetivo / breakeven / cierre. MFE y MAE en R (promedio / mediana).", "",
           "## Variantes", ""]
    out += [f"- **{d['variante']}**: {d['descripcion']}" for d in datos]
    out += ["", "## Resumen", "", CABECERA, SEPARADOR]
    out += [_fila_metricas(d["variante"], d["metricas"]) for d in datos]
    out += ["", "## MFE de los trades que salieron por tiempo", "",
            "Cuánto llegó a ir a favor cada uno antes del stop de tiempo (trades por tramo).", ""]
    tramos = list(datos[0]["mfe_tiempo"]) if datos else []
    out += ["| variante | " + " | ".join(tramos) + " | total |", "|---|" + "---:|" * (len(tramos) + 1)]
    for d in datos:
        mt = d["mfe_tiempo"]
        out.append(f"| {d['variante']} | " + " | ".join(str(mt.get(t, 0)) for t in tramos) + f" | {sum(mt.values())} |")
    out += ["", "## Por rango de precio", "", CABECERA, SEPARADOR]
    for _, _, nombre in RANGOS_PRECIO:
        for d in datos:
            out.append(_fila_metricas(f"{d['variante']} {nombre}", d["por_precio"][nombre]))
    out += ["", "## Por nivel de catalizador", "", CABECERA, SEPARADOR]
    for nivel in ("nivel 1", "nivel 2"):
        for d in datos:
            out.append(_fila_metricas(f"{d['variante']} {nivel}", d["por_nivel"][nivel]))
    out += ["", "## Señales y no entradas", "", "| variante | señales | " + " | ".join(SALIDAS) + " | no entradas |",
            "|---|---:|" + "---:|" * len(SALIDAS) + "---|"]
    for d in datos:
        s = d["metricas"].get("salidas", {})
        ne = ", ".join(f"{k} {v}" for k, v in sorted(d.get("no_entradas", {}).items())) or "—"
        out.append(f"| {d['variante']} | {d['senales']} | " + " | ".join(str(s.get(m, 0)) for m in SALIDAS) + f" | {ne} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("metricas", nargs="+", type=Path)
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--desde", default="")
    ap.add_argument("--hasta", default="")
    args = ap.parse_args(argv)
    datos = _cargar(args.metricas)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(markdown(datos, args.desde, args.hasta), encoding="utf-8")
    print(f"comparativo: {args.salida} ({len(datos)} variantes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
