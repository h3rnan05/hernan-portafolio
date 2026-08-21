"""Banco de pruebas sobre la auditoría ya guardada -- responde "¿qué
habría alertado el bot con OTROS parámetros?" sin tocar red ni esperar a
que pase otro día de mercado.

POR QUÉ EXISTE (2026-08-21). Auditando por qué el bot nunca alertó se
descubrió que `score_minimo_alerta` estaba en 85 cuando el `score_base`
más alto que su propia función de scoring produjo en 3.161 candidatas
reales fue 81,2 -- un umbral inalcanzable, no una decisión de
selectividad. Ese diagnóstico se hizo con scripts sueltos, imposibles de
repetir. Este módulo lo vuelve una herramienta: cada vez que se quiera
mover un umbral, se mide primero contra la historia real en vez de
adivinar.

QUÉ PUEDE Y QUÉ NO PUEDE MEDIR -- la distinción importa y no se maquilla:

  SÍ: la DECISIÓN final (`evaluator.evaluar` -> `accionable`) sobre
      evaluaciones ya calculadas. `accionable` combina cuatro
      condiciones (patrón, temprano, riesgo definido, score >= umbral) y
      las cuatro quedaron guardadas en la auditoría, así que recomponer
      el veredicto con otro umbral es exacto, no una aproximación.

  NO: los FACTORES de los que salen esas condiciones. La auditoría
      guarda los resultados del evaluador, no las velas crudas, así que
      no se puede re-derivar qué habría pasado con otro
      `umbral_rvol_intradia` o con otra definición de patrón -- eso
      exigiría volver a pedir los datos de mercado de esos días (Yahoo
      solo conserva ~60 días de velas de 1 minuto, y por-ticker para
      miles es inviable). Cambiar esos parámetros requiere dejar correr
      el bot y volver a medir.

  TAMPOCO: si la señal GANA DINERO. Esto mide qué habría alertado, no
      qué habría pasado después. El resultado real lo mide
      `outcomes.py` contra el mercado, y hoy no hay ni una alerta que
      medir. No confundir "habría alertado 5 veces" con "habría
      acertado 5 veces".

USO
  python -m momentum_hunter.replay                      # barrido de umbrales por defecto
  python -m momentum_hunter.replay --umbrales 55,60,65  # umbrales concretos
  python -m momentum_hunter.replay --detalle 60         # qué alertaría exactamente en ese umbral
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from momentum_hunter.audit import DIR_AUDITORIA

log = logging.getLogger("momentum_hunter.replay")

UMBRALES_POR_DEFECTO = (50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 85.0)


@dataclass(frozen=True)
class Evaluacion:
    """Una evaluación tal como quedó guardada -- solo los campos que
    deciden `accionable`, más lo necesario para identificarla."""
    dia: str
    timestamp: str
    ticker: str
    score_ajustado: float | None
    patron: str | None
    temprano: bool
    # `None` = no se sabe. `riesgo_definido` se empezó a guardar el
    # 2026-08-21 (ver `audit.snapshot_candidato`); en los registros
    # anteriores falta, y este módulo NUNCA lo asume -- los cuenta y los
    # reporta aparte en vez de inventar un veredicto (ver `Resultado`).
    riesgo_definido: bool | None


@dataclass(frozen=True)
class Resultado:
    umbral: float
    alertas: int              # evaluaciones que habrían disparado
    tickers: tuple[str, ...]  # tickers distintos involucrados
    dias: int                 # días distintos con al menos una alerta
    indeterminadas: int       # pasaban todo lo demás, pero sin `riesgo_definido` guardado


def cargar_evaluaciones(dir_auditoria: Path = DIR_AUDITORIA) -> list[Evaluacion]:
    """Todas las evaluaciones de todos los días auditados. Un archivo
    corrupto o un registro incompleto se omite en vez de tumbar el
    análisis (mismo principio que `watchlist.cargar`)."""
    evaluaciones: list[Evaluacion] = []
    if not dir_auditoria.exists():
        return evaluaciones
    for path in sorted(dir_auditoria.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("auditoría ilegible, se omite: %s", path.name)
            continue
        dia = path.stem
        for corrida in data.get("corridas", []):
            for c in corrida.get("candidatos", []):
                ev = c.get("evaluacion")
                if not isinstance(ev, dict):
                    continue
                evaluaciones.append(Evaluacion(
                    dia=dia,
                    timestamp=str(corrida.get("timestamp", "")),
                    ticker=str(c.get("ticker", "?")),
                    score_ajustado=ev.get("score_ajustado"),
                    patron=ev.get("patron"),
                    temprano=bool(ev.get("temprano")),
                    riesgo_definido=ev.get("riesgo_definido"),
                ))
    return evaluaciones


def _pasa_lo_demas(e: Evaluacion) -> bool:
    """Las condiciones de `accionable` que NO dependen del umbral."""
    return e.patron is not None and e.temprano


def simular(evaluaciones: list[Evaluacion], umbral: float) -> Resultado:
    """Qué habría alertado con `score_minimo_alerta = umbral`, aplicando
    exactamente las mismas condiciones que `evaluator.evaluar`.

    Las que pasan todo menos `riesgo_definido` (porque ese campo no se
    guardaba antes del 2026-08-21) se cuentan como INDETERMINADAS, no
    como alertas ni como descartes: son el margen de error honesto de
    esta simulación sobre datos viejos."""
    alertas: list[Evaluacion] = []
    indeterminadas = 0
    for e in evaluaciones:
        if e.score_ajustado is None or e.score_ajustado < umbral:
            continue
        if not _pasa_lo_demas(e):
            continue
        if e.riesgo_definido is None:
            indeterminadas += 1
            continue
        if not e.riesgo_definido:
            continue
        alertas.append(e)
    return Resultado(
        umbral=umbral,
        alertas=len(alertas),
        tickers=tuple(sorted({a.ticker for a in alertas})),
        dias=len({a.dia for a in alertas}),
        indeterminadas=indeterminadas,
    )


def dias_auditados(evaluaciones: list[Evaluacion]) -> int:
    return len({e.dia for e in evaluaciones})


def barrido(
    evaluaciones: list[Evaluacion], umbrales: tuple[float, ...] = UMBRALES_POR_DEFECTO,
) -> list[Resultado]:
    return [simular(evaluaciones, u) for u in umbrales]


def formatear_barrido(resultados: list[Resultado], dias: int) -> str:
    """Rango [confirmadas, confirmadas+indeterminadas] en vez de un solo
    número: sobre datos anteriores al 2026-08-21 falta `riesgo_definido`,
    así que el conteo exacto no se puede saber. Reportar solo el mínimo
    (0) daría la impresión falsa de que ningún umbral alerta nunca;
    reportar el máximo asumiría que todas tenían riesgo definido -- que
    es exactamente el supuesto no declarado que hizo el análisis a mano
    del 2026-08-21. El rango es lo único honesto."""
    lineas = [
        f"{'umbral':>7} {'alertas (rango)':>17} {'tickers':>9} {'días':>6} {'≈/semana':>16}",
        "-" * 60,
    ]
    for r in resultados:
        alto = r.alertas + r.indeterminadas
        rango = f"{r.alertas}" if r.indeterminadas == 0 else f"{r.alertas} - {alto}"
        sem_bajo = (r.alertas / dias * 5) if dias else 0.0
        sem_alto = (alto / dias * 5) if dias else 0.0
        sem = (f"{sem_bajo:.1f}" if r.indeterminadas == 0
               else f"{sem_bajo:.1f} - {sem_alto:.1f}")
        lineas.append(
            f"{r.umbral:7.0f} {rango:>17} {len(r.tickers):9} {r.dias:6} {sem:>16}")
    return "\n".join(lineas)


def _detalle(evaluaciones: list[Evaluacion], umbral: float) -> str:
    filas = sorted(
        (e for e in evaluaciones
         if e.score_ajustado is not None and e.score_ajustado >= umbral and _pasa_lo_demas(e)),
        key=lambda e: -(e.score_ajustado or 0.0),
    )
    if not filas:
        return f"Ninguna evaluación habría alertado con umbral {umbral:.0f}."
    vistos: set[str] = set()
    lineas = [f"{'score':>7}  {'día':12} {'ticker':8} {'patrón':24} riesgo_definido"]
    for e in filas:
        if e.ticker in vistos:
            continue
        vistos.add(e.ticker)
        rd = "?" if e.riesgo_definido is None else ("sí" if e.riesgo_definido else "no")
        lineas.append(
            f"{e.score_ajustado:7.1f}  {e.dia:12} {e.ticker:8} {str(e.patron):24} {rd}")
    return "\n".join(lineas)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--umbrales", help="lista separada por comas, ej. 55,60,65")
    ap.add_argument("--detalle", type=float, help="muestra qué alertaría exactamente en ese umbral")
    args = ap.parse_args()

    evaluaciones = cargar_evaluaciones()
    if not evaluaciones:
        print("No hay auditoría que replayear todavía.")
        return
    dias = dias_auditados(evaluaciones)
    print(f"{len(evaluaciones)} evaluaciones en {dias} días auditados\n")

    if args.detalle is not None:
        print(_detalle(evaluaciones, args.detalle))
        return

    umbrales = (tuple(float(x) for x in args.umbrales.split(","))
                if args.umbrales else UMBRALES_POR_DEFECTO)
    print(formatear_barrido(barrido(evaluaciones, umbrales), dias))
    indet = max((r.indeterminadas for r in barrido(evaluaciones, umbrales)), default=0)
    if indet:
        print(f"\n'indet.' = pasaban todo lo demás pero no se guardó `riesgo_definido` "
              f"(campo agregado el 2026-08-21).\nNo se cuentan como alertas: son el margen "
              f"de error real de esta simulación sobre datos viejos.")
    print("\nEsto mide QUÉ habría alertado, no si habría ganado dinero "
          "(ver docstring del módulo).")


if __name__ == "__main__":
    main()
