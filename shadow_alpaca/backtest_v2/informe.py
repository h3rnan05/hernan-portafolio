"""Métricas e informe markdown del backtest v2.

Las métricas son sobre R (resultado / riesgo planeado del trade) y sobre
el equity simulado. Los criterios de aprobación son del dueño
(2026-09-28) y viven aquí, no en la config de la estrategia: son la vara
con la que se mide la v2, no un parámetro de la v2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MTY = ZoneInfo("America/Monterrey")


@dataclass(frozen=True)
class Criterios:
    trades_min: int = 150
    expectativa_min_r: float = 0.2
    factor_beneficio_min: float = 1.3
    drawdown_max: float = 0.10


# Cortes del desglose (dimensiones del informe, no reglas de la estrategia).
FRANJAS = ((time(9, 36), time(10, 0), "09:36–09:59"), (time(10, 0), time(10, 30), "10:00–10:29"),
           (time(10, 30), time(11, 1), "10:30–11:00"))
RANGOS_PRECIO = ((2, 5, "$2–5"), (5, 10, "$5–10"), (10, 20, "$10–20"), (20, 50.01, "$20–50"))


def metricas(trades, curva=None) -> dict:
    rs = [t.r for t in trades]
    gan = [r for r in rs if r > 0]
    per = [r for r in rs if r <= 0]
    suma_per = -sum(per)
    dd = None
    if curva:
        pico, dd = curva[0][1], 0.0
        for _, v in curva:
            pico = max(pico, v)
            dd = max(dd, (pico - v) / pico if pico > 0 else 0.0)
    return {
        "trades": len(rs),
        "acierto": len(gan) / len(rs) if rs else None,
        "ganancia_media_r": sum(gan) / len(gan) if gan else None,
        "perdida_media_r": sum(per) / len(per) if per else None,
        "expectativa_r": sum(rs) / len(rs) if rs else None,
        "factor_beneficio": (sum(gan) / suma_per) if suma_per > 0 else (None if not gan else float("inf")),
        "drawdown_max": dd,
        "mfe_r": sum(t.mfe_r for t in trades) / len(trades) if trades else None,
        "mae_r": sum(t.mae_r for t in trades) / len(trades) if trades else None,
        "pnl": sum(t.pnl for t in trades),
    }


def evaluar(m: dict, c: Criterios) -> list[tuple[str, str, bool]]:
    def ok(v, cond):
        return v is not None and cond(v)
    return [
        (f"≥ {c.trades_min} trades", f"{m['trades']}", m["trades"] >= c.trades_min),
        (f"expectativa > {c.expectativa_min_r} R", _r(m["expectativa_r"]),
         ok(m["expectativa_r"], lambda v: v > c.expectativa_min_r)),
        (f"factor de beneficio > {c.factor_beneficio_min}", _f(m["factor_beneficio"]),
         ok(m["factor_beneficio"], lambda v: v > c.factor_beneficio_min)),
        (f"drawdown < {c.drawdown_max:.0%}", _p(m["drawdown_max"]),
         ok(m["drawdown_max"], lambda v: v < c.drawdown_max)),
    ]


def _r(v):
    return "—" if v is None else f"{v:+.2f} R"


def _p(v):
    return "—" if v is None else f"{v:.1%}"


def _f(v):
    return "—" if v is None else ("∞" if v == float("inf") else f"{v:.2f}")


def _fila(nombre, trades) -> str:
    m = metricas(trades)
    return (f"| {nombre} | {m['trades']} | {_p(m['acierto'])} | {_r(m['ganancia_media_r'])} | "
            f"{_r(m['perdida_media_r'])} | {_r(m['expectativa_r'])} | {_f(m['factor_beneficio'])} |")


def _tabla(titulo, grupos) -> list[str]:
    out = [f"### {titulo}", "", "| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    out += [_fila(n, ts) for n, ts in grupos]
    return out + [""]


def desgloses(trades) -> list[str]:
    por_nivel = [(f"nivel {n}", [t for t in trades if t.senal.nivel == n]) for n in (1, 2)]
    franjas = []
    for ini, fin, nombre in FRANJAS:
        franjas.append((nombre, [t for t in trades if ini <= t.senal.momento.astimezone(NY).time() < fin]))
    precios = [(n, [t for t in trades if lo <= t.entrada_ref < hi]) for lo, hi, n in RANGOS_PRECIO]
    salidas = sorted({t.motivo for t in trades})
    por_salida = [(m, [t for t in trades if t.motivo == m]) for m in salidas]
    return (_tabla("Por nivel de catalizador", por_nivel)
            + _tabla("Por hora de entrada (NY; 09:36 NY = 13:36 UTC en verano = 07:36 Monterrey)", franjas)
            + _tabla("Por rango de precio", precios)
            + _tabla("Por motivo de salida", por_salida))


def markdown(res, cfg, desde, hasta, params, criterios: Criterios, notas: list[str]) -> str:
    m = metricas(res.trades, res.curva)
    veredicto = evaluar(m, criterios)
    pasa = all(ok for _, _, ok in veredicto)
    ahora = datetime.now(UTC)
    lineas = [
        f"# Backtest estrategia v2 — {desde} a {hasta}",
        "",
        f"Generado {ahora:%Y-%m-%d %H:%M} UTC / {ahora.astimezone(MTY):%H:%M} Monterrey (UTC−6). "
        f"Config: `{cfg.ruta.name}` (versión {cfg.version}, prompt v{cfg.catalizador.prompt_version}, "
        f"modelo `{cfg.catalizador.modelo}`). Equity inicial ${params.equity_inicial:,.0f}, "
        f"slippage {params.slippage:.2%} por lado, comisión {params.comision}.",
        "",
        f"## Veredicto: {'APRUEBA' if pasa else 'NO APRUEBA'}",
        "",
        "| criterio | resultado | ¿cumple? |",
        "|---|---:|:---:|",
    ]
    lineas += [f"| {n} | {v} | {'sí' if ok else 'no'} |" for n, v, ok in veredicto]
    lineas += [
        "",
        "## Resultados",
        "",
        "| métrica | valor |",
        "|---|---:|",
        f"| trades | {m['trades']} |",
        f"| tasa de acierto | {_p(m['acierto'])} |",
        f"| ganancia media | {_r(m['ganancia_media_r'])} |",
        f"| pérdida media | {_r(m['perdida_media_r'])} |",
        f"| expectativa por trade | {_r(m['expectativa_r'])} |",
        f"| factor de beneficio | {_f(m['factor_beneficio'])} |",
        f"| drawdown máximo | {_p(m['drawdown_max'])} |",
        f"| MFE promedio | {_r(m['mfe_r'])} |",
        f"| MAE promedio | {_r(m['mae_r'])} |",
        f"| P&L simulado | ${m['pnl']:,.2f} |",
        "",
        "## Desglose",
        "",
    ]
    lineas += desgloses(res.trades)
    lineas += ["## Embudo", "", "| etapa | símbolo-días |", "|---|---:|"]
    lineas += [f"| {k} | {v} |" for k, v in res.embudo.items()]
    lineas += ["", "Por qué no hubo señal (peor intento de cada símbolo-día, puede sumar más de uno):", ""]
    lineas += [f"- {k}: {v}" for k, v in res.descartes_senal.most_common()]
    if res.no_entradas:
        lineas += ["", "Señales que no entraron:", ""]
        lineas += [f"- {k}: {v}" for k, v in res.no_entradas.most_common()]
    if res.clasificaciones:
        lineas += ["", "Clasificaciones de la IA:", ""]
        lineas += [f"- {k}: {v}" for k, v in sorted(res.clasificaciones.items())]
    if res.sin_dato:
        lineas += ["", "Sin dato (excluido, nunca contado como cero):", ""]
        lineas += [f"- {k}: {v}" for k, v in res.sin_dato.most_common()]
    lineas += ["", "## Limitaciones (no se maquillan)", ""]
    lineas += [f"- {n}" for n in notas]
    return "\n".join(lineas) + "\n"


LIMITACIONES = [
    "Halts: no hay fuente histórica (A6 no existe). El filtro «sin halt en 30 min» NO se aplicó: optimista.",
    "Float, ETF y SPAC: los ACTUALES de Yahoo, no los de cada fecha.",
    "Universo: los listados de hoy; faltan los deslistados durante el año (sesgo de supervivencia, optimista).",
    "Entrada a la apertura de la vela siguiente a la señal + slippage; sin impacto de mercado ni rechazos.",
    "Stop y objetivo en la misma vela: cuenta el stop (conservador). En la vela del llenado solo se mira el stop.",
    "Equity para el tamaño = inicial + P&L realizado (sin el no realizado). Frenos sobre el realizado.",
    "Noticias: Benzinga vía Alpaca; una noticia sin hora de publicación no se usa.",
]
