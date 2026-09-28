"""Rutas de estado y telemetría fuera del árbol git.

El 2026-09-28 una escritura concurrente dentro del checkout abandonó el
stash del persist y el working tree perdió el libro de revisiones. El
estado vivo no puede vivir en un archivo que git también reescribe.

`MOMENTUM_ESTADO_DIR` (default `/var/lib/momentum/estado`) es la raíz.
Las subcarpetas conservan la ruta relativa que tenían dentro del repo
(`momentum_hunter/telemetria/<fecha>/...` sigue siendo esa ruta, un
nivel más abajo).

Migración: si el destino no existe y el archivo legado (dentro del
repo) sí, se copia UNA vez. No se borra el legado y no se pisa un
destino que ya existe: la copia nueva puede ser más vieja que lo que
el proceso ya escribió ahí.

`MOMENTUM_ESTADO_MIGRAR=0` apaga la copia. Lo usan las pruebas para no
arrastrar el árbol real. En producción se deja encendido.

Watchlist: en el VPS el buscador y el ejecutor comparten disco, así que
`watchlist.json` también sale del repo. El ejecutor no lo escribe: sus
mutaciones siguen en el overlay (`MOMENTUM_WATCHLIST_STATE`, ya fuera
de git). El buscador es quien materializa el canónico.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

DEFAULT_RAIZ = Path("/var/lib/momentum/estado")

# Relativos al checkout. El mismo listado está en
# scripts/migrar_estado_fuera_del_repo.sh: el deploy por SSH corre esa
# copia ANTES de tener este módulo en el VPS.
RELATIVOS = (
    "momentum_hunter/telemetria",
    "momentum_hunter/auditoria",
    "momentum_hunter/alertas_enviadas.json",
    "momentum_hunter/estado_diario.json",
    "momentum_hunter/universo_cache.json",
    "momentum_hunter/watchlist.json",
    "momentum_hunter/diario",
    "momentum_paper_trader/telemetria",
    "momentum_paper_trader/revisiones.json",
    "momentum_paper_trader/archivo_triggered.jsonl",
)


def raiz() -> Path:
    bruto = os.environ.get("MOMENTUM_ESTADO_DIR", "").strip()
    return Path(bruto) if bruto else DEFAULT_RAIZ


def migrar_activo() -> bool:
    return os.environ.get("MOMENTUM_ESTADO_MIGRAR", "1").strip() != "0"


def legado(relativo: str) -> Path:
    return _REPO / relativo


def destino(relativo: str) -> Path:
    return raiz() / relativo


def _copiar_una_vez(viejo: Path, dest: Path) -> None:
    """Copia a un temporal y renombra. Un corte a mitad no deja un
    destino a medias que la próxima arrancada tomaría por bueno."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".migrando")
    if tmp.exists():
        if tmp.is_dir():
            shutil.rmtree(tmp)
        else:
            tmp.unlink()
    if viejo.is_dir():
        shutil.copytree(viejo, tmp)
    else:
        shutil.copy2(viejo, tmp)
    os.replace(tmp, dest)


def resolver(relativo: str, *, es_dir: bool = False) -> Path:
    dest = destino(relativo)
    viejo = legado(relativo)
    try:
        if dest.resolve() == viejo.resolve():
            return dest
    except OSError:
        pass
    if migrar_activo() and not dest.exists() and viejo.exists():
        try:
            _copiar_una_vez(viejo, dest)
        except OSError:
            # Sin destino vacío: si no se pudo copiar, el caller de
            # revisiones tiene que negarse a operar, no inventar un libro.
            return dest
    try:
        if es_dir:
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return dest


class RutaEstado:
    """Path perezoso: se resuelve en cada uso, no al importar.

    Los defaults de función (`def f(path=PATH)`) se atan al definir la
    función. Si PATH fuera un Path concreto, ni el entorno ni un
    monkeypatch posterior cambiarían ese default. Esta clase delega en
    `resolver()` en cada operación.
    """

    def __init__(self, relativo: str, *, es_dir: bool = False) -> None:
        self.relativo = relativo
        self.es_dir = es_dir

    def resolver(self) -> Path:
        return resolver(self.relativo, es_dir=self.es_dir)

    def __fspath__(self) -> str:
        return os.fspath(self.resolver())

    def __truediv__(self, otro: object) -> Path:
        return self.resolver() / otro  # type: ignore[operator]

    def __str__(self) -> str:
        return str(self.resolver())

    def __repr__(self) -> str:
        return f"RutaEstado({self.relativo!r})"

    def __eq__(self, otro: object) -> bool:
        if isinstance(otro, RutaEstado):
            return self.resolver() == otro.resolver()
        try:
            return self.resolver() == Path(os.fspath(otro)).resolve()  # type: ignore[arg-type]
        except (TypeError, ValueError, OSError):
            return False

    def __getattr__(self, nombre: str):
        return getattr(self.resolver(), nombre)
