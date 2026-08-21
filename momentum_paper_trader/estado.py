"""Persistencia de qué entradas TRIGGERED ya pasaron por la revisión de la
IA -- mismo principio que el resto del repo (JSON chico, committeado por el
workflow, ver `momentum_hunter/heartbeat.py`/`tracker.py`).

Antes de la capa de decisión con IA (ver `ia_decision.py`), esto solo
registraba órdenes colocadas -- ahora registra CADA revisión, haya
terminado en orden o no. Es la diferencia importante: si no se registrara
también el "no" de la IA, cada corrida volvería a preguntarle lo mismo
sobre la misma entrada TRIGGERED (que queda en `watchlist.json` varios
días, ver `watchlist.RETENCION_DIAS_TERMINALES`), gastando llamadas a la
API y -- peor -- dándole otra oportunidad de decir "sí" por puro azar de
muestreo del modelo hasta que acierte que sí. Una entrada TRIGGERED se
revisa UNA vez, con cualquier resultado, y se queda así.

La clave es (ticker, `creado_en`) -- `creado_en` identifica la entrada
ÚNICA de la watchlist, no solo el ticker, así que el mismo ticker
disparando en dos días distintos genera dos revisiones distintas
correctamente."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

PATH = Path(__file__).resolve().parent / "revisiones.json"


@dataclass
class RevisionIA:
    ticker: str
    creado_en: str   # `EntradaWatchlist.creado_en` -- identifica la entrada única
    entro: bool       # veredicto final de la IA (ya con el "cinturón y tirantes" aplicado)
    confianza: int
    razonamiento: str
    timestamp: str
    # Solo se llenan si `entro=True` -- una revisión que resultó en "no
    # entrar" nunca tuvo una orden real que registrar.
    order_id: str | None = None
    cantidad: int | None = None
    precio_entrada: float | None = None
    stop: float | None = None
    objetivo: float | None = None


def _clave(ticker: str, creado_en: str) -> str:
    return f"{ticker}|{creado_en}"


def cargar(path: Path = PATH) -> list[RevisionIA]:
    """Un archivo corrupto no debe tumbar la corrida -- se ignora y se
    reinicia vacío (mismo principio que `momentum_hunter.watchlist.cargar`)."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    revisiones: list[RevisionIA] = []
    for d in data.get("revisiones", []):
        try:
            revisiones.append(RevisionIA(**d))
        except TypeError:
            continue
    return revisiones


def guardar(revisiones: list[RevisionIA], path: Path = PATH) -> None:
    data = {"revisiones": [asdict(r) for r in revisiones]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def ya_revisada(revisiones: list[RevisionIA], ticker: str, creado_en: str) -> bool:
    clave = _clave(ticker, creado_en)
    return any(_clave(r.ticker, r.creado_en) == clave for r in revisiones)
