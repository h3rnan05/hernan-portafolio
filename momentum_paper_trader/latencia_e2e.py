"""Latencia de punta a punta: vela de ruptura → orden enviada, por tramo.

SOLO REPORTA. No cambia ningún tiempo, ni la cadencia del vigía, ni un
umbral: dice dónde se va el tiempo para que una persona decida.

La cadena ya estaba instrumentada, pero repartida en dos archivos:
`revisiones.json` (del ejecutor) y la entrada TRIGGERED de la watchlist
(del hunter). Este módulo la junta por (ticker, creado_en) y la parte en
tramos que SUMAN EXACTAMENTE el total:

  1. patron        velas_desde_ruptura × 60 s. La vela de ruptura hasta
                   la vela que confirmó. Es de la ESTRATEGIA (cuántas velas
                   pide el patrón), no del sistema.
  2. vela          60 s fijos: la vela que confirmó tiene que cerrar.
  3. dato          cierre de esa vela → `data_received_ts`. Atraso del
                   feed + espera hasta el tick del vigía (cada 60 s).
  4. evaluacion    `data_received_ts` → `evaluador_ts`.
  5. persistir     `evaluador_ts` → `watchlist_escrito_ts`.
  6. entrega       `watchlist_escrito_ts` → `executor_leido_ts` (hunter → ejecutor).
                   OJO: `executor_leido_ts` se sella en la corrida que por fin
                   revisa la señal. Si antes la bloqueó una compuerta (máximo
                   de posiciones, efectivo, etc.), esa espera cae aquí. Para
                   separarla hacen falta los eventos del ejecutor
                   (/var/lib/momentum/events.jsonl, fuera de git).
  7. ia            `executor_leido_ts` → `ia_decision_ts` (el LLM).
  8. orden         `ia_decision_ts` → orden aceptada (`timestamp`).

Los relojes vienen con resolución de 1 s. Un tramo negativo es un reloj
inconsistente: se reporta y ese tramo no entra a la estadística (no se
recorta a 0). Un reloj ausente deja el tramo sin medir; no se inventa.

Uso: `python -m momentum_paper_trader.latencia_e2e [--desde AAAA-MM-DD]
[--relojes extra.json] [--salida archivo.json]`. `--relojes` es un
{"TICKER|creado_en": {campos de la watchlist}} para órdenes cuya entrada
ya se purgó de la watchlist (se reconstruye del historial de git).
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

TRAMOS = ("patron", "vela", "dato", "evaluacion", "persistir", "entrega", "ia", "orden")
# Lo que el sistema puede acortar sin cambiar la estrategia.
TRAMOS_DEL_SISTEMA = ("dato", "evaluacion", "persistir", "entrega", "ia", "orden")
NOMBRES = {
    "patron": "confirmación del patrón (estrategia)",
    "vela": "cierre de la vela que confirmó",
    "dato": "feed + espera del tick",
    "evaluacion": "evaluación del hunter",
    "persistir": "escribir la watchlist",
    "entrega": "hunter → ejecutor",
    "ia": "decisión de la IA",
    "orden": "colocar la orden",
}


def _t(valor) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _seg(a: datetime | None, b: datetime | None) -> float | None:
    return None if a is None or b is None else (b - a).total_seconds()


@dataclass
class Cadena:
    ticker: str
    creado_en: str
    orden_ts: str
    tramos: dict[str, float | None] = field(default_factory=dict)
    total_s: float | None = None
    inconsistentes: list[str] = field(default_factory=list)


def cadena(revision: dict, wl: dict | None) -> Cadena:
    wl = wl or {}
    velas = wl.get("velas_desde_ruptura")
    evento = _t(revision.get("market_event_ts") or wl.get("market_event_ts"))
    cierre_vela = evento + timedelta(seconds=60) if evento else None
    recibido, evaluado = _t(wl.get("data_received_ts")), _t(wl.get("evaluador_ts"))
    escrito = _t(revision.get("watchlist_escrito_ts") or wl.get("watchlist_escrito_ts"))
    leido, ia = _t(revision.get("executor_leido_ts")), _t(revision.get("ia_decision_ts"))
    orden = _t(revision.get("timestamp"))
    c = Cadena(revision.get("ticker", "?"), revision.get("creado_en", "?"), revision.get("timestamp", "?"))
    c.tramos = {
        "patron": float(velas) * 60.0 if isinstance(velas, (int, float)) and velas >= 0 else None,
        "vela": 60.0 if evento else None,
        "dato": _seg(cierre_vela, recibido),
        "evaluacion": _seg(recibido, evaluado),
        "persistir": _seg(evaluado, escrito),
        "entrega": _seg(escrito, leido),
        "ia": _seg(leido, ia),
        "orden": _seg(ia, orden),
    }
    for k, v in c.tramos.items():
        if v is not None and v < 0:
            c.inconsistentes.append(k)
    if c.tramos["patron"] is not None and evento and orden:
        c.total_s = c.tramos["patron"] + (orden - evento).total_seconds()
    return c


def _pct(valores: list[float], q: float) -> float:
    orden = sorted(valores)
    return orden[min(len(orden) - 1, int(round(q * (len(orden) - 1))))]


def _maximos(tramos: dict[str, dict], clave: str) -> list[str]:
    """Todos los tramos empatados en el máximo (con 1 s de resolución los
    empates son reales, y elegir uno sería inventar un ganador)."""
    if not tramos:
        return []
    tope = max(v[clave] for v in tramos.values())
    return [k for k in TRAMOS if k in tramos and tramos[k][clave] == tope]


def agregar(cadenas: list[Cadena]) -> dict:
    por_tramo = {}
    for k in TRAMOS:
        vals = [c.tramos[k] for c in cadenas if c.tramos.get(k) is not None and k not in c.inconsistentes]
        por_tramo[k] = None if not vals else {
            "n": len(vals), "mediana_s": statistics.median(vals), "p90_s": _pct(vals, 0.9),
            "max_s": max(vals), "total_s": sum(vals)}
    totales = [c.total_s for c in cadenas if c.total_s is not None]
    medidos = {k: v for k, v in por_tramo.items() if v}
    sistema = {k: v for k, v in medidos.items() if k in TRAMOS_DEL_SISTEMA}
    return {
        "ordenes": len(cadenas),
        "con_cadena_completa": sum(1 for c in cadenas if all(v is not None for v in c.tramos.values())),
        "total": None if not totales else {
            "n": len(totales), "mediana_s": statistics.median(totales), "p90_s": _pct(totales, 0.9),
            "max_s": max(totales), "min_s": min(totales)},
        "tramos": por_tramo,
        # Por mediana (lo típico) y por p90 (la cola que hace llegar tarde).
        "mas_lento_mediana": _maximos(medidos, "mediana_s"),
        "mas_lento_sistema_mediana": _maximos(sistema, "mediana_s"),
        "mas_lento_sistema_p90": _maximos(sistema, "p90_s"),
        "inconsistentes": {f"{c.ticker}|{c.creado_en}": c.inconsistentes for c in cadenas if c.inconsistentes},
        "cola": _cola(cadenas, totales),
    }


def _cola(cadenas: list[Cadena], totales: list[float]) -> list[str]:
    """Las órdenes por encima del p90 y qué tramo se comió su tiempo."""
    if len(totales) < 2:
        return []
    corte = _pct(totales, 0.9)
    out = []
    for c in sorted(cadenas, key=lambda c: -(c.total_s or 0)):
        if c.total_s is None or c.total_s < corte:
            continue
        medidos = {k: v for k, v in c.tramos.items() if v is not None and k not in c.inconsistentes}
        peor = max(medidos, key=medidos.get)
        out.append(f"{c.ticker} {c.orden_ts[:16]} total {_m(c.total_s)} -- {NOMBRES[peor]} {_m(medidos[peor])}")
    return out


def _m(s: float | None) -> str:
    return "—" if s is None else (f"{s:.0f} s" if s < 90 else f"{s / 60:.1f} min")


def formatear(r: dict) -> str:
    lineas = [f"Latencia vela de ruptura → orden enviada · {r['ordenes']} órdenes "
              f"({r['con_cadena_completa']} con todos los relojes)"]
    t = r["total"]
    if t:
        lineas.append(f"TOTAL: mediana {_m(t['mediana_s'])} · p90 {_m(t['p90_s'])} · "
                      f"mín {_m(t['min_s'])} · máx {_m(t['max_s'])}")
    lineas.append(f"{'tramo':40} {'n':>3} {'mediana':>9} {'p90':>9} {'máx':>9}  % del tiempo")
    suma = sum(v["total_s"] for v in r["tramos"].values() if v)
    for k in TRAMOS:
        v = r["tramos"][k]
        if not v:
            lineas.append(f"{NOMBRES[k]:40} {'0':>3} {'sin medir':>9}")
            continue
        parte = v["total_s"] / suma if suma else 0
        lineas.append(f"{NOMBRES[k]:40} {v['n']:>3} {_m(v['mediana_s']):>9} {_m(v['p90_s']):>9} "
                      f"{_m(v['max_s']):>9}  {parte:5.0%}")
    def _n(ks: list[str]) -> str:
        return " = ".join(NOMBRES[k] for k in ks)

    if r["mas_lento_mediana"]:
        lineas.append(f"\nMás lento en total (mediana): {_n(r['mas_lento_mediana'])}")
    if r["mas_lento_sistema_mediana"]:
        lineas.append(f"Más lento del SISTEMA (mediana): {_n(r['mas_lento_sistema_mediana'])} · "
                      f"(p90): {_n(r['mas_lento_sistema_p90'])}")
    lentas = [c for c in r.get("cola", [])]
    for c in lentas:
        lineas.append(f"Cola: {c}")
    if r["inconsistentes"]:
        lineas.append(f"Relojes inconsistentes (tramo negativo, fuera de la estadística): {r['inconsistentes']}")
    return "\n".join(lineas)


def construir(revisiones: list[dict], relojes: dict[str, dict], desde: str | None = None) -> list[Cadena]:
    out = []
    for r in revisiones:
        if not r.get("entro") or not r.get("order_id") or not r.get("market_event_ts"):
            continue
        if desde and str(r.get("timestamp", ""))[:10] < desde:
            continue
        out.append(cadena(r, relojes.get(f"{r.get('ticker')}|{r.get('creado_en')}")))
    return out


def _relojes_de_watchlist() -> dict[str, dict]:
    """Entradas vivas de la watchlist (retención de 7 días), con el
    overlay del VPS si está activo."""
    from momentum_hunter import watchlist
    entradas = (watchlist.cargar(apply_vps_state=True) if watchlist.vps_state_habilitado()
                else watchlist.cargar())
    return {f"{e.ticker}|{e.creado_en}": asdict(e) for e in entradas}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Latencia ruptura → orden por tramo (solo reporte).")
    ap.add_argument("--desde", default=None)
    ap.add_argument("--relojes", type=Path, default=None)
    ap.add_argument("--salida", type=Path, default=None)
    args = ap.parse_args(argv)
    from momentum_paper_trader import estado
    revisiones = [asdict(r) for r in estado.cargar()]
    relojes = _relojes_de_watchlist()
    if args.relojes:
        extra = json.loads(args.relojes.read_text(encoding="utf-8"))
        relojes.update({k: v for k, v in extra.items() if isinstance(v, dict)})
    cadenas = construir(revisiones, relojes, args.desde)
    r = agregar(cadenas)
    print(formatear(r))
    if args.salida:
        args.salida.write_text(json.dumps({"resumen": r, "cadenas": [asdict(c) for c in cadenas]},
                                          ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
