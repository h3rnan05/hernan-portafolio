"""Descarga el catálogo de activos del host PAPER y lo deja en un JSON local.

POR QUÉ ES OTRO PROCESO. El hunter no puede llamar a un endpoint de
trading: su frontera es no incorporar bróker ni ejecución. Este job es
el único que habla con `GET /v2/assets`. Escribe un archivo; el hunter
solo lo lee. Si este proceso no corrió, el hunter no adivina: trata el
dato como desconocido y sigue como hasta hoy.

HOST. Se usa `alpaca_client._BASE_URL`, que ya está fijo en
`https://paper-api.alpaca.markets/v2`. No hay variable ni argumento
para cambiarlo: el mismo candado que el resto del paper trader.

FALLO. Un HTTP que no sea 200, un cuerpo que no se entiende, una
paginación que no termina o una lista vacía NO tocan el archivo
anterior. Una foto a medias haría que el hunter diera por no listados
símbolos que sí lo están. La escritura, cuando sale bien, es atómica
(`os.replace` en el mismo directorio): quien lee ve el archivo viejo
o el nuevo entero, nunca un JSON cortado.

QUÉ SE GUARDA. symbol, exchange, tradable, fractionable, status, name
y `fecha_generacion` del archivo. shortable, marginable y
easy_to_borrow no entran: no deciden nada en este bot. Un booleano que
no vino como bool queda en null; no se convierte en false.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import requests

from momentum_hunter.catalogo_activos import ruta_catalogo
from momentum_paper_trader.alpaca_client import _BASE_URL

log = logging.getLogger("momentum_paper_trader.assets_job")

# Mismo host que el cliente de órdenes. `_BASE_URL` ya incluye `/v2`.
_URL_ASSETS = f"{_BASE_URL}/assets"
_PARAMS = {"status": "active", "asset_class": "us_equity"}
_TIMEOUT_S = 60.0
# El endpoint de trading hoy devuelve la lista entera. Si un día viene
# `next_page_token`, se sigue. El tope corta un token que se repite:
# preferimos no escribir a publicar un catálogo truncado.
_MAX_PAGINAS = 30

class ErrorCatalogo(Exception):
    """Fallo del job. `codigo` es una etiqueta corta: el texto de la
    excepción de red puede traer una URL y no se registra."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


def ruta_por_defecto() -> Path:
    """La misma función que usa el hunter para leer. Un solo camino
    para que el job no escriba en un archivo que nadie mira."""
    return ruta_catalogo()


