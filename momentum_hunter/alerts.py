"""Filtro de envío -- Prompt 7: "Solo enviar Telegram cuando: Score > 85
Y Catalizador confirmado Y Volumen mayor a 4 veces el promedio Y Liquidez
suficiente para entrar. No enviar más de cinco alertas por día. Calidad
antes que cantidad."

Deliberadamente NO manda un mensaje de "no encontré nada" cuando ningún
candidato califica -- eso sí lo hace `screener/opportunity_hunter.py`
(`SIN_OPORTUNIDADES`), pero ese bot corre UNA VEZ al día y ese mensaje
es la confirmación de que sí corrió. Este bot puede correr varias veces
al día (Prompt 5-7 describen un scanner, no una corrida diaria única);
mandar "nada hoy" cada vez que no hay una señal de score>85 sería
exactamente el ruido que Prompt 2 pide evitar ("prefiero perder
oportunidades que recibir demasiadas falsas alarmas"). El silencio es el
resultado esperado y normal, no un fallo que haya que reportar."""

from __future__ import annotations

from dataclasses import dataclass

from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion


@dataclass(frozen=True)
class Candidato:
    """Todo lo que ya se calculó para un ticker en una corrida -- el
    objeto que viaja entre `run.py`, `alerts.py`, `classification.py`,
    `strategy.py` y `report.py` sin que ninguno recalcule nada."""
    ticker: str
    nombre: str | None
    precio: float
    volumen_promedio: float | None
    factores: FactoresMomentum
    catalizador: Catalizador | None
    meta: Metadata
    puntuacion: Puntuacion


def califica_para_alerta(c: Candidato, cfg: MomentumConfig) -> bool:
    """Las CUATRO condiciones del Prompt 7, todas obligatorias -- ninguna
    combinación parcial dispara una alerta."""
    if c.puntuacion.score_total <= cfg.score_minimo_alerta:
        return False
    if cfg.requiere_catalizador_confirmado and (c.catalizador is None or not c.catalizador.confirmado):
        return False
    if c.factores.rvol is None or c.factores.rvol < cfg.rvol_minimo_alerta:
        return False
    if c.volumen_promedio is None or c.volumen_promedio < cfg.volumen_promedio_min:
        return False
    return True


def filtrar_alertas(candidatos: list[Candidato], cfg: MomentumConfig) -> list[Candidato]:
    """Todos los que califican, ordenados por score y recortados al tope
    diario -- "2-3 excelentes, no 20 mediocres" es el mismo espíritu que
    ya adoptó `screener/opportunity_hunter.py` para el bot hermano."""
    calificados = [c for c in candidatos if califica_para_alerta(c, cfg)]
    calificados.sort(key=lambda c: c.puntuacion.score_total, reverse=True)
    return calificados[: cfg.limite_diario_alertas]
