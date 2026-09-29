"""Variantes del backtest v2: UN cambio cada una sobre la config base.

Son experimentos del backtest, no cambios de estrategia: la config del
YAML sigue siendo la base y el hunter no ve nada de esto. Cada variante
es una función `ConfigV2 -> ConfigV2` (dataclasses congeladas: se usa
`replace`). `v1` usa `stop_origen = "minimo_vela_ruptura"`, un valor que
el YAML no acepta a propósito (`config.py` solo admite
`minimo_rango_apertura`): si alguna vez se aprueba, entra por un PR de
config con su validación, no por aquí.

Pedidas el 2026-09-29:
  base  la config del YAML (stop 4 %, spread 0,3 %, ORB 09:30–09:35,
        ventana 09:36–11:00, stop de tiempo 30 min).
  v1    stop = mínimo de la vela de ruptura (1 min), acotado 1,5–4 %.
  v2    spread máximo 0,6 %.
  v3    rango de apertura de 15 min (09:30–09:45), ventana 09:46–11:00.
  v4    stop de tiempo 60 min.

Pedidas el 2026-09-29 (entradas, ver `entradas.py`):
  e1    entrada por retroceso al máximo del ORB / VWAP.
  e2    orden límite en el máximo del ORB, válida 15 min.
  e3    ruptura del máximo del premercado 09:31–09:45.
  u1    universo solo $2–20.
  obj15 objetivo 1,5 R (base 2 R).       st45  stop de tiempo 45 min.
  diagnostico  corre la base y escribe el diagnóstico de entrada, sin trades.
Un nombre compuesto (`e1_u1`) aplica cada parte en orden: sigue siendo
un experimento con cambios contados, no un ajuste libre.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from typing import Callable

from estrategia_v2.config import ConfigV2

STOP_VELA_RUPTURA = "minimo_vela_ruptura"


def _base(cfg: ConfigV2) -> ConfigV2:
    return cfg


def _v1(cfg: ConfigV2) -> ConfigV2:
    return replace(cfg, riesgo=replace(cfg.riesgo, stop_origen=STOP_VELA_RUPTURA))


def _v2(cfg: ConfigV2) -> ConfigV2:
    return replace(cfg, senal=replace(cfg.senal, spread_max_pct=0.006))


def _v3(cfg: ConfigV2) -> ConfigV2:
    return replace(cfg, senal=replace(cfg.senal, rango_apertura_fin=time(9, 45), ventana_inicio=time(9, 46)))


def _v4(cfg: ConfigV2) -> ConfigV2:
    return replace(cfg, riesgo=replace(cfg.riesgo, stop_tiempo_minutos=60.0))


def _u1(cfg: ConfigV2) -> ConfigV2:
    return replace(cfg, universo=replace(cfg.universo, precio_max=20.0))


def _obj15(cfg: ConfigV2) -> ConfigV2:
    return replace(cfg, riesgo=replace(cfg.riesgo, objetivo_r=1.5))


def _st45(cfg: ConfigV2) -> ConfigV2:
    return replace(cfg, riesgo=replace(cfg.riesgo, stop_tiempo_minutos=45.0))


VARIANTES: dict[str, tuple[str, Callable[[ConfigV2], ConfigV2]]] = {
    "base": ("config del YAML sin cambios", _base),
    "v1": ("stop = mínimo de la vela de ruptura (1 min), acotado 1,5–4 %", _v1),
    "v2": ("spread máximo 0,6 % (base 0,3 %)", _v2),
    "v3": ("rango de apertura 09:30–09:45 y ventana 09:46–11:00", _v3),
    "v4": ("stop de tiempo 60 min (base 30)", _v4),
    "e1": ("entrada por retroceso al máx. del ORB / VWAP, stop = mínimo del retroceso, espera 20 min", _base),
    "e2": ("orden límite en el máximo del ORB tras la ruptura, válida 15 min", _base),
    "e3": ("ruptura del máximo del premercado 09:31–09:45 con volumen ≥ 1,5×, stop = mínimo de los 3 primeros min", _base),
    "u1": ("universo solo $2–20", _u1),
    "obj15": ("objetivo 1,5 R (base 2 R)", _obj15),
    "st45": ("stop de tiempo 45 min (base 30)", _st45),
    "diagnostico": ("base sin trades: diagnóstico de entrada (60 min tras la ruptura)", _base),
    # Planes B (fase 5): otra estrategia, solo lectura. Ver planb.py.
    "pead": ("plan B: deriva post-resultados (8-K 2.02, gap ≥ 4 %, RVOL ≥ 3, $2–20; cierre día 0 → cierre día 3 o stop 6 %)", _base),
    "seguimiento": ("plan B: nivel 1 al día siguiente (ruptura del máximo del día 1 en el día 2, stop 4 %, cierre EOD)", _base),
}
ENTRADAS = {"e1": "e1", "e2": "e2", "e3": "e3"}


def partes(nombre: str) -> list[str]:
    return [x for x in nombre.split("_") if x]


def aplicar(cfg: ConfigV2, nombre: str) -> ConfigV2:
    """Config de la variante (compuesta o no). La regla de entrada no vive
    en la config: `entrada_de` la da aparte."""
    for parte in partes(nombre):
        if parte not in VARIANTES:
            raise KeyError(f"variante desconocida: {parte} (hay {', '.join(VARIANTES)})")
        cfg = VARIANTES[parte][1](cfg)
    return cfg


def entrada_de(nombre: str) -> str:
    entradas = [ENTRADAS[p] for p in partes(nombre) if p in ENTRADAS]
    if len(entradas) > 1:
        raise KeyError(f"{nombre}: dos reglas de entrada a la vez")
    return entradas[0] if entradas else "orb"


def descripcion(nombre: str) -> str:
    return "; ".join(VARIANTES[p][0] for p in partes(nombre))
