"""Early Opportunity Engine (Prompt 2) -- responde una pregunta que
`scoring.py` NO responde: no "¿qué tan buena es esta señal?" sino
"¿llegamos a tiempo?". Son dos preguntas independientes a propósito: un
setup puede ser estructuralmente excelente (score alto en
`scoring.puntuar`) y aun así ser una mala entrada porque el movimiento ya
pasó (Prompt 2: "si la respuesta es que ya llegamos tarde, la alerta
debe bajar de prioridad AUNQUE el score general sea alto").

Por eso el veredicto (`temprano`/`tarde`) NO sale del promedio ponderado
de los seis factores de abajo -- sale de dos reglas duras, explicables
sin ambigüedad (extensión desde VWAP/EMA9, y velas desde que se activó
el patrón). El score compuesto (0-100) es información de apoyo para el
mensaje, no lo que decide el veredicto -- exactamente para que un
score alto no pueda "rescatar" una entrada tardía, como pide Prompt 2.

100% determinístico: cada factor es una fórmula fija sobre datos ya
calculados en `factors/intradia.py` -- ningún LLM interviene."""

from __future__ import annotations

from dataclasses import dataclass, field

from momentum_hunter.config import MomentumConfig
from momentum_hunter.models import FactoresIntradia

# Bandas de la puntuación de cada factor -- fijas y documentadas, igual
# que los umbrales de `screener/opportunity_hunter.py`.
ANTIGUEDAD_MIN_MINUTOS = 15     # catalizador de <=15 min: puntuación máxima
ANTIGUEDAD_MAX_MINUTOS = 180    # catalizador de >=3 horas: puntuación mínima
EXTENSION_SANA_PCT = 0.03       # <=3% de VWAP/EMA9: puntuación máxima
FRESCURA_MAX_VELAS = 2          # <=2 velas desde la ruptura: puntuación máxima
RR_MINIMO_SANO = 2.0            # riesgo/recompensa >=2.0: puntuación máxima
RR_MINIMO_ACEPTABLE = 0.5


@dataclass(frozen=True)
class EarlyOpportunity:
    score: float                    # 0-100, informativo -- no decide el veredicto
    veredicto: str                   # "temprano" | "tarde"
    motivo_veredicto: str
    sub: dict[str, float] = field(default_factory=dict)


def _score_antiguedad(minutos: float | None) -> float | None:
    if minutos is None:
        return None
    if minutos <= ANTIGUEDAD_MIN_MINUTOS:
        return 100.0
    if minutos >= ANTIGUEDAD_MAX_MINUTOS:
        return 0.0
    rango = ANTIGUEDAD_MAX_MINUTOS - ANTIGUEDAD_MIN_MINUTOS
    return 100.0 * (ANTIGUEDAD_MAX_MINUTOS - minutos) / rango


def _score_aceleracion(aceleracion: float | None) -> float | None:
    if aceleracion is None:
        return None
    # aceleracion=1.0 (neutral) -> 50; >=1.5 -> 100; <=0.5 -> 0.
    return max(0.0, min(100.0, (aceleracion - 1.0) * 100.0 + 50.0))


def extension_pct(factores: FactoresIntradia) -> float | None:
    """Qué tan lejos está el precio de sus anclas de corto plazo (VWAP,
    EMA9) -- la métrica central de "¿ya corrimos demasiado?". Toma la
    mayor de las dos distancias disponibles (la más conservadora)."""
    if factores.precio_actual is None:
        return None
    distancias = []
    if factores.vwap:
        distancias.append(abs(factores.precio_actual - factores.vwap) / factores.vwap)
    if factores.ema9:
        distancias.append(abs(factores.precio_actual - factores.ema9) / factores.ema9)
    return max(distancias) if distancias else None


def _score_extension(pct: float | None, techo: float) -> float | None:
    if pct is None:
        return None
    if pct <= EXTENSION_SANA_PCT:
        return 100.0
    if pct >= techo:
        return 0.0
    return 100.0 * (techo - pct) / (techo - EXTENSION_SANA_PCT)


