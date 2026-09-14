"""Clasificador SHADOW de catalizadores -- Wave 1.

POR QUÉ. El dueño pidió (2026-09-14) medir el contrafactual de ampliar
`CATALYST_KEYWORDS` ANTES de encenderlo. Si estas frases entran directo
a `detector.py`, el embudo cambia sin haber visto el delta. Este módulo
carga la lista escrita en `WAVE1_FRASES.md`, las une a las keywords de
producción SIN mutarlas, y clasifica offline. `run.py` no lo importa.

NUNCA escribe en la watchlist, NUNCA llama a un bróker, NUNCA decide
tamaños. Solo lee texto. El matching es el mismo que producción
(substring en minúsculas, `ORDEN_PRIORIDAD`) a propósito: si el
contrafactual usara otra regla, el número no diría nada del detector
real."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from momentum_hunter.catalysts.detector import CATALYST_KEYWORDS, ORDEN_PRIORIDAD

WAVE1_DOC = Path(__file__).resolve().parent / "WAVE1_FRASES.md"
WAVE1_VERSION = "wave1-shadow-v1"

_MARCA_INICIO = "<!-- WAVE1_PROPUESTAS_INICIO -->"
_MARCA_FIN = "<!-- WAVE1_PROPUESTAS_FIN -->"


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
    """Lee el markdown visible. Si el archivo no tiene marcas o un
    `### tipo` desconocido, falla: más vale no inventar una lista."""
    doc = Path(path) if path is not None else WAVE1_DOC
    crudo = doc.read_text(encoding="utf-8")
    if _MARCA_INICIO not in crudo or _MARCA_FIN not in crudo:
        raise ValueError(f"{doc} no tiene marcas WAVE1_PROPUESTAS")
    bloque = crudo.split(_MARCA_INICIO, 1)[1].split(_MARCA_FIN, 1)[0]

    por_tipo: dict[str, list[str]] = {}
    tipo: str | None = None
    tipos_validos = set(ORDEN_PRIORIDAD)
    for linea in bloque.splitlines():
        s = linea.strip()
        # `## buyback` abre un tipo; `### Variantes de trimestre` es
        # subsección humana y se ignora si la primera palabra no es un
        # tipo conocido. Wave 1 no inventa tipos.
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
        raise ValueError(f"{doc} no tiene frases propuestas parseables")
    return {t: tuple(frases) for t, frases in por_tipo.items()}


def keywords_propuestos(
    actuales: dict[str, tuple[str, ...]] | None = None,
    extras: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Unión CURRENT ∪ Wave 1. Copia nueva -- no muta
    `CATALYST_KEYWORDS`."""
    base = actuales if actuales is not None else CATALYST_KEYWORDS
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
    frase que ganó -- hace falta para el listado ticker|titular|tipo|frase
    del contrafactual. No reemplaza al detector de producción."""
    kws = keywords if keywords is not None else CATALYST_KEYWORDS
    bajo = texto.lower()
    for tipo in ORDEN_PRIORIDAD:
        for kw in kws.get(tipo, ()):
            if kw in bajo:
                return MatchSombra(tipo=tipo, frase=kw)
    return None
