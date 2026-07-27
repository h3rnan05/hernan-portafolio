"""Memoria contextual -- Principios 3 y 12 del pedido de 2026-07-27:
"el sistema debe pensar en probabilidades, nunca en certezas... si
todavía no existe suficiente historial, debe decirlo. No debe inventar
confianza." Y: "debe recordar cosas como 'las últimas 12 operaciones
con este patrón fueron malas'... no para prohibir operaciones, sino
para ajustar la confianza."

Lee el mismo historial real que ya persiste `tracker.py` (cada alerta
enviada, con su resultado medido por `outcomes.py`) y lo convierte en
dos cosas:

1. **Una frase de probabilidad honesta** para el mensaje: "jugadas como
   esta me han funcionado X% de las veces (N casos)" cuando hay muestra
   suficiente, o la admisión explícita de que no la hay -- nunca un
   número inventado sobre 3 casos.
2. **Advertencias contextuales** cuando el historial medido de este
   patrón o este tipo de catalizador es débil -- que viajan al debate
   del abogado del diablo (`skeptic.py`) como advertencias, NUNCA como
   objeciones fatales: la memoria ajusta la confianza, no prohíbe
   (Principio 12 literal).

Todo es lectura y agregación sobre resultados reales ya medidos --
ninguna función de aquí modifica `scoring.py`, `config.py` ni ningún
umbral (Principio 8: "jamás autooptimización silenciosa"; el ciclo es
medir -> demostrar -> proponer -> el humano decide)."""

from __future__ import annotations

from dataclasses import dataclass

from momentum_hunter.stats import calcular_estadisticas
from momentum_hunter.tracker import AlertaRegistrada

# Umbrales fijos y documentados. N_MINIMO_HISTORIAL es la muestra mínima
# para atreverse a citar un porcentaje -- por debajo de eso, la única
# respuesta honesta es "no lo sé todavía".
N_MINIMO_HISTORIAL = 10
HORIZONTE_REFERENCIA_DIAS = 3       # punto medio honesto del horizonte de 1-10 días del bot
UMBRAL_HISTORIAL_DEBIL = 0.40       # win rate medido < 40% con muestra suficiente = confianza a la baja


@dataclass(frozen=True)
class ContextoHistorico:
    dimension: str                   # "patron" | "catalizador"
    clave: str
    n: int                            # alertas de este tipo YA RESUELTAS al horizonte de referencia
    win_rate: float | None
    retorno_promedio: float | None

    @property
    def suficiente(self) -> bool:
        return self.n >= N_MINIMO_HISTORIAL


def _contexto(dimension: str, clave: str, grupo: list[AlertaRegistrada]) -> ContextoHistorico:
    e = calcular_estadisticas(grupo, HORIZONTE_REFERENCIA_DIAS)
    return ContextoHistorico(
        dimension=dimension, clave=clave, n=e.n,
        win_rate=e.win_rate, retorno_promedio=e.retorno_promedio,
    )


def contexto_patron(alertas: list[AlertaRegistrada], patron: str | None) -> ContextoHistorico:
    clave = patron or ""
    grupo = [a for a in alertas if a.clasificacion == clave] if clave else []
    return _contexto("patron", clave, grupo)


def contexto_catalizador(alertas: list[AlertaRegistrada], tipo: str | None) -> ContextoHistorico:
    clave = tipo or ""
    grupo = [a for a in alertas if a.catalizador_tipo == clave] if clave else []
    return _contexto("catalizador", clave, grupo)


def frase_probabilidad(ctx: ContextoHistorico) -> str:
    """La frase que va al mensaje -- Principio 3 literal: probabilidad
    real con muestra suficiente, o la admisión de que no existe. Nunca
    'esta acción va a subir'."""
    if ctx.n == 0:
        return ("Todavía no tengo resultados medidos de jugadas como esta, así que no puedo "
                "darte una probabilidad honesta. Pasó todos mis filtros, pero trátala como "
                "algo no probado.")
    if not ctx.suficiente:
        plural = "jugada como esta medida" if ctx.n == 1 else "jugadas como esta medidas"
        return (f"Solo llevo {ctx.n} {plural} -- muy pocas para citar un porcentaje. "
                "No voy a inventar confianza que no tengo.")
    return (f"Jugadas como esta me han funcionado {ctx.win_rate:.0%} de las veces "
            f"({ctx.n} casos medidos). El pasado no garantiza nada, pero es mi mejor dato real.")


def advertencias_contextuales(contextos: list[ContextoHistorico]) -> list[str]:
    """Advertencias para el debate del abogado del diablo -- SOLO cuando
    hay muestra suficiente Y el resultado medido es débil. Con muestra
    chica no se advierte nada: una mala racha de 3 casos no es evidencia,
    es ruido (el mismo criterio que exige `frase_probabilidad`)."""
    avisos: list[str] = []
    nombres = {"patron": "este mismo tipo de jugada", "catalizador": "este mismo tipo de noticia"}
    for ctx in contextos:
        if ctx.suficiente and ctx.win_rate is not None and ctx.win_rate < UMBRAL_HISTORIAL_DEBIL:
            avisos.append(
                f"Mis últimas {ctx.n} alertas con {nombres.get(ctx.dimension, 'esta configuración')} "
                f"solo funcionaron {ctx.win_rate:.0%} de las veces -- eso baja mi confianza en esta."
            )
    return avisos
