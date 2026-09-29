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


VARIANTES: dict[str, tuple[str, Callable[[ConfigV2], ConfigV2]]] = {
    "base": ("config del YAML sin cambios", _base),
    "v1": ("stop = mínimo de la vela de ruptura (1 min), acotado 1,5–4 %", _v1),
    "v2": ("spread máximo 0,6 % (base 0,3 %)", _v2),
    "v3": ("rango de apertura 09:30–09:45 y ventana 09:46–11:00", _v3),
    "v4": ("stop de tiempo 60 min (base 30)", _v4),
}


def aplicar(cfg: ConfigV2, nombre: str) -> ConfigV2:
    if nombre not in VARIANTES:
        raise KeyError(f"variante desconocida: {nombre} (hay {', '.join(VARIANTES)})")
    return VARIANTES[nombre][1](cfg)


def descripcion(nombre: str) -> str:
    return VARIANTES[nombre][0]
