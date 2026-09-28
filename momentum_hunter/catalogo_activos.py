"""Lectura del catálogo local de activos. Este módulo no llama a ningún
endpoint: el archivo lo escribe otro proceso, y ese proceso no se
importa desde acá.

CUÁNDO SIRVE. Si `fecha_generacion` es de hoy (UTC) y tiene 36 h o
menos, el archivo es usable. Si falta, no se puede leer, está vacío,
tiene más de 36 h o su fecha no es la de hoy, el dato es desconocido:
`tradable` y el exchange quedan en None (nunca False ni 0) y el
escaneo no filtra por esto. Un campo ausente dentro de una fila
fresca tampoco se convierte en False: solo un `tradable: false`
explícito saca al símbolo.

PARA QUÉ. (1) Saber si el símbolo está en la lista de us_equity
activos, si es tradable y en qué exchange. (2) Pedir al feed la forma
que el archivo realmente trae cuando la clase viene con guion en el
universo y con punto en el catálogo (`BRK-B` / `BRK.B`, la misma
traducción que ya usa el feed). La clave que ve el resto del pipeline
sigue siendo el ticker pedido.

NO SE RELLENA LA VENTANA. Los que salen se cuentan y no se evalúan.
No se traen otros del siguiente tramo de la rotación: eso cambiaría
qué símbolos tradables mira cada slot, y este archivo no está para eso.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

EDAD_MAXIMA = timedelta(hours=36)

_NOMBRE = "alpaca_assets.json"


def ruta_catalogo() -> Path:
    """`MOMENTUM_CATALOGO_ACTIVOS` si está puesta; si no, el JSON
    junto a este paquete. El job escribe en la misma ruta."""
    env = os.environ.get("MOMENTUM_CATALOGO_ACTIVOS", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "datos" / _NOMBRE


def _texto(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    return texto or None


def _bool(valor: object) -> bool | None:
    if isinstance(valor, bool):
        return valor
    return None


def _fecha(valor: object) -> datetime | None:
    """ISO con zona, en UTC. Sin zona no se asume UTC: un archivo
    ambiguo no autoriza a filtrar el universo."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        dt = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class _Fila:
    symbol: str
    exchange: str | None
    tradable: bool | None
    fractionable: bool | None
    status: str | None
    name: str | None


@dataclass(frozen=True)
class _Snapshot:
    fecha: datetime | None
    filas: dict[str, _Fila]
    legible: bool


@dataclass(frozen=True)
class Ficha:
    """Lo que se sabe de un símbolo. Con el catálogo frío, todo es None
    y `catalogo_fresco` es False: no hay un False escondido en `tradable`."""

    catalogo_fresco: bool
    en_catalogo: bool
    symbol: str | None
    exchange: str | None
    tradable: bool | None
    fractionable: bool | None
    status: str | None
    name: str | None


@dataclass(frozen=True)
class InformeCatalogo:
    desconocido: bool
    tickers: list[str]
    descartados: int | None
    motivos: dict[str, int] | None


@dataclass(frozen=True)
class VeredictoSimbolo:
    """`catalogo_fresco` False: el caller usa la traducción de siempre.
    True y `simbolo_feed` None: el archivo no tiene ese símbolo (ni su
    forma de clase) y no se manda una adivinanza al feed.
    True y un símbolo: esa es la forma que hay que pedir."""

    catalogo_fresco: bool
    simbolo_feed: str | None


_VACIA = _Snapshot(fecha=None, filas={}, legible=False)
_cache: dict[tuple, _Snapshot] = {}


def _firma(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), st.st_mtime_ns, st.st_size)


def _fila_de(crudo: object) -> _Fila | None:
    if not isinstance(crudo, dict):
        return None
    symbol = _texto(crudo.get("symbol"))
    if symbol is None:
        return None
    return _Fila(
        symbol=symbol.upper(),
        exchange=_texto(crudo.get("exchange")),
        tradable=_bool(crudo.get("tradable")),
        fractionable=_bool(crudo.get("fractionable")),
        status=_texto(crudo.get("status")),
        name=_texto(crudo.get("name")),
    )


def _leer(path: Path) -> _Snapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _VACIA
    if not isinstance(data, dict):
        return _VACIA
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        return _Snapshot(fecha=_fecha(data.get("fecha_generacion")), filas={}, legible=False)
    filas: dict[str, _Fila] = {}
    for crudo in assets:
        fila = _fila_de(crudo)
        if fila is None:
            continue
        filas[fila.symbol] = fila
    if not filas:
        return _Snapshot(fecha=_fecha(data.get("fecha_generacion")), filas={}, legible=False)
    return _Snapshot(fecha=_fecha(data.get("fecha_generacion")), filas=filas, legible=True)


def cargar(path: Path | None = None) -> _Snapshot:
    ruta = path if path is not None else ruta_catalogo()
    firma = _firma(ruta)
    hit = _cache.get(firma)
    if hit is not None:
        return hit
    snap = _leer(ruta)
    _cache.clear()
    _cache[firma] = snap
    return snap


