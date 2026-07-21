"""Formatea la shortlist para humanos.

El scoring/ranking (screener/scoring.py) NO cambia acá — este módulo solo
traduce los sub-scores ya calculados a lenguaje llano, como lo explicaría
un analista a alguien que está aprendiendo a invertir. Nunca imprime
números de factores crudos ("Momentum 93"): cada sub-score >=60 se traduce
a una frase en español simple, sin jerga.

Ojo con la honestidad de las frases: solo se dice lo que el factor
realmente mide. Por ejemplo `liquidez` es volumen de negociación (dollar
volume), no flujo de compras institucionales — no existe ese dato en el
screener, así que no se inventa esa frase aunque "suene bien".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from screener.config import ScreenerConfig
from screener.options_ideas import (
    DISCLAIMER,
    DatosOpciones,
    generar_ideas,
    movimiento_esperado,
)
from screener.scoring import Puntuacion

# Sectores GICS que trae yfinance en info["sector"]. Fallback genérico para
# lo que no esté mapeado (sector desconocido, o taxonomías distintas).
_EMOJI_SECTOR: dict[str, str] = {
    "Technology": "💻",
    "Healthcare": "⚕️",
    "Financial Services": "🏦",
    "Consumer Cyclical": "🛍️",
    "Industrials": "🏭",
    "Communication Services": "📡",
    "Consumer Defensive": "🛒",
    "Energy": "🛢️",
    "Basic Materials": "⛏️",
    "Real Estate": "🏠",
    "Utilities": "⚡",
}
_EMOJI_SECTOR_DEFAULT = "🏢"

# Cada factor tiene una frase "fuerte" (score >= 85) y una "sólida"
# (60 <= score < 85). Nunca se muestra el número: solo la idea en español
# simple, del tipo que le explicarías a alguien nuevo en esto.
_FRASES: dict[str, dict[str, str]] = {
    "momentum": {
        "fuerte": "Ha estado subiendo de forma sostenida durante varios meses — "
                  "una de las que más ha subido de todo el grupo analizado.",
        "solida": "Ha estado subiendo de forma sostenida durante varios meses.",
    },
    "tendencia": {
        "fuerte": "El precio se mantiene claramente por encima de sus promedios "
                  "de largo plazo: la tendencia alcista es muy clara.",
        "solida": "El precio se mantiene por encima de sus promedios de largo "
                  "plazo, señal de una tendencia saludable.",
    },
    "baja_vol": {
        "fuerte": "Se mueve con muy poca volatilidad comparada con el resto del mercado.",
        "solida": "Se mueve con relativamente poca volatilidad.",
    },
    "liquidez": {
        "fuerte": "Se negocia con muchísimo volumen todos los días, lo que facilita "
                  "entrar y salir de la posición sin mover el precio.",
        "solida": "Se negocia con buen volumen diario, fácil de comprar y vender.",
    },
    "calidad": {
        "fuerte": "El negocio es muy rentable — gana mucho dinero sobre el capital "
                  "que usa, una de las más sólidas de su sector.",
        "solida": "El negocio es rentable: gana dinero de forma consistente sobre su capital.",
    },
    "valor": {
        "fuerte": "Se puede comprar a un precio muy razonable frente a lo que gana la empresa.",
        "solida": "Se puede comprar a un precio razonable en relación con sus ganancias.",
    },
}

# Para el resumen del modelo: qué está "buscando" cada factor, en un
# sustantivo corto que se pueda concatenar en una lista.
_TEMA_FACTOR: dict[str, str] = {
    "momentum":  "acciones con impulso fuerte en el precio",
    "tendencia": "acciones en una tendencia alcista clara",
    "baja_vol":  "acciones estables, de baja volatilidad",
    "liquidez":  "acciones muy líquidas y fáciles de operar",
    "calidad":   "negocios de alta calidad y rentabilidad",
    "valor":     "empresas que cotizan a precios razonables",
}


def _frase(factor: str, score: float) -> str | None:
    plantilla = _FRASES.get(factor)
    if not plantilla or score < 60:
        return None
    return plantilla["fuerte"] if score >= 85 else plantilla["solida"]


def razones(p: Puntuacion, n: int) -> list[str]:
    """Las n frases (en español llano) de los factores con mayor sub-score
    que explican por qué el modelo encontró esta empresa."""
    top = sorted(p.sub.items(), key=lambda kv: kv[1], reverse=True)
    frases = []
    for factor, score in top:
        f = _frase(factor, score)
        if f:
            frases.append(f)
        if len(frases) == n:
            break
    return frases


def resumen_modelo(top: list[Puntuacion]) -> str:
    """'Qué está viendo el modelo hoy': los factores mejor representados
    en la shortlist (promedio de sub-scores) + el sector más frecuente.
    Calculado de los datos reales del día, nunca un texto fijo."""
    if not top:
        return "Hoy no hubo suficientes empresas que pasaran los filtros."

    promedios: dict[str, float] = {}
    conteos: dict[str, int] = {}
    for p in top:
        for factor, score in p.sub.items():
            promedios[factor] = promedios.get(factor, 0.0) + score
            conteos[factor] = conteos.get(factor, 0) + 1
    for factor in promedios:
        promedios[factor] /= conteos[factor]

    mejores = sorted(promedios.items(), key=lambda kv: kv[1], reverse=True)
    temas = [_TEMA_FACTOR[f] for f, _ in mejores[:3] if f in _TEMA_FACTOR]

    sectores = Counter(p.sector for p in top if p.sector)
    sector_top = sectores.most_common(1)
    if sector_top and sector_top[0][1] >= max(2, len(top) // 4):
        temas.append(f"varias empresas del sector {sector_top[0][0]}")

    if not temas:
        return "Hoy el modelo no muestra una preferencia clara por ningún factor en particular."
    return "Hoy el modelo está favoreciendo especialmente: " + ", ".join(temas) + "."


def _nombre_display(p: Puntuacion) -> str:
    return p.nombre or p.ticker


def checklist_investigacion(p: Puntuacion) -> list[str]:
    """El checklist de investigación — preguntas fijas, nunca respuestas.
    El objetivo es enseñar cómo piensa un analista profesional antes de
    invertir, no darle al usuario la respuesta hecha. Determinístico: no
    llama a ningún dato ni LLM, es la misma lista de preguntas siempre
    (con el nombre/industria de la empresa insertados donde corresponde)."""
    nombre = _nombre_display(p)
    industria = p.industria or p.sector
    competidores = (
        f"¿Quiénes son los competidores más grandes de {nombre} dentro de {industria}?"
        if industria else f"¿Quiénes son los competidores más grandes de {nombre}?"
    )
    return [
        f"¿Cuándo son los próximos resultados trimestrales (earnings) de {nombre}?",
        "¿Los ingresos han estado creciendo en los últimos años?",
        "¿Cuánta deuda tiene la empresa?",
        "¿Qué está esperando el consenso de analistas para los próximos trimestres?",
        "¿Los directivos (insiders) han estado comprando o vendiendo acciones propias?",
        "¿Los inversionistas institucionales están aumentando o reduciendo su posición?",
        competidores,
        "¿Hay noticias importantes de la empresa esta semana?",
        "¿La valuación actual es cara comparada con su propia historia o con su industria?",
        "¿Cómo le afectaría un cambio en las tasas de interés?",
        "¿Qué riesgos debo entender antes de invertir en esta empresa?",
    ]


def _fmt_pct(x: float | None) -> str:
    return f"{x:.0%}" if x is not None else "No disponible"


def _fmt_dato(x: str | None) -> str:
    return x if x else "No disponible"


def _bloque_opciones_telegram(p: Puntuacion, datos: DatosOpciones) -> list[str]:
    lineas = [
        "   💡 Ideas de opciones para investigar",
        f"   Volatilidad implícita actual: {_fmt_pct(datos.iv_actual)}",
        f"   Volatilidad histórica: {_fmt_pct(p.vol_historica_anual)}",
        "   IV Rank: No disponible — requiere histórico de IV que el "
        "screener no recolecta hoy.",
        f"   Próximos resultados: {_fmt_dato(datos.proxima_fecha_resultados)}",
        f"   Movimiento esperado: {movimiento_esperado(p, datos)}",
        "",
    ]
    ideas = generar_ideas(p, datos)
    if not ideas:
        lineas.append("   Sin tendencia técnica suficientemente definida para "
                       "sugerir una estrategia a investigar.")
    for idea in ideas:
        lineas.append(f"   Estrategia posible a investigar: {idea.nombre}")
        lineas.append(f"   Razón: {idea.razon}")
        lineas.append(f"   Riesgo máximo: {idea.riesgo_maximo}")
        lineas.append(f"   Ganancia máxima: {idea.ganancia_maxima}")
        lineas.append(f"   Breakeven: {idea.breakeven}")
        lineas.append(f"   Nota educativa: {idea.notas}")
        lineas.append("")
    lineas.append(f"   {DISCLAIMER}")
    return lineas


def _bloque_opciones_markdown(p: Puntuacion, datos: DatosOpciones) -> list[str]:
    out = [
        "**Ideas de opciones para investigar**",
        "",
        f"- Volatilidad implícita actual: {_fmt_pct(datos.iv_actual)}",
        f"- Volatilidad histórica: {_fmt_pct(p.vol_historica_anual)}",
        "- IV Rank: No disponible — requiere histórico de IV que el "
        "screener no recolecta hoy.",
        f"- Próximos resultados: {_fmt_dato(datos.proxima_fecha_resultados)}",
        f"- Movimiento esperado: {movimiento_esperado(p, datos)}",
        "",
    ]
    ideas = generar_ideas(p, datos)
    if not ideas:
        out.append("Sin tendencia técnica suficientemente definida para sugerir "
                    "una estrategia a investigar.")
    for idea in ideas:
        out.append(f"*Estrategia posible a investigar: {idea.nombre}*")
        out.append(f"- Razón: {idea.razon}")
        out.append(f"- Riesgo máximo: {idea.riesgo_maximo}")
        out.append(f"- Ganancia máxima: {idea.ganancia_maxima}")
        out.append(f"- Breakeven: {idea.breakeven}")
        out.append(f"- Nota educativa: {idea.notas}")
        out.append("")
    out.append(f"**{DISCLAIMER}**")
    return out


@dataclass(frozen=True)
class DiffShortlist:
    """Diferencia entre la shortlist de la corrida anterior y la de hoy.
    'Anterior' es literalmente el shortlist_hoy.json de antes de
    sobrescribirlo -- no asume que fue "ayer" en el calendario (fines de
    semana, feriados, o una corrida saltada comparan contra la última
    corrida real, que es lo honesto)."""
    nuevos: list[str]
    salieron: list[str]
    deltas: dict[str, float]  # ticker -> score_hoy - score_anterior (solo los que siguen en la lista)
    lider_anterior: str | None = None  # ticker #1 de la corrida anterior, para detectar cambio de líder


def calcular_diff(anterior: dict | None, top_hoy: list[Puntuacion]) -> DiffShortlist:
    """anterior: el dict ya parseado de shortlist_hoy.json ANTES de
    sobrescribirlo (o None si es la primera corrida / no se pudo leer)."""
    if not anterior:
        return DiffShortlist(nuevos=[], salieron=[], deltas={}, lider_anterior=None)
    filas_antes = anterior.get("shortlist", [])
    scores_antes = {fila["ticker"]: fila["score"] for fila in filas_antes}
    scores_hoy = {p.ticker: p.score_total for p in top_hoy}
    nuevos = [t for t in scores_hoy if t not in scores_antes]
    salieron = [t for t in scores_antes if t not in scores_hoy]
    deltas = {t: round(scores_hoy[t] - scores_antes[t], 1) for t in scores_hoy if t in scores_antes}
    lider_anterior = filas_antes[0]["ticker"] if filas_antes else None
    return DiffShortlist(nuevos=nuevos, salieron=salieron, deltas=deltas, lider_anterior=lider_anterior)


def _emoji_sector(sector: str | None) -> str:
    return _EMOJI_SECTOR.get(sector, _EMOJI_SECTOR_DEFAULT) if sector else _EMOJI_SECTOR_DEFAULT


def _flecha_delta(delta: float | None) -> str:
    """▲N / ▼N / = -- redondeado a entero porque el score ya se muestra
    sin decimales; None (ticker nuevo, sin comparación posible) no
    imprime nada."""
    if delta is None:
        return ""
    redondeado = round(delta)
    if redondeado > 0:
        return f" ▲{redondeado}"
    if redondeado < 0:
        return f" ▼{abs(redondeado)}"
    return " ="


def _destacado_del_dia(mostrar: list[Puntuacion], diff: DiffShortlist | None) -> str | None:
    """Una línea que dirige la atención a lo más relevante del día: quién
    lidera (si cambió respecto a la corrida anterior) o, si el líder se
    mantuvo, quién tuvo la mayor subida de score entre los mostrados.
    Devuelve None sin historial todavía (primera corrida) -- no se inventa
    un destacado sin datos para compararlo."""
    if not diff or not mostrar or (not diff.deltas and not diff.nuevos):
        return None
    lider = mostrar[0]
    es_nuevo = lider.ticker in diff.nuevos
    if es_nuevo or (diff.lider_anterior and diff.lider_anterior != lider.ticker):
        if es_nuevo:
            detalle = f"entra por primera vez a la shortlist liderando con {lider.score_total:.0f} puntos"
        else:
            # Si el score del nuevo líder no cambió, omitir la flecha de
            # delta: combinar "toma el primer lugar" con "=" leería como
            # contradictorio (el cambio es de liderazgo, no de score).
            delta_lider = diff.deltas.get(lider.ticker)
            sufijo = _flecha_delta(delta_lider) if delta_lider else ""
            detalle = f"toma el primer lugar con {lider.score_total:.0f} puntos{sufijo}"
        return f"⭐ Destacado del día: {lider.ticker} {detalle}."

    subidas = {p.ticker: diff.deltas[p.ticker] for p in mostrar if diff.deltas.get(p.ticker, 0) > 0}
    if subidas:
        ticker_max = max(subidas, key=subidas.get)
        score_max = next(p.score_total for p in mostrar if p.ticker == ticker_max)
        return (f"⭐ Destacado del día: {ticker_max} tuvo la mayor subida de puntaje hoy "
                f"(▲{round(subidas[ticker_max])}, ahora {score_max:.0f} puntos).")
    return None


def texto_telegram_corto(
    ranking: list[Puntuacion], cfg: ScreenerConfig, universo_n: int,
    diff: DiffShortlist | None = None,
) -> str:
    """El mensaje diario real: solo avisa qué empresas pasaron el
    screener, sin razones/checklist/opciones -- eso satura Telegram con
    algo que nadie termina de leer. La investigación profunda se pide
    bajo demanda con /report TICKER (telegram_bot/report_command.py),
    que sí reutiliza razones()/checklist_investigacion()/ideas de
    opciones para ESE ticker en particular. Solo se muestran las primeras
    cfg.top_n_mensaje_diario -- el resto vía /list."""
    hoy = datetime.now(UTC).strftime("%Y-%m-%d")
    top = ranking[:cfg.top_n]
    mostrar = top[:cfg.top_n_mensaje_diario]
    lineas = [
        "Buenos días.",
        "",
        f"Analicé {universo_n} empresas hoy ({hoy}).",
        f"{len(top)} pasaron todos los filtros cuantitativos.",
        "",
        "Esto NO es una recomendación de compra.",
        "",
    ]

    if diff and (diff.nuevos or diff.salieron):
        if diff.nuevos:
            lineas.append("🟢 Nuevas:")
            lineas += [f"  {t}" for t in diff.nuevos]
        if diff.salieron:
            lineas.append("🔴 Salieron:")
            lineas += [f"  {t}" for t in diff.salieron]
        lineas.append("")

    destacado = _destacado_del_dia(mostrar, diff)
    if destacado:
        lineas.append(destacado)
        lineas.append("")

    lineas.append("Empresas destacadas:")
    lineas.append("")
    for i, p in enumerate(mostrar, 1):
        es_nuevo = diff is not None and p.ticker in diff.nuevos
        emoji = _emoji_sector(p.sector)
        sufijo = " 🟢 NUEVA" if es_nuevo else _flecha_delta(diff.deltas.get(p.ticker) if diff else None)
        lineas.append(f"{i}. {emoji} {p.ticker} ({p.score_total:.0f}{sufijo})")
        industria = p.industria or p.sector
        if industria:
            lineas.append(f"   {industria}")
        lineas.append("")

    restantes = len(top) - len(mostrar)
    if restantes > 0:
        lineas.append(f"... y {restantes} más. Escribe /list")
        lineas.append("")

    lineas.append("Para investigar cualquiera escribe:")
    lineas.append("")
    lineas.append("/report TICKER")
    if mostrar:
        ejemplos = "  ·  ".join(f"/report {p.ticker}" for p in mostrar[:3])
        lineas.append("")
        lineas.append(f"Ejemplo: {ejemplos}")
    return "\n".join(lineas)


def texto_telegram_lista_completa(ranking: list[Puntuacion], universo_n: int) -> str:
    """/list -- la shortlist completa de hoy (hasta cfg.top_n), sin
    truncar. Mismo formato visual que texto_telegram_corto, sin el
    saludo/diff/disclaimer repetidos."""
    hoy = datetime.now(UTC).strftime("%Y-%m-%d")
    lineas = [f"📋 Shortlist completa de hoy ({len(ranking)} de {universo_n} analizadas) -- {hoy}", ""]
    for i, p in enumerate(ranking, 1):
        emoji = _emoji_sector(p.sector)
        lineas.append(f"{i}. {emoji} {p.ticker} ({p.score_total:.0f}/100)")
        industria = p.industria or p.sector
        if industria:
            lineas.append(f"   {industria}")
    lineas.append("")
    lineas.append("Para investigar cualquiera: /report TICKER")
    return "\n".join(lineas)


def texto_telegram(
    ranking: list[Puntuacion], cfg: ScreenerConfig, universo_n: int,
    datos_opciones: dict[str, DatosOpciones] | None = None,
) -> str:
    """Versión larga (razones + checklist + ideas de opciones por cada
    empresa) -- ya NO se manda automáticamente por Telegram (ver
    texto_telegram_corto); queda como el contenido completo persistido en
    shortlist_hoy.md y como la base que reutiliza /report TICKER."""
    hoy = datetime.now(UTC).strftime("%Y-%m-%d")
    top = ranking[:cfg.top_n]
    lineas = [
        "Buenos días.",
        "",
        f"Analicé {universo_n} empresas hoy ({hoy}).",
        f"{len(top)} pasaron todos los filtros cuantitativos.",
        "",
        "Esto NO son recomendaciones de compra.",
        "Son candidatos para que investigues más a fondo.",
        "",
    ]
    for i, p in enumerate(top, 1):
        industria = f" · {p.industria}" if p.industria else (f" · {p.sector}" if p.sector else "")
        lineas.append(f"{i}. {_nombre_display(p)} ({p.ticker}) — {p.score_total:.0f}/100{industria}")
        lineas.append("   ¿Por qué el modelo encontró esta empresa?")
        r = razones(p, cfg.razones_por_accion)
        if r:
            for frase in r:
                lineas.append(f"   • {frase}")
        else:
            lineas.append("   • Puntúa de forma pareja en varios factores, sin uno que domine.")
        lineas.append("")
        lineas.append("   ¿Qué deberías investigar?")
        for pregunta in checklist_investigacion(p):
            lineas.append(f"   ☐ {pregunta}")
        lineas.append("")
        datos = (datos_opciones or {}).get(p.ticker) or DatosOpciones(ticker=p.ticker)
        lineas.extend(_bloque_opciones_telegram(p, datos))
        lineas.append("")

    lineas.append(resumen_modelo(top))
    lineas.append("")
    lineas.append("Recuerda: esta lista es el punto de partida de tu investigación, no el final.")
    return "\n".join(lineas)


def markdown(
    ranking: list[Puntuacion], cfg: ScreenerConfig, universo_n: int,
    datos_opciones: dict[str, DatosOpciones] | None = None,
) -> str:
    hoy = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    top = ranking[:cfg.top_n]
    out = [
        f"# Shortlist del día — {hoy}",
        "",
        f"Analicé **{universo_n}** empresas hoy. **{len(top)}** pasaron todos los "
        "filtros cuantitativos.",
        "",
        "**Esto NO son recomendaciones de compra — son candidatos para investigar.**",
        "",
    ]
    for i, p in enumerate(top, 1):
        industria = p.industria or p.sector or "—"
        out.append(f"## {i}. {_nombre_display(p)} ({p.ticker}) — {p.score_total:.0f}/100")
        out.append(f"*{industria}*")
        out.append("")
        out.append("¿Por qué el modelo encontró esta empresa?")
        r = razones(p, cfg.razones_por_accion)
        if r:
            for frase in r:
                out.append(f"- {frase}")
        else:
            out.append("- Puntúa de forma pareja en varios factores, sin uno que domine.")
        out.append("")
        out.append("**¿Qué deberías investigar?**")
        out.append("")
        for pregunta in checklist_investigacion(p):
            out.append(f"- [ ] {pregunta}")
        out.append("")
        datos = (datos_opciones or {}).get(p.ticker) or DatosOpciones(ticker=p.ticker)
        out.extend(_bloque_opciones_markdown(p, datos))
        out.append("")

    out.append("---")
    out.append("")
    out.append(resumen_modelo(top))
    return "\n".join(out) + "\n"
