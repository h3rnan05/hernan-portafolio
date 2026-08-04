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
radar.

Refinamiento pedido el 2026-07-27, después de ver el mensaje de "hoy no
hubo nada" funcionando: "que se sienta más útil y accionable... cuando
aparezca una acción en el radar, el bot debería decir qué está
esperando". Cada candidato en observación (no los "tarde" -- a esos ya
no hay nada que esperar, ver `_bloque_tarde`) ahora trae su propia lista
de "lo que estoy esperando", reutilizando textualmente
`evaluator.explicar_rechazo` (Principio 7: nunca "el score fue bajo",
siempre la condición concreta que falta)."""

from __future__ import annotations

from momentum_hunter import classification, evaluator
from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.config import MomentumConfig

TOPE_RADAR_TICKERS = 3   # cuántos tickers "en observación" mostrar con su propio bloque -- calidad antes que cantidad


def candidatos_para_radar(candidatos: list[CandidatoIntradia]) -> list[CandidatoIntradia]:
    """"Casi" -- catalizador confirmado y (dinero entrando o patrón
    detectado), pero no accionable. Nunca incluye a los que se
    detuvieron en el paso 1 (sin catalizador no hay nada que vigilar)."""
    return [
        c for c in candidatos
        if c.resultado.paso_detenido is None and not c.resultado.accionable
        and (c.resultado.dinero_entrando or c.resultado.patron is not None)
    ]


def _bloque_en_observacion(c: CandidatoIntradia, cfg: MomentumConfig) -> list[str]:
    """Un bloque por ticker -- qué está pasando ahora + qué le falta
    exactamente para volverse accionable. Reutiliza textualmente
    `evaluator.explicar_rechazo` (la misma explicación que ya usa
    `report.py` para un rechazo) en vez de inventar una segunda
    redacción de la misma lógica."""
    if c.resultado.patron is not None:
        descripcion = classification.DESCRIPCION_HUMANA.get(c.resultado.patron, "en movimiento")
        emoji, situacion = "🔥", f"Está {descripcion}."
    else:
        emoji, situacion = "👀", "Hay dinero entrando, pero todavía no veo una forma clara de entrada."
    condiciones = evaluator.explicar_rechazo(c.resultado, cfg)
    lineas = [f"{emoji} {c.ticker}", situacion, "", "Lo que estoy esperando:"]
    lineas += [f"• {condicion}" for condicion in condiciones]
    lineas += ["", "Si se confirma, te aviso automáticamente."]
    return lineas


def construir_resumen(
    candidatos: list[CandidatoIntradia],
    elegidas: frozenset[str] | set[str] = frozenset(),
    vetadas: dict[str, str] | None = None,
    cfg: MomentumConfig | None = None,
) -> str | None:
    """None si no hay nada que vigilar -- mismo principio que
    `alerts.py`: el silencio es un resultado válido, no se fuerza
    contenido.

    `elegidas` son los tickers que SÍ se alertaron en esta corrida;
    `vetadas` mapea ticker -> motivo (en lenguaje humano) de las
    candidatas que eran accionables pero el abogado del diablo mató
    (`skeptic.py`). Ninguna de las dos desaparece en silencio (Principio
    7): las accionables no elegidas se reportan como subcampeonas de la
    competencia relativa, y las vetadas con su motivo exacto.

    Refinamiento 2026-07-27: cada candidato genuinamente "en
    observación" (todavía a tiempo, con o sin patrón formado) trae su
    propio bloque con lo que le falta -- distinto de los "tarde", donde
    no hay nada que esperar porque ya corrieron sin nosotros."""
    vetadas = vetadas or {}
    cfg = cfg or MomentumConfig()
    radar = candidatos_para_radar(candidatos)
    subcampeonas = [
        c for c in candidatos
        if c.resultado.accionable and c.ticker not in elegidas and c.ticker not in vetadas
    ]
    if not radar and not subcampeonas and not vetadas:
        return None

    en_observacion = [c for c in radar if c.resultado.temprano][:TOPE_RADAR_TICKERS]
    tarde = [c for c in radar if not c.resultado.temprano]

    lineas = ["📡 Market Radar", ""]
    if elegidas:
        lineas.append("Además de la alerta que te acabo de mandar, esto es lo que sigo vigilando:")
    else:
        lineas.append("No encontré ninguna oportunidad con suficiente convicción para abrir una posición todavía.")

    for c in subcampeonas:
        lineas.append("")
        lineas.append(
            f"🥈 {c.ticker}: también calificó hoy, pero no fue la mejor -- es la siguiente "
            "en la lista si la primera se invalida."
        )
    for ticker, motivo in vetadas.items():
        lineas.append("")
        lineas.append(f"⛔ {ticker}: calificaba, pero la descarté. {motivo}")

    for c in en_observacion:
        lineas.append("")
        lineas.extend(_bloque_en_observacion(c, cfg))

    for c in tarde:
        lineas.append("")
        lineas.append(f"⚠️ {c.ticker}: ya se movió demasiado -- en observación, no para entrar.")

    return "\n".join(lineas)
