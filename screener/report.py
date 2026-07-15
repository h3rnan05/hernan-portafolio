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
from datetime import UTC, datetime

from screener.config import ScreenerConfig
from screener.scoring import Puntuacion

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


def texto_telegram(ranking: list[Puntuacion], cfg: ScreenerConfig, universo_n: int) -> str:
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

    lineas.append(resumen_modelo(top))
    lineas.append("")
    lineas.append("Recuerda: esta lista es el punto de partida de tu investigación, no el final.")
    return "\n".join(lineas)


def markdown(ranking: list[Puntuacion], cfg: ScreenerConfig, universo_n: int) -> str:
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

    out.append("---")
    out.append("")
    out.append(resumen_modelo(top))
    return "\n".join(out) + "\n"
