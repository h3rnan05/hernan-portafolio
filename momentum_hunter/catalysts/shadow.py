"""Clasificador SHADOW de Tanda 1 -- producción NO se toca en este PR.

POR QUÉ. El dueño pidió CF primero; el expand de producción va en #121.
Este módulo lee `WAVE1_FRASES.md` (Tanda 1 mínima), las une al set PRE
sin mutar `CATALYST_KEYWORDS`, y clasifica offline.

`run.py` no lo importa. NUNCA escribe watchlist ni llama a un bróker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from momentum_hunter.catalysts.detector import CATALYST_KEYWORDS, ORDEN_PRIORIDAD

WAVE1_DOC = Path(__file__).resolve().parent / "WAVE1_FRASES.md"
WAVE1_VERSION = "tanda1-v1"

_MARCA_INICIO = "<!-- TANDA1_INICIO -->"
_MARCA_FIN = "<!-- TANDA1_FIN -->"

# Producción en ESTE PR (sin Tanda 1). El CF compara esto contra
# PRE ∪ markdown. No mutar: si detector.py cambia, el test de
# coincidencia PRE↔producción explota.
CATALYST_KEYWORDS_PRE_TANDA1: dict[str, tuple[str, ...]] = {
    tipo: kws for tipo, kws in CATALYST_KEYWORDS.items()
    if tipo not in ("buyback", "earnings")
}
CATALYST_KEYWORDS_PRE_TANDA1["buyback"] = (
    "share buyback", "repurchase program", "stock buyback", "buyback program",
)
CATALYST_KEYWORDS_PRE_TANDA1["earnings"] = (
    "quarterly results", "earnings results", "beats estimates", "misses estimates",
    "q1 results", "q2 results", "q3 results", "q4 results", "reports revenue of",
)


@dataclass(frozen=True)
class MatchSombra:
    tipo: str
    frase: str


def _celda_frase(celda: str) -> str | None:
    texto = celda.strip()
    if not texto or texto == "frase" or set(texto) <= {"-", ":"}:
        return None
    if texto.startswith("`") and texto.endswith("`") and len(texto) > 2:
        return texto[1:-1].strip().lower()
    return texto.lower() or None


def cargar_frases_propuestas(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Lee Tanda 1 del markdown. Sin marcas → falla, no inventa."""
    doc = Path(path) if path is not None else WAVE1_DOC
    crudo = doc.read_text(encoding="utf-8")
    if _MARCA_INICIO not in crudo or _MARCA_FIN not in crudo:
        raise ValueError(f"{doc} no tiene marcas TANDA1")
    bloque = crudo.split(_MARCA_INICIO, 1)[1].split(_MARCA_FIN, 1)[0]

    por_tipo: dict[str, list[str]] = {}
    tipo: str | None = None
    tipos_validos = set(ORDEN_PRIORIDAD)
    for linea in bloque.splitlines():
        s = linea.strip()
        if s.startswith("## ") and not s.startswith("####"):
            candidato = s.lstrip("#").strip().split()[0].lower()
            if candidato in tipos_validos:
                tipo = candidato
                por_tipo.setdefault(tipo, [])
            continue
        if not s.startswith("|") or tipo is None:
            continue
        partes = [p.strip() for p in s.strip("|").split("|")]
        if not partes:
            continue
        frase = _celda_frase(partes[0])
        if frase is None:
            continue
        if frase not in por_tipo[tipo]:
            por_tipo[tipo].append(frase)

    if not por_tipo:
        raise ValueError(f"{doc} no tiene frases Tanda 1 parseables")
    return {t: tuple(frases) for t, frases in por_tipo.items()}


def keywords_propuestos(
    actuales: dict[str, tuple[str, ...]] | None = None,
    extras: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Unión PRE ∪ Tanda 1. Copia nueva -- no muta producción."""
    base = actuales if actuales is not None else CATALYST_KEYWORDS_PRE_TANDA1
    extra = extras if extras is not None else cargar_frases_propuestas()
    out: dict[str, tuple[str, ...]] = {}
    for tipo, kws in base.items():
        visto: set[str] = set()
        merged: list[str] = []
        for kw in tuple(kws) + tuple(extra.get(tipo, ())):
            if kw in visto:
                continue
            visto.add(kw)
            merged.append(kw)
        out[tipo] = tuple(merged)
    return out


def clasificar_sombra(
    texto: str,
    keywords: dict[str, tuple[str, ...]] | None = None,
) -> MatchSombra | None:
    kws = keywords if keywords is not None else CATALYST_KEYWORDS
    bajo = texto.lower()
    for tipo in ORDEN_PRIORIDAD:
        for kw in kws.get(tipo, ()):
            if kw in bajo:
                return MatchSombra(tipo=tipo, frase=kw)
    return None
