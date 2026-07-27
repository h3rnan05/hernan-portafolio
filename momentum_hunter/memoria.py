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


def estrellas(ctx: ContextoHistorico) -> str | None:
    """Calidad del movimiento en estrellas (refinamiento "Head Trader",
    punto 5) -- SOLO desde el historial real del propio sistema, nunca de
    un score teórico. None sin muestra suficiente: mostrar tres estrellas
    "por defecto" sería inventar una calificación (mismo criterio que
    `frase_probabilidad`). Los cortes son fijos y documentados."""
    if not ctx.suficiente or ctx.win_rate is None:
        return None
    if ctx.win_rate >= 0.70:
        return "★★★★★"
    if ctx.win_rate >= 0.60:
        return "★★★★☆"
    if ctx.win_rate >= 0.50:
        return "★★★☆☆"
    if ctx.win_rate >= 0.40:
        return "★★☆☆☆"
    return "★☆☆☆☆"


def linea_calidad(ctx: ContextoHistorico) -> str:
    """La línea de calidad para el mensaje -- estrellas con su dato, o la
    admisión honesta de que todavía no hay calificación."""
    e = estrellas(ctx)
    if e is None:
        return (f"Calidad histórica: sin calificar todavía -- necesito al menos "
                f"{N_MINIMO_HISTORIAL} casos medidos y llevo {ctx.n}.")
    return f"Calidad histórica: {e} ({ctx.win_rate:.0%} de éxito en {ctx.n} casos)."


def confianza(ctx: ContextoHistorico, n_advertencias: int) -> tuple[str, str]:
    """Nivel de confianza CON sus razones (refinamiento "Head Trader",
    punto 7: "no quiero un porcentaje solamente, quiero saber por qué").
    Devuelve (nivel, texto completo listo para el mensaje).

    Reglas fijas: sin muestra suficiente la confianza es Baja y el texto
    lo dice sin rodeos (nunca se bloquea la alerta por esto -- Principio
    3 pide admitir, no castigar); con muestra, el nivel sale del win rate
    real, y dos o más dudas del abogado del diablo lo bajan un nivel
    (las dudas están listadas en el propio mensaje, así que la rebaja es
    rastreable a ellas)."""
    if not ctx.suficiente:
        if ctx.n == 0:
            texto = ("Confianza: Baja -- no tengo ni un caso medido de esta jugada todavía. "
                     "No invento confianza que no tengo.")
        else:
            texto = (f"Confianza: Baja -- solo tengo {ctx.n} ejemplo(s) histórico(s) de esta "
                     "jugada. No invento confianza que no tengo.")
        return "Baja", texto

    if ctx.win_rate >= 0.60:
        nivel = "Alta"
    elif ctx.win_rate >= 0.45:
        nivel = "Media"
    else:
        nivel = "Baja"

    rebajada = False
    if n_advertencias >= 2 and nivel != "Baja":
        nivel = {"Alta": "Media", "Media": "Baja"}[nivel]
        rebajada = True

    texto = (f"Confianza: {nivel} -- jugada vista {ctx.n} veces, funcionó "
             f"{ctx.win_rate:.0%} de las veces.")
    if rebajada:
        texto += " Le resté un nivel por las dudas listadas abajo."
    return nivel, texto


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
