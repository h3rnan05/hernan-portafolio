"""Persistencia de qué entradas TRIGGERED ya generaron una orden paper --
mismo principio que el resto del repo (JSON chico, committeado por el
workflow, ver `momentum_hunter/heartbeat.py`/`tracker.py`).

Sin esto, cada corrida re-leería la misma entrada TRIGGERED en
`watchlist.json` (que queda ahí, terminal, varios días -- ver
`watchlist.RETENCION_DIAS_TERMINALES`) y colocaría una orden nueva cada
vez. La clave es (ticker, `creado_en`) -- `creado_en` identifica la
entrada ÚNICA de la watchlist, no solo el ticker, así que el mismo
ticker disparando en dos días distintos genera dos órdenes distintas
correctamente."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

PATH = Path(__file__).resolve().parent / "ordenes.json"


@dataclass
class OrdenRegistrada:
    ticker: str
    creado_en: str   # `EntradaWatchlist.creado_en` -- identifica la entrada única
    order_id: str
    cantidad: int
    precio_entrada: float
    stop: float
    objetivo: float
    timestamp: str


def _clave(ticker: str, creado_en: str) -> str:
    return f"{ticker}|{creado_en}"


def cargar(path: Path = PATH) -> list[OrdenRegistrada]:
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
    ordenes: list[OrdenRegistrada] = []
    for d in data.get("ordenes", []):
        try:
            ordenes.append(OrdenRegistrada(**d))
        except TypeError:
            continue
    return ordenes


def guardar(ordenes: list[OrdenRegistrada], path: Path = PATH) -> None:
    data = {"ordenes": [asdict(o) for o in ordenes]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def ya_procesada(ordenes: list[OrdenRegistrada], ticker: str, creado_en: str) -> bool:
    clave = _clave(ticker, creado_en)
    return any(_clave(o.ticker, o.creado_en) == clave for o in ordenes)
