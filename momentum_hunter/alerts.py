"""Filtro de envío -- ahora en dos etapas, siguiendo el pivote de
"screener" a "trader" (Prompt 1):

**Etapa 1 (diaria/gruesa)**: `CandidatoDiario` es el mismo filtro de
siempre sobre barras diarias (precio, cap, RVOL de 20 días, catalizador,
`scoring.puntuar`) -- pero ya NO decide si se alerta. Solo decide qué
tickers son lo bastante interesantes como para gastar una llamada de
datos INTRADÍA en ellos (`run.py` los recorta a
`cfg.max_candidatos_intradia`).

**Etapa 2 (intradía/fina)**: `CandidatoIntradia` es el resultado de
`evaluator.evaluar()` -- el árbol de las 5 preguntas de Prompt 4. Una
alerta se manda SOLO si `resultado.accionable` es `True`: catalizador
confirmado, patrón claro detectado, y el Early Opportunity Engine dice
"temprano" (Prompt 2: un score alto nunca rescata una entrada tardía o
sin patrón).

Se mantiene el silencio cuando nada calFica -- este bot puede correr
varias veces al día, y repetir "nada hoy" sería el mismo ruido que
Prompt 1/2 piden evitar. El "casi" (catalizador + volumen pero sin
patrón o ya tarde) no se descarta en silencio: alimenta el Market Radar
(`radar.py`), no una alerta de entrada."""

from __future__ import annotations

from dataclasses import dataclass

from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion


@dataclass(frozen=True)
class CandidatoDiario:
    """Salida de la etapa 1 -- sobre barras diarias, mismo motor de
    siempre (`scoring.puntuar`). Ya no decide si se alerta: solo si
    vale la pena pedir datos intradía para este ticker."""
    ticker: str
    nombre: str | None
    precio: float
    volumen_promedio: float | None
    factores: FactoresMomentum
    catalizador: Catalizador | None
    meta: Metadata
    puntuacion: Puntuacion


@dataclass(frozen=True)
class CandidatoIntradia:
    """Salida de la etapa 2 -- todo lo que `evaluator.evaluar()` y
    `report.py` necesitan, ya calculado una sola vez."""
    ticker: str
    nombre: str | None
    catalizador: Catalizador | None
    minutos_desde_catalizador: float | None
    factores: FactoresIntradia
    bi_hoy: BarraIntradia
    meta: Metadata
    atr_diario: float | None          # de la etapa 1 -- fallback si no hay VWAP/EMA9 para el stop
    resultado: ResultadoEvaluacion


def candidatos_para_etapa_intradia(
    candidatos: list[CandidatoDiario], cfg: MomentumConfig,
) -> list[CandidatoDiario]:
    """Recorta a los `cfg.max_candidatos_intradia` mejores -- pedir velas
    intradía para todo el universo diario no es viable (ver
    `data/provider.py`). Exige catalizador confirmado (pregunta 1 de
    Prompt 4 de todas formas lo va a cortar si no lo hay, así que no
    tiene sentido gastar la llamada intradía en tickers que ya sabemos
    que van a terminar ahí)."""
    con_catalizador = [
        c for c in candidatos if c.catalizador is not None and c.catalizador.confirmado
    ]
    con_catalizador.sort(key=lambda c: c.puntuacion.score_total, reverse=True)
    return con_catalizador[: cfg.max_candidatos_intradia]


def accionables_ordenados(candidatos: list[CandidatoIntradia]) -> list[CandidatoIntradia]:
    """Todos los `accionable` (ya decidido por `evaluator.evaluar`),
    ordenados por score ajustado -- SIN recortar. `run.py` los recorre
    en este orden para la competencia relativa (Principio 4) + el debate
    del abogado del diablo (`skeptic.py`): si la mejor candidata muere en
    el debate, la siguiente hereda el turno de competir."""
    accionables = [c for c in candidatos if c.resultado.accionable]
    accionables.sort(key=lambda c: c.resultado.score_ajustado, reverse=True)
    return accionables


def cuota_alertas(cfg: MomentumConfig) -> int:
    """Cuántas alertas puede producir UNA corrida. Con `solo_la_mejor`
    (default): exactamente una -- "si hoy solamente pudiera abrir UNA
    operación, ¿sería esta?" solo tiene una respuesta afirmativa posible
    por corrida. Apagado, aplica el techo de seguridad de siempre."""
    return 1 if cfg.solo_la_mejor else cfg.limite_diario_alertas


def filtrar_alertas(candidatos: list[CandidatoIntradia], cfg: MomentumConfig) -> list[CandidatoIntradia]:
    """Accionables ordenados y recortados a la cuota -- la selectividad
    real ya la hizo el árbol de decisión; esto aplica la competencia
    relativa (y el techo de emergencia cuando `solo_la_mejor` está
    apagado). El abogado del diablo corre DESPUÉS de esto, en `run.py`,
    sobre la lista completa de `accionables_ordenados` -- esta función
    existe para quien quiera el recorte simple sin el debate."""
    return accionables_ordenados(candidatos)[: cuota_alertas(cfg)]