def _score_distancia_maximo_dia(factores: FactoresIntradia) -> float | None:
    """Más cerca del máximo del día = más fuerza confirmando la tesis
    (o la ruptura misma está ocurriendo ahí) -- distinto de "extensión",
    que mide distancia a un promedio de corto plazo, no al extremo del
    día."""
    if factores.precio_actual is None or not factores.maximo_dia:
        return None
    proximidad = factores.precio_actual / factores.maximo_dia
    return max(0.0, min(100.0, (proximidad - 0.90) / 0.10 * 100.0))


def _score_frescura(velas: int | None, techo_velas: int) -> float | None:
    if velas is None:
        return None
    if velas <= FRESCURA_MAX_VELAS:
        return 100.0
    if velas >= techo_velas:
        return 0.0
    return 100.0 * (techo_velas - velas) / (techo_velas - FRESCURA_MAX_VELAS)


def _score_riesgo_recompensa(entrada: float, stop: float | None, objetivo: float | None) -> float | None:
    if stop is None or objetivo is None or entrada <= stop:
        return None
    riesgo = entrada - stop
    recompensa = objetivo - entrada
    if riesgo <= 0:
        return None
    ratio = recompensa / riesgo
    if ratio >= RR_MINIMO_SANO:
        return 100.0
    if ratio <= RR_MINIMO_ACEPTABLE:
        return 0.0
    return 100.0 * (ratio - RR_MINIMO_ACEPTABLE) / (RR_MINIMO_SANO - RR_MINIMO_ACEPTABLE)


def calcular(
    minutos_desde_catalizador: float | None, factores: FactoresIntradia,
    entrada: float, stop: float | None, objetivo: float | None, cfg: MomentumConfig,
) -> EarlyOpportunity:
    componentes: dict[str, tuple[float, float]] = {}

    s = _score_antiguedad(minutos_desde_catalizador)
    if s is not None:
        componentes["antiguedad_catalizador"] = (s, 0.20)
    s = _score_aceleracion(factores.aceleracion_volumen)
    if s is not None:
        componentes["aceleracion_volumen"] = (s, 0.20)
    ext = extension_pct(factores)
    s = _score_extension(ext, cfg.extension_maxima_pct)
    if s is not None:
        componentes["extension"] = (s, 0.20)
    s = _score_distancia_maximo_dia(factores)
    if s is not None:
        componentes["distancia_maximo_dia"] = (s, 0.10)
    s = _score_frescura(factores.velas_desde_ruptura, cfg.velas_maximas_desde_patron)
    if s is not None:
        componentes["frescura_patron"] = (s, 0.15)
    s = _score_riesgo_recompensa(entrada, stop, objetivo)
    if s is not None:
        componentes["riesgo_recompensa"] = (s, 0.15)

    peso_total = sum(p for _, p in componentes.values())
    score = round(sum(v * p for v, p in componentes.values()) / peso_total, 1) if peso_total > 0 else 0.0
    sub = {k: v for k, (v, _) in componentes.items()}

    # El veredicto NUNCA sale del score de arriba -- ver docstring del
    # módulo. Dos reglas duras, cada una suficiente por sí sola para
    # decir "tarde", sin importar qué tan bien se vean las demás.
    if ext is not None and ext > cfg.extension_maxima_pct:
        return EarlyOpportunity(
            score=score, veredicto="tarde",
            motivo_veredicto=f"El precio ya está {ext:.0%} lejos de VWAP/EMA9 -- "
                             f"perseguirlo ahora es mal riesgo/recompensa.",
            sub=sub,
        )
    if factores.velas_desde_ruptura is not None and factores.velas_desde_ruptura > cfg.velas_maximas_desde_patron:
        return EarlyOpportunity(
            score=score, veredicto="tarde",
            motivo_veredicto=f"El patrón se activó hace {factores.velas_desde_ruptura} velas -- "
                             "ya pasó la ventana en la que esta entrada tenía sentido.",
            sub=sub,
        )
    return EarlyOpportunity(
        score=score, veredicto="temprano",
        motivo_veredicto="El precio sigue cerca de sus anclas de corto plazo y el patrón "
                         "se activó hace pocas velas -- todavía hay margen real para entrar.",
        sub=sub,
    )