def _ahora_utc(ahora: datetime | None) -> datetime | None:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        return None
    return momento.astimezone(UTC)


def esta_fresco(snap: _Snapshot, ahora: datetime | None = None) -> bool:
    """Hoy en UTC y no más viejo que `EDAD_MAXIMA`. Un archivo de ayer
    con menos de 36 h igual no cubre hoy: el job es diario y la sesión
    no debe operar con la foto del día anterior."""
    if not snap.legible or snap.fecha is None or not snap.filas:
        return False
    momento = _ahora_utc(ahora)
    if momento is None:
        return False
    if momento - snap.fecha > EDAD_MAXIMA:
        return False
    if snap.fecha.date() != momento.date():
        return False
    return True


def _claves(ticker: str) -> list[str]:
    """Formas con las que buscar. La traducción de clase es la de
    `_simbolo_para_query` (guion de Yahoo → punto del feed) más el
    camino inverso de una sola letra (`BRK.B` → `BRK-B`), para que
    dé igual cuál de las dos traiga el universo."""
    from momentum_hunter.data.alpaca_datos import _simbolo_para_query

    t = ticker.strip().upper()
    if not t:
        return []
    claves = [t]
    traducida = _simbolo_para_query(t)
    if traducida:
        forma = traducida.upper()
        if forma not in claves:
            claves.append(forma)
    if "." in t:
        base, _, sufijo = t.partition(".")
        if (
            base
            and len(sufijo) == 1
            and sufijo.isalpha()
            and "-" not in base
            and "." not in base
        ):
            inversa = f"{base}-{sufijo}"
            if inversa not in claves:
                claves.append(inversa)
    return claves


def _buscar(snap: _Snapshot, ticker: str) -> _Fila | None:
    if not isinstance(ticker, str):
        return None
    for clave in _claves(ticker):
        fila = snap.filas.get(clave)
        if fila is not None:
            return fila
    return None


def _ficha_fria() -> Ficha:
    return Ficha(False, False, None, None, None, None, None, None)


def consultar(ticker: str, *, path: Path | None = None, ahora: datetime | None = None) -> Ficha:
    snap = cargar(path)
    if not esta_fresco(snap, ahora):
        return _ficha_fria()
    fila = _buscar(snap, ticker)
    if fila is None:
        return Ficha(True, False, None, None, None, None, None, None)
    return Ficha(
        True, True, fila.symbol, fila.exchange, fila.tradable,
        fila.fractionable, fila.status, fila.name,
    )


def _motivo_descarte(fila: _Fila | None) -> str | None:
    """None = se queda. Un tradable ausente no descarta: no es False.
    No estar en el archivo sí descarta, porque el archivo ES la lista
    de us_equity activos que el job pidió."""
    if fila is None:
        return "no_listado"
    if fila.tradable is False:
        return "no_tradable"
    if fila.status is not None and fila.status.lower() != "active":
        return "status"
    return None


def filtrar_por_catalogo(
    tickers: list[str], *, path: Path | None = None, ahora: datetime | None = None,
) -> InformeCatalogo:
    """Con el catálogo frío devuelve la misma lista y `descartados`
    en None. Con el catálogo fresco saca no tradable, status distinto
    de active, y los que no están en el archivo."""
    snap = cargar(path)
    if not esta_fresco(snap, ahora):
        return InformeCatalogo(True, list(tickers), None, None)
    quedan: list[str] = []
    motivos: dict[str, int] = {}
    for t in tickers:
        if not isinstance(t, str) or not t.strip():
            quedan.append(t)
            continue
        motivo = _motivo_descarte(_buscar(snap, t))
        if motivo is None:
            quedan.append(t)
            continue
        motivos[motivo] = motivos.get(motivo, 0) + 1
    return InformeCatalogo(False, quedan, sum(motivos.values()), motivos)


def resolver_simbolo(
    ticker: str, *, path: Path | None = None, ahora: datetime | None = None,
) -> VeredictoSimbolo:
    snap = cargar(path)
    if not esta_fresco(snap, ahora):
        return VeredictoSimbolo(False, None)
    fila = _buscar(snap, ticker)
    if fila is None or not fila.symbol:
        return VeredictoSimbolo(True, None)
    return VeredictoSimbolo(True, fila.symbol)


def anotar_exchange(meta, *, path: Path | None = None, ahora: datetime | None = None) -> None:
    """Si el archivo está fresco y trae exchange, esa es la bolsa de la
    metadata. Un nombre que ya vino del proveedor de precios se queda:
    el archivo solo completa el nombre cuando no había ninguno."""
    snap = cargar(path)
    if not esta_fresco(snap, ahora):
        return
    fila = _buscar(snap, getattr(meta, "ticker", ""))
    if fila is None:
        return
    if fila.exchange:
        meta.bolsa = fila.exchange
    if getattr(meta, "nombre", None) is None and fila.name:
        meta.nombre = fila.name
