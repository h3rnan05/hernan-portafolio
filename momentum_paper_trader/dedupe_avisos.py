"""Anti-spam de los ERROR que pueden repetirse en cada tick del vigía.

Cada tick es un proceso nuevo (`python -m momentum_paper_trader.run`),
así que la memoria no alcanza: la marca vive en disco, un archivo por
clave, bajo `MOMENTUM_AVISOS_DIR` (el mismo directorio que el aviso de
fallo de IA, fuera del git de estado). La clave incluye la fecha de
sesión en Nueva York: dentro del día no se repite; al día siguiente,
si el problema sigue, vuelve a avisar. Eso cubre el cierre y la
apertura siguiente sin un Telegram por minuto.

Nunca decide ni coloca. Si no se puede leer la marca, se avisa (mejor
un duplicado que el silencio del 2026-09-25)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("momentum_paper_trader.dedupe_avisos")

_NY = ZoneInfo("America/New_York")
# Las marcas viejas no aportan: el día de sesión ya cambió y la clave
# nueva no las usa. Se podan para que el archivo no crezca sin límite.
_RETENCION = timedelta(days=14)


def fecha_sesion(ahora: datetime) -> str:
    return ahora.astimezone(_NY).date().isoformat()


def clave(tipo: str, ticker: str, ahora: datetime, detalle: str = "") -> str:
    """Una marca por tipo, ticker y día de sesión. `detalle` separa dos
    trades del mismo símbolo (el `creado_en` de la revisión)."""
    extra = f":{detalle}" if detalle else ""
    return f"{tipo}:{ticker}:{fecha_sesion(ahora)}{extra}"


def ruta() -> Path:
    base = os.environ.get("MOMENTUM_AVISOS_DIR", "/var/lib/momentum")
    return Path(base) / "posiciones_avisos.json"


def ya_avisada(clave_aviso: str) -> bool:
    claves = _cargar().get("claves")
    return isinstance(claves, dict) and clave_aviso in claves


def marcar(clave_aviso: str, ahora: datetime | None = None) -> None:
    ahora = ahora or datetime.now(UTC)
    try:
        estado = _cargar()
        claves = estado.get("claves")
        if not isinstance(claves, dict):
            claves = {}
        claves[clave_aviso] = ahora.astimezone(UTC).isoformat()
        _podar(claves, ahora)
        estado["claves"] = claves
        _guardar(estado)
    except Exception as ex:
        # Sin marca, el tick siguiente puede repetir el aviso. Peor
        # sería tragarse el ERROR que este módulo existe para dejar salir.
        log.warning("no se pudo guardar la marca de aviso (%s)", type(ex).__name__)


def _cargar() -> dict:
    try:
        data = json.loads(ruta().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _guardar(estado: dict) -> None:
    path = ruta()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, path)


def _podar(claves: dict, ahora: datetime) -> None:
    limite = ahora.astimezone(UTC) - _RETENCION
    for k in list(claves):
        try:
            marca = datetime.fromisoformat(str(claves[k]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            del claves[k]
            continue
        if marca.tzinfo is None:
            marca = marca.replace(tzinfo=UTC)
        if marca < limite:
            del claves[k]
