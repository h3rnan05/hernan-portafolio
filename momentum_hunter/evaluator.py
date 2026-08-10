"""El árbol de decisión de Prompt 4 -- "quiero un trader tomando
decisiones, no un screener". Reemplaza el promedio ponderado como lo que
DECIDE si se alerta (`scoring.puntuar` sigue existiendo, pero ahora
produce el `score_base` que este árbol ajusta, no el veredicto final).

Las cinco preguntas, en orden, exactamente como las describe Prompt 4:

1. ¿Catalizador real? -- si no, **corta el análisis aquí** (única
   pregunta que es un corte duro real, tal como pide el prompt).
2. ¿Está entrando dinero? (RVOL intradía) -- si no, penaliza el score.
3. ¿Desequilibrio oferta/demanda? (float bajo / interés en corto) -- si
   no, penaliza el score. Esto reemplaza a la antigua categoría
   "short_squeeze": aquí es un GATE que aplica a cualquier patrón, no
   una etiqueta aparte (ver `classification.py`).
4. ¿Patrón claro? (`classification.detectar_patron`) -- si no hay
   ninguno de los seis patrones, la penalización es tan grande que en la
   práctica anula la alerta: no se puede decir "dónde entro" sin un
   patrón que defina la entrada.
5. ¿Todavía vale la pena entrar? (`early_opportunity.calcular`) -- si el
   veredicto es "tarde", la penalización también anula la alerta
   (Prompt 2: un score alto no debe rescatar una entrada tardía).

Las preguntas 4 y 5 se expresan como "penalización" (no como corte duro
como la 1) porque así lo pide el prompt literalmente ("si alguna
respuesta es negativa, bajar significativamente la puntuación") -- pero
sus penalizaciones son deliberadamente grandes (ver
`PENALIZACION_SIN_PATRON`/`PENALIZACION_TARDE`) porque, en la práctica,
no existe una alerta accionable sin patrón o ya tarde: no hay dónde
poner una entrada. `accionable` lo deja explícito en vez de depender
silenciosamente de que la aritmética de penalización nunca cambie.

`es_large_cap` (2026-08-07, modo large-cap -- ver `config.py`): la
pregunta 3 deja de aplicar tal cual para una empresa grande. El float
bajo/interés en corto alto es el mecanismo que explica por qué una
small-cap puede explotar con relativamente poco volumen; una mega-cap no
tiene ese mecanismo estructural, así que preguntarlo ahí sería exigir
algo que por diseño casi nunca puede ser cierto -- una penalización
disfrazada de pregunta. Para `es_large_cap=True`, la pregunta 3 se omite
por completo (ni penalización ni mención en `explicar_rechazo`): el
catalizador confirmado + un patrón real ya en marcha (que para large-cap
en la práctica significa `gap_and_go`/`opening_range_breakout` --
requieren un gap real, ver `classification.py`) hacen ese trabajo."""

from __future__ import annotations

from dataclasses import dataclass, field

from momentum_hunter import classification, early_opportunity
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.early_opportunity import EarlyOpportunity
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata

UMBRAL_FLOAT_DESEQUILIBRIO = 20_000_000    # acciones -- mismo umbral que ya usaba la vieja
                                            # categoría "short_squeeze"
UMBRAL_SHORT_DESEQUILIBRIO = 0.15          # 15% del float en corto

PENALIZACION_SIN_DINERO = 20.0
PENALIZACION_SIN_DESEQUILIBRIO = 15.0
# Grandes a propósito -- ver docstring del módulo sobre por qué preguntas
# 4 y 5 son, en la práctica, cortes duros aunque se expresen como
# "penalización" para seguir la letra de Prompt 4.
PENALIZACION_SIN_PATRON = 100.0
PENALIZACION_TARDE = 100.0


@dataclass(frozen=True)
class ResultadoEvaluacion:
    paso_detenido: str | None          # "catalizador" si el análisis terminó ahí, si no None
    dinero_entrando: bool
    desequilibrio: bool                 # siempre False si es_large_cap -- ver docstring del módulo
    patron: str | None                  # clave de classification.py, o None
    temprano: bool
    early: EarlyOpportunity | None
    penalizaciones: list[str] = field(default_factory=list)
    score_base: float = 0.0
    score_ajustado: float = 0.0
    accionable: bool = False
    es_large_cap: bool = False


def _hay_desequilibrio(meta: Metadata) -> bool:
    float_bajo = meta.shares_float is not None and meta.shares_float <= UMBRAL_FLOAT_DESEQUILIBRIO
    short_alto = meta.short_pct_float is not None and meta.short_pct_float >= UMBRAL_SHORT_DESEQUILIBRIO
    return float_bajo or short_alto


