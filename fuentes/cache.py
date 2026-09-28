"""Caché en disco de las respuestas, fuera de git.

Un pedido se guarda como JSON en `<dir>/<fuente>/<sha1 de la clave>.json`
con la hora en que se obtuvo. Una segunda corrida del backtest no vuelve
a pedir lo mismo. La caché guarda RESPUESTAS, no decisiones: si mañana
cambia el mapa ítem→nivel, la corrida siguiente reinterpreta lo cacheado.

`FUENTES_CACHE_DIR` (default `/var/lib/momentum/fuentes`). Cualquier ruta
dentro del checkout se rechaza: nada de esto se commitea.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

ENV_DIR = "FUENTES_CACHE_DIR"
DIR_DEFAULT = Path("/var/lib/momentum/fuentes")

_RAIZ_REPO = Path(__file__).resolve().parents[1]


def exigir_fuera_del_repo(path: Path) -> Path:
    candidato = path if path.is_absolute() else Path.cwd() / path
    try:
        resuelto = candidato.resolve()
    except OSError:
        resuelto = candidato.absolute()
    if resuelto == _RAIZ_REPO or _RAIZ_REPO in resuelto.parents:
        raise ValueError("cache_dentro_del_repo")
    return path


def directorio_configurado() -> Path:
    raw = os.environ.get(ENV_DIR, "").strip()
    return exigir_fuera_del_repo(Path(raw) if raw else DIR_DEFAULT)


class Cache:
    def __init__(self, directorio: Path | None = None, ahora: Callable[[], float] = time.time) -> None:
        self.dir = exigir_fuera_del_repo(directorio) if directorio is not None else directorio_configurado()
        self._ahora = ahora

    def _ruta(self, fuente: str, clave: str) -> Path:
        h = hashlib.sha1(clave.encode("utf-8")).hexdigest()[:20]
        return self.dir / fuente / f"{h}.json"

    def leer(self, fuente: str, clave: str, max_edad_s: float | None = None) -> dict | None:
        """El registro `{"clave", "obtenido", "valor"}` o None si no está
        o está más viejo que `max_edad_s`. Un archivo ilegible cuenta
        como ausente (se vuelve a pedir), nunca como valor."""
        ruta = self._ruta(fuente, clave)
        if not ruta.exists():
            return None
        try:
            reg = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(reg, dict) or reg.get("clave") != clave or "valor" not in reg:
            return None
        if max_edad_s is not None:
            obtenido = reg.get("obtenido_ts")
            if not isinstance(obtenido, (int, float)) or self._ahora() - obtenido > max_edad_s:
                return None
        return reg

    def escribir(self, fuente: str, clave: str, valor) -> dict:
        ruta = self._ruta(fuente, clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ts = self._ahora()
        reg = {
            "clave": clave,
            "obtenido_ts": ts,
            "obtenido": datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds"),
            "valor": valor,
        }
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        temporal.replace(ruta)
        return reg

    def obtener(self, fuente: str, clave: str, calcular: Callable[[], object], max_edad_s: float | None = None):
        """Valor cacheado o el de `calcular()`, que se guarda. Si
        `calcular` levanta, NO se guarda nada: un fallo no se cachea
        como dato (la próxima corrida vuelve a intentar)."""
        reg = self.leer(fuente, clave, max_edad_s)
        if reg is not None:
            return reg["valor"]
        valor = calcular()
        self.escribir(fuente, clave, valor)
        return valor
