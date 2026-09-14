"""Clasificador SHADOW -- Tanda 1 ya está en producción.

POR QUÉ. El dueño pidió medir el contrafactual ANTES de encender, y
después OK 2026-09-14 para aplicar SOLO Tanda 1. Este módulo:

  1. Congela las keywords PRE-Tanda1 (para el CF: qué habría pasado
     sin el cambio).
  2. Lee la lista visible `WAVE1_FRASES.md` (bloque TANDA1) y comprueba
     que coincide con lo que hay en `detector.py`.
  3. Clasifica offline con cualquier set -- misma regla que producción
     (substring, `ORDEN_PRIORIDAD`).

`run.py` no lo importa. NUNCA escribe watchlist ni llama a un bróker.
Tanda 2 no se carga."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from momentum_hunter.catalysts.detector import CATALYST_KEYWORDS, ORDEN_PRIORIDAD

WAVE1_DOC = Path(__file__).resolve().parent / "WAVE1_FRASES.md"
WAVE1_VERSION = "tanda1-v1"

_MARCA_INICIO = "<!-- TANDA1_INICIO -->"
_MARCA_FIN = "<!-- TANDA1_FIN -->"

# Snapshot de producción ANTES del OK Tanda1. El CF compara esto contra
# `CATALYST_KEYWORDS` actual. Si alguien edita esto para "hacer pasar"
# el delta, el test de coincidencia markdown↔detector no lo cubre --
# no tocar salvo que se revierta Tanda1.
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
    """Primera columna de la tabla: `frase` entre backticks, o None si
    la fila es separador / cabecera / vacía."""
    texto = celda.strip()
    if not texto or texto == "frase" or set(texto) <= {"-", ":"}:
        return None
    if texto.startswith("`") and texto.endswith("`") and len(texto) > 2:
        return texto[1:-1].strip().lower()
    return texto.lower() or None


def cargar_frases_propuestas(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Lee el markdown visible de Tanda 1. Si faltan marcas, falla:
    más vale no inventar una lista."""
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
    """Misma regla que `clasificar_titular`, pero devuelve también la
    frase que ganó."""
    kws = keywords if keywords is not None else CATALYST_KEYWORDS
    bajo = texto.lower()
    for tipo in ORDEN_PRIORIDAD:
        for kw in kws.get(tipo, ()):
            if kw in bajo:
                return MatchSombra(tipo=tipo, frase=kw)
    return None
