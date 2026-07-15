"""Formatea las noticias relevantes para Telegram. 'DO NOTHING' (sin
noticias relevantes hoy) es una salida válida y se comunica explícitamente
-- no se manda ruido, y tampoco se manda silencio sin explicación."""

from __future__ import annotations

from datetime import UTC, datetime

from news_analyst.models import NoticiaRelevante

_TONO_EMOJI = {"positivo": "🟢", "negativo": "🔴", "neutral": "⚪", "incierto": "🟡"}

DISCLAIMER = (
    "Esto NO es una recomendación de compra/venta. Solo te dice qué "
    "noticias tocan tu shortlist y por qué."
)


def _estrellas(nivel: int) -> str:
    return "⭐" * nivel + "☆" * (5 - nivel)


def texto_telegram(relevantes: list[NoticiaRelevante], total_titulares: int) -> str:
    hoy = datetime.now(UTC).strftime("%Y-%m-%d")
    if not relevantes:
        return (
            f"🔍 Por qué debería importarte -- {hoy}\n\n"
            f"Revisé {total_titulares} titulares nuevos: ninguno menciona a "
            f"una empresa de tu shortlist de hoy. Sin ruido que mostrarte.\n\n"
            f"{DISCLAIMER}"
        )

    lineas = [f"🔍 Por qué debería importarte -- {hoy}", ""]
    sin_explicar = 0
    for r in relevantes:
        e = r.mencion.entrada
        lineas.append(f'📰 "{r.titular}"')
        lineas.append(f"🤖 {e.nombre or e.ticker} ({e.ticker}) está en la "
                       f"posición #{e.posicion} de tu shortlist de hoy.")
        if r.explicacion:
            emoji = _TONO_EMOJI.get(r.explicacion.tono, "⚪")
            lineas.append(f"   {r.explicacion.texto}")
            lineas.append(f"   Tono: {emoji} {r.explicacion.tono} | "
                           f"Importancia: {_estrellas(r.explicacion.nivel_importancia)}")
        else:
            sin_explicar += 1
            lineas.append("   (sin explicación disponible esta corrida)")
        lineas.append("")

    if sin_explicar:
        lineas.append(f"{sin_explicar} titular(es) mencionan tu shortlist pero "
                       f"no se explicaron (tope de la corrida, sin "
                       f"ANTHROPIC_API_KEY, o la explicación no pasó el filtro).")
        lineas.append("")

    lineas.append(DISCLAIMER)
    return "\n".join(lineas)
