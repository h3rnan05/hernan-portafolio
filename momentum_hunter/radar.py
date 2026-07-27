"""Market Radar (idea bonus del dueño del producto, 2026-07-26): "no son
señales de compra, son una forma de decirte dónde deberías estar
prestando atención ahora mismo".

Toma los candidatos de la etapa 2 (`CandidatoIntradia`) que pasaron el
catalizador (paso 1 del evaluador) pero NO llegaron a ser accionables --
o porque todavía no hay un patrón claro, o porque el patrón ya se ve
"tarde" (Early Opportunity Engine). Antes, esos candidatos simplemente
desaparecían en silencio; ahora alimentan un resumen corto y agrupado,
en vez de una alerta por ticker (que sería exactamente el ruido que
Prompt 1/2 piden evitar).

Se corre por separado de `alerts.filtrar_alertas` -- un candidato NUNCA
aparece en ambos: si es accionable, va a una alerta de entrada, no al
radar."""

from __future__ import annotations

from momentum_hunter import classification
from momentum_hunter.alerts import CandidatoIntradia

TOPE_RADAR = 8   # techo de líneas del resumen -- sigue siendo "calidad antes que cantidad"


def candidatos_para_radar(candidatos: list[CandidatoIntradia]) -> list[CandidatoIntradia]:
    """"Casi" -- catalizador confirmado y (dinero entrando o patrón
    detectado), pero no accionable. Nunca incluye a los que se
    detuvieron en el paso 1 (sin catalizador no hay nada que vigilar)."""
    return [
        c for c in candidatos
        if c.resultado.paso_detenido is None and not c.resultado.accionable
        and (c.resultado.dinero_entrando or c.resultado.patron is not None)
    ]


def construir_resumen(
    candidatos: list[CandidatoIntradia],
    elegidas: frozenset[str] | set[str] = frozenset(),
    vetadas: dict[str, str] | None = None,
) -> str | None:
    """None si no hay nada que vigilar -- mismo principio que
    `alerts.py`: el silencio es un resultado válido, no se fuerza
    contenido.

    `elegidas` son los tickers que SÍ se alertaron en esta corrida;
    `vetadas` mapea ticker -> motivo (en lenguaje humano) de las
    candidatas que eran accionables pero el abogado del diablo mató
    (`skeptic.py`). Ninguna de las dos desaparece en silencio (Principio
    7): las accionables no elegidas se reportan como subcampeonas de la
    competencia relativa, y las vetadas con su motivo exacto."""
    vetadas = vetadas or {}
    radar = candidatos_para_radar(candidatos)
    subcampeonas = [
        c for c in candidatos
        if c.resultado.accionable and c.ticker not in elegidas and c.ticker not in vetadas
    ]
    if not radar and not subcampeonas and not vetadas:
        return None

    formando = [c for c in radar if c.resultado.patron is not None and c.resultado.temprano]
    tarde = [c for c in radar if c.resultado.patron is not None and not c.resultado.temprano]
    sin_patron = [c for c in radar if c.resultado.patron is None]

    por_patron: dict[str, list[str]] = {}
    for c in formando:
        por_patron.setdefault(c.resultado.patron, []).append(c.ticker)

    lineas = ["📡 Market Radar", ""]

    for c in subcampeonas:
        lineas.append(
            f"🥈 {c.ticker}: también calificó hoy, pero no fue la mejor -- es la siguiente "
            "en la lista si la primera se invalida."
        )
    for ticker, motivo in vetadas.items():
        lineas.append(f"⛔ {ticker}: calificaba, pero la descarté. {motivo}")
    for patron, tickers in por_patron.items():
        descripcion = classification.DESCRIPCION_HUMANA.get(patron, "en movimiento")
        sustantivo = "acción" if len(tickers) == 1 else "acciones"
        verbo = "está" if len(tickers) == 1 else "están"
        lineas.append(f"🔥 {len(tickers)} {sustantivo} {verbo} {descripcion}: {', '.join(tickers)}.")

    if sin_patron:
        sustantivo = "acción" if len(sin_patron) == 1 else "acciones"
        tickers = ", ".join(c.ticker for c in sin_patron)
        lineas.append(f"👀 {len(sin_patron)} {sustantivo} con dinero entrando, todavía sin nada claro que operar: {tickers}.")

    for c in tarde[: max(0, TOPE_RADAR - len(lineas))]:
        lineas.append(f"⚠️ {c.ticker}: ya se movió demasiado -- en observación, no para entrar.")

    return "\n".join(lineas)