def evaluar(
    catalizador: Catalizador | None, minutos_desde_catalizador: float | None,
    factores: FactoresIntradia, bi_hoy: BarraIntradia, meta: Metadata,
    entrada: float, stop: float | None, objetivo: float | None,
    score_base: float, cfg: MomentumConfig, es_large_cap: bool = False,
) -> ResultadoEvaluacion:
    # Pregunta 1 -- corte duro real.
    if catalizador is None or not catalizador.confirmado:
        return ResultadoEvaluacion(
            paso_detenido="catalizador", dinero_entrando=False, desequilibrio=False,
            patron=None, temprano=False, early=None,
            penalizaciones=["Sin catalizador confirmado -- análisis terminado aquí."],
            score_base=score_base, score_ajustado=0.0, accionable=False,
            es_large_cap=es_large_cap,
        )

    # Preguntas 2-5.
    dinero_entrando = (
        factores.rvol_actual is not None and factores.rvol_actual >= cfg.umbral_rvol_intradia
    )
    # Pregunta 3: N/A para large-cap -- ver docstring del módulo.
    desequilibrio = False if es_large_cap else _hay_desequilibrio(meta)
    patron = classification.detectar_patron(bi_hoy, factores)
    early = early_opportunity.calcular(minutos_desde_catalizador, factores, entrada, stop, objetivo, cfg)
    temprano = early.veredicto == "temprano"

    penalizaciones: list[str] = []
    descuento = 0.0
    if not dinero_entrando:
        descuento += PENALIZACION_SIN_DINERO
        penalizaciones.append("El volumen no muestra que esté entrando dinero ahora mismo.")
    if not desequilibrio and not es_large_cap:
        descuento += PENALIZACION_SIN_DESEQUILIBRIO
        penalizaciones.append("No hay un desequilibrio claro de oferta/demanda (float bajo o interés en corto alto).")
    if patron is None:
        descuento += PENALIZACION_SIN_PATRON
        penalizaciones.append("No hay un patrón técnico claro formándose todavía.")
    if not temprano:
        descuento += PENALIZACION_TARDE
        penalizaciones.append(early.motivo_veredicto)

    score_ajustado = round(max(0.0, score_base - descuento), 1)
    accionable = patron is not None and temprano and score_ajustado >= cfg.score_minimo_alerta

    return ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=dinero_entrando, desequilibrio=desequilibrio,
        patron=patron, temprano=temprano, early=early, penalizaciones=penalizaciones,
        score_base=score_base, score_ajustado=score_ajustado, accionable=accionable,
        es_large_cap=es_large_cap,
    )


def explicar_rechazo(r: ResultadoEvaluacion, cfg: MomentumConfig) -> list[str]:
    """Principio 7 (pedido 2026-07-27): "cuando descarte algo debe
    explicar exactamente qué lo descartó... qué tendría que cambiar para
    que la oportunidad volviera a ser válida". Una línea por condición
    que falló, en términos de lo que tendría que ser distinto -- nunca
    'el score fue bajo'. Lista vacía si el resultado fue accionable (no
    hubo rechazo que explicar)."""
    if r.accionable:
        return []
    if r.paso_detenido == "catalizador":
        return ["Que exista un catalizador verificable en una fuente real -- sin él, "
                "ni siquiera se evalúa el resto."]
    cambios: list[str] = []
    if not r.dinero_entrando:
        cambios.append(f"Que el volumen del momento supere {cfg.umbral_rvol_intradia:.0f} veces "
                       "el de los minutos anteriores -- hoy no está entrando dinero con fuerza.")
    if not r.desequilibrio and not r.es_large_cap:
        cambios.append("Que exista un desequilibrio real de oferta/demanda (pocas acciones "
                       "disponibles, o muchos vendedores en corto atrapados).")
    if r.patron is None:
        cambios.append("Que el precio forme una de las seis figuras que sé operar -- sin una "
                       "forma clara no hay entrada ni salida definibles.")
    if not r.temprano and r.early is not None:
        if r.early.razon == "extension":
            cambios.append("Que el precio regrese cerca de sus niveles de referencia -- ahora "
                           "está demasiado estirado para entrar sin perseguirlo.")
        else:
            cambios.append("Que aparezca una configuración fresca -- esta ya corrió sin nosotros "
                           "y perseguirla es mal negocio.")
    if not cambios:
        # Accionable=False sin ninguna condición individual fallida solo
        # puede ser el umbral de score: las penalizaciones acumuladas
        # dejaron el total por debajo del mínimo.
        cambios.append(f"Que el conjunto sume más convicción: el total quedó en "
                       f"{r.score_ajustado:.0f} y exijo más de {cfg.score_minimo_alerta:.0f}.")
    return cambios
