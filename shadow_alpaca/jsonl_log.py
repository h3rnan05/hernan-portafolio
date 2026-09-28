"""Append de una línea JSON. El directorio de la sombra no puede caer
dentro de los paquetes que operan: su telemetría se commitea, y un
archivo nuestro ahí entraría en el mismo `git add` que el escaneo.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("shadow_alpaca.jsonl")

_PAQUETES_OPERATIVOS = ("momentum_hunter", "momentum_paper_trader")


def dir_salida(explicito: Path | None = None) -> Path:
    if explicito is not None:
        return explicito
    raw = os.environ.get("SHADOW_ALPACA_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent / "salida"


def exigir_directorio_aislado(path: Path) -> Path:
    """Rechaza escribir dentro del hunter o del paper trader.

    Se resuelve el path para que un symlink hacia `telemetria/` no pase
    como si fuera otro directorio. Si el destino todavía no existe, se
    mira el ancestro que sí existe.
    """
    candidato = path if path.is_absolute() else Path.cwd() / path
    try:
        resuelto = candidato.resolve()
    except OSError:
        resuelto = candidato.absolute()
    raiz = Path(__file__).resolve().parents[1]
    for nombre in _PAQUETES_OPERATIVOS:
        prohibido = (raiz / nombre).resolve()
        if resuelto == prohibido or prohibido in resuelto.parents:
            raise ValueError("directorio_operativo")
    return path


def append_linea(path: Path, registro: dict) -> None:
    exigir_directorio_aislado(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    linea = json.dumps(registro, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(linea + "\n")
        fh.flush()


def leer_jsonl(path: Path) -> list[dict]:
    """Líneas ilegibles se omiten. Un archivo que no está no es una lista
    vacía: el caller distingue 'no hay archivo' de 'hay archivo sin filas'."""
    try:
        texto = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # No está: no es una serie vacía ni un archivo corrupto. El caller
        # que necesita distinguir los casos mira `exists()` antes.
        return []
    except OSError as ex:
        log.warning("jsonl ilegible (%s): %s", type(ex).__name__, path.name)
        return []
    out: list[dict] = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError:
            log.warning("línea jsonl ilegible, se omite: %s", path.name)
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
