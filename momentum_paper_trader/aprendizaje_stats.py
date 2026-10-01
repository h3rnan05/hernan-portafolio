"""Estadísticas por segmento para el aprendizaje diario (PR-B).

Funciones PURAS sobre la memoria de trades (`memoria_trades.py`). No leen
archivos, no deciden y no cambian ningún umbral: solo dicen qué tan
fuerte es la evidencia de cada segmento. Quien propone ajustes es
`autoajuste.py`, y todo ajuste vive en sombra hasta que el dueño dé GO.

Guardas (diseño del 2026-10-01):
  - n mínimo y sesiones distintas mínimas: debajo de eso el segmento
    queda en `observacion` aunque los números se vean feos.
  - R contraído hacia la media global: R_shr = (n·R + k·μ)/(n + k).
  - Win rate con intervalo de Wilson y posterior Beta(2,2).
  - IC de R por bootstrap POR DÍA (los trades del mismo día están
    correlacionados; tratarlos como independientes exagera la evidencia).
  - Corrección de Bonferroni por la cantidad de segmentos evaluados.

Un trade sin R (`None`) no entra al cálculo: un dato faltante no es 0."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import NormalDist

N_MINIMO = 20
SESIONES_MINIMAS = 5
K_CONTRACCION = 10
R_SHR_MAXIMO_PARA_PROPONER = -0.25
ALFA = 0.10
BOOTSTRAP_ITER = 2000
SEMILLA = 20261001

DIMENSIONES = ("patron", "catalizador_tipo", "es_large_cap", "banda_precio", "confianza",
               "franja_et", "espera_slot", "regimen")

ESTADO_SIN_MUESTRA = "sin_muestra"
ESTADO_OBSERVACION = "observacion"
ESTADO_PROPUESTA = "propuesta"
ESTADO_SIN_EVIDENCIA = "sin_evidencia_negativa"


def valor_dimension(trade: dict, dim: str):
    """Valor del trade en esa dimensión; None = sin dato (segmento aparte)."""
    if dim == "espera_slot":
        e = trade.get("espera_desde_disparo_min")
        if e is None:
            return None
        return ">=10min" if e >= 10 else "<10min"
    if dim == "regimen":
        return trade.get("regimen_nivel") or trade.get("clima_mercado")
    return trade.get(dim)


def wilson(ganadores: int, n: int, alfa: float = ALFA) -> tuple[float, float] | None:
    if n <= 0:
        return None
    z = NormalDist().inv_cdf(1 - alfa / 2)
    p = ganadores / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def beta_posterior(ganadores: int, n: int, a: float = 2.0, b: float = 2.0) -> float:
    return (ganadores + a) / (n + a + b)


def contraer(media: float | None, n: int, mu: float | None, k: float = K_CONTRACCION) -> float | None:
    if mu is None:
        return media
    if n <= 0 or media is None:
        return mu
    return (n * media + k * mu) / (n + k)


def bootstrap_por_dia(trades: list[dict], alfa: float = ALFA, iteraciones: int = BOOTSTRAP_ITER,
                      semilla: int = SEMILLA) -> tuple[float, float] | None:
    """IC de la media de R remuestreando DÍAS completos. Determinista con
    semilla. None con menos de 2 días (no hay variación que medir)."""
    por_dia: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        if t.get("r") is not None and t.get("fecha"):
            por_dia[t["fecha"]].append(float(t["r"]))
    dias = sorted(por_dia)
    if len(dias) < 2:
        return None
    rng = random.Random(semilla)
    medias = []
    for _ in range(iteraciones):
        s: list[float] = []
        for _d in dias:
            s.extend(por_dia[rng.choice(dias)])
        medias.append(sum(s) / len(s))
    medias.sort()
    lo = medias[int(math.floor(alfa / 2 * iteraciones))]
    hi = medias[min(iteraciones - 1, int(math.ceil((1 - alfa / 2) * iteraciones)) - 1)]
    return (lo, hi)


def resumen(trades: list[dict], mu: float | None = None, alfa: float = ALFA) -> dict:
    validos = [t for t in trades if t.get("r") is not None]
    n = len(validos)
    if n == 0:
        return {"n": 0, "sesiones": 0, "ganadores": 0, "wr": None, "wr_ic": None, "wr_post": None,
                "r_medio": None, "r_shr": mu, "r_ic": None, "pnl": None, "expectativa_usd": None}
    rs = [float(t["r"]) for t in validos]
    g = sum(1 for r in rs if r > 0)
    pnls = [t["pnl"] for t in validos if t.get("pnl") is not None]
    media = sum(rs) / n
    return {
        "n": n, "sesiones": len({t.get("fecha") for t in validos if t.get("fecha")}),
        "ganadores": g, "wr": g / n, "wr_ic": wilson(g, n, alfa), "wr_post": beta_posterior(g, n),
        "r_medio": media, "r_shr": contraer(media, n, mu), "r_ic": bootstrap_por_dia(validos, alfa),
        "pnl": round(sum(pnls), 2) if pnls else None,
        "expectativa_usd": round(sum(pnls) / len(pnls), 2) if pnls else None,
    }


def estado_segmento(s: dict, n_minimo: int = N_MINIMO, sesiones_minimas: int = SESIONES_MINIMAS,
                    r_shr_max: float = R_SHR_MAXIMO_PARA_PROPONER) -> str:
    if not s or not s.get("n"):
        return ESTADO_SIN_MUESTRA
    negativo = (s.get("r_shr") is not None and s["r_shr"] <= r_shr_max)
    ic = s.get("r_ic")
    ic_negativo = ic is not None and ic[1] < 0
    if s["n"] < n_minimo or s["sesiones"] < sesiones_minimas:
        return ESTADO_OBSERVACION if (negativo or (s.get("r_medio") or 0) < 0) else ESTADO_SIN_EVIDENCIA
    if negativo and ic_negativo:
        return ESTADO_PROPUESTA
    return ESTADO_SIN_EVIDENCIA


def por_segmento(trades: list[dict], dimensiones=DIMENSIONES, solo_validos: bool = True) -> dict:
    """{'global': resumen, 'segmentos': [ {dimension, valor, ..., estado} ], 'alfa_corregido'}.
    `solo_validos` usa `cuenta_para_aprender`; un trade sin esa marca no cuenta."""
    base = [t for t in trades if (t.get("cuenta_para_aprender") is True or not solo_validos)]
    glob = resumen(base)
    mu = glob["r_medio"]
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in base:
        for d in dimensiones:
            v = valor_dimension(t, d)
            grupos[(d, "sin dato" if v is None else str(v))].append(t)
    m = max(1, len(grupos))
    alfa_c = ALFA / m
    segs = []
    for (d, v), rows in sorted(grupos.items()):
        s = resumen(rows, mu, alfa_c)
        s.update(dimension=d, valor=v)
        # Un segmento "sin dato" nunca se propone: no se sabe qué es.
        s["estado"] = ESTADO_OBSERVACION if (v == "sin dato" and s["n"]) else estado_segmento(s)
        if v == "sin dato" and s["estado"] == ESTADO_PROPUESTA:
            s["estado"] = ESTADO_OBSERVACION
        segs.append(s)
    return {"global": glob, "segmentos": segs, "alfa_corregido": alfa_c, "n_segmentos": m,
            "n_minimo": N_MINIMO, "sesiones_minimas": SESIONES_MINIMAS}