def _texto(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    return texto or None


def _bool(valor: object) -> bool | None:
    """Solo un bool de JSON. El string `"false"` es verdadero en
    Python si se hace `bool(s)`; eso inventaría un tradable."""
    if isinstance(valor, bool):
        return valor
    return None


def reducir_activo(crudo: object) -> dict | None:
    """Una fila del archivo, o None si no hay símbolo con el que
    indexar. Los campos que no vinieron quedan en null."""
    if not isinstance(crudo, dict):
        return None
    symbol = _texto(crudo.get("symbol"))
    if symbol is None:
        return None
    return {
        "symbol": symbol.upper(),
        "exchange": _texto(crudo.get("exchange")),
        "tradable": _bool(crudo.get("tradable")),
        "fractionable": _bool(crudo.get("fractionable")),
        "status": _texto(crudo.get("status")),
        "name": _texto(crudo.get("name")),
    }


def _token(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    return texto or None


def extraer_pagina(cuerpo: object) -> tuple[list, str | None]:
    """`(filas, next_page_token)`. Una lista es la respuesta de hoy:
    una sola página, sin token. Un objeto con lista y token es la
    forma paginada, por si el host la empieza a usar."""
    if isinstance(cuerpo, list):
        return cuerpo, None
    if isinstance(cuerpo, dict):
        filas = cuerpo.get("assets")
        if isinstance(filas, list):
            return filas, _token(cuerpo.get("next_page_token"))
    raise ErrorCatalogo("cuerpo")


def _credenciales() -> tuple[str, str]:
    key = os.environ.get("ALPACA_PAPER_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_PAPER_API_SECRET", "").strip()
    if not key or not secret:
        raise ErrorCatalogo("sin_credenciales")
    return key, secret


def _leer_pagina(params: dict, headers: dict) -> object:
    try:
        respuesta = requests.get(
            _URL_ASSETS, params=params, headers=headers, timeout=_TIMEOUT_S,
        )
    except requests.RequestException as ex:
        log.error("catálogo de activos: red (%s)", type(ex).__name__)
        raise ErrorCatalogo("red") from None
    if respuesta.status_code != 200:
        log.error("catálogo de activos: http_%s", respuesta.status_code)
        raise ErrorCatalogo(f"http_{respuesta.status_code}")
    try:
        return respuesta.json()
    except ValueError:
        raise ErrorCatalogo("cuerpo") from None


def descargar() -> list[dict]:
    """Todas las páginas, o lanza `ErrorCatalogo` sin haber escrito nada."""
    key, secret = _credenciales()
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    params = dict(_PARAMS)
    vistos: set[str] = set()
    crudos: list = []
    for _ in range(_MAX_PAGINAS):
        cuerpo = _leer_pagina(params, headers)
        filas, token = extraer_pagina(cuerpo)
        crudos.extend(filas)
        if token is None:
            break
        if token in vistos:
            raise ErrorCatalogo("paginacion")
        vistos.add(token)
        params = dict(_PARAMS)
        params["page_token"] = token
    else:
        raise ErrorCatalogo("paginacion")

    activos: list[dict] = []
    sin_simbolo = 0
    for crudo in crudos:
        fila = reducir_activo(crudo)
        if fila is None:
            sin_simbolo += 1
            continue
        activos.append(fila)
    if sin_simbolo:
        log.info("catálogo de activos: %d fila(s) sin símbolo, no entran", sin_simbolo)
    if not activos:
        raise ErrorCatalogo("vacio")
    # La última aparición gana si el host repite un símbolo entre páginas.
    por_symbol: dict[str, dict] = {}
    for fila in activos:
        por_symbol[fila["symbol"]] = fila
    return [por_symbol[s] for s in sorted(por_symbol)]


def escribir_atomico(path: Path, payload: dict) -> None:
    """Reemplaza `path` de un golpe. Si algo falla antes del `replace`,
    el archivo anterior sigue igual y no queda un temporal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".alpaca_assets.", suffix=".tmp", dir=path.parent,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def correr(salida: Path | None = None, ahora: datetime | None = None) -> int:
    """Descarga y escribe. Devuelve cuántos símbolos quedaron. Si falla,
    no reemplaza `salida`."""
    destino = salida if salida is not None else ruta_por_defecto()
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ErrorCatalogo("reloj")
    activos = descargar()
    payload = {
        "fecha_generacion": momento.astimezone(UTC).isoformat(timespec="seconds"),
        "assets": activos,
    }
    try:
        escribir_atomico(destino, payload)
    except OSError as ex:
        log.error("catálogo de activos: no se pudo escribir (%s)", type(ex).__name__)
        raise ErrorCatalogo("escritura") from None
    log.info("catálogo de activos escrito: %d símbolos en %s", len(activos), destino.name)
    return len(activos)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Catálogo de activos del host paper (solo escritura local)")
    parser.add_argument(
        "--salida", type=Path, default=None,
        help="ruta del JSON (por omisión, momentum_hunter/datos/alpaca_assets.json)",
    )
    args = parser.parse_args(argv)
    try:
        n = correr(salida=args.salida)
    except ErrorCatalogo as ex:
        log.error("catálogo de activos: no se escribió (%s)", ex.codigo)
        return 1
    log.info("listo (%d)", n)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
