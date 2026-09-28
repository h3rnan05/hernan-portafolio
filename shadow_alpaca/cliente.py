"""HTTP de solo lectura contra `data.alpaca.markets`.

El host está fijo a propósito, igual que el paper trader fija el suyo:
no hay argumento ni variable de entorno que lo cambie. Las claves van en
headers y no se registran. Un fallo se guarda por su código (`red`,
`auth`, `http_429`), nunca por el texto de la excepción: ese texto puede
traer una URL.
"""

from __future__ import annotations

import logging
import os
import time

import requests

from shadow_alpaca import DATA_BASE

log = logging.getLogger("shadow_alpaca.cliente")

ESPERA_MAX_S = 8.0


class ErrorDatos(Exception):
    """Fallo del feed de datos. `codigo` es una etiqueta corta."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


def armar_url(path: str) -> str:
    """Solo un path absoluto de este host. Una URL completa se rechaza:
    sería la forma de colar el host de trading."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ErrorDatos("ruta")
    if "://" in path or "alpaca.markets" in path:
        raise ErrorDatos("ruta")
    return DATA_BASE + path


class ClienteDatos:
    """GET paginable. `transport` existe para las pruebas; en producción
    es `requests.get`. Las claves se leen en cada pedido y no se guardan
    en el log."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout: float = 20.0,
        reintentos: int = 3,
        transport=None,
        dormir=time.sleep,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self.timeout = timeout
        self.reintentos = max(1, reintentos)
        self._transport = transport or requests.get
        self._dormir = dormir

    def _credenciales(self) -> tuple[str, str]:
        key = (self._api_key if self._api_key is not None
               else os.environ.get("ALPACA_PAPER_API_KEY", "")).strip()
        secret = (self._api_secret if self._api_secret is not None
                  else os.environ.get("ALPACA_PAPER_API_SECRET", "")).strip()
        if not key or not secret:
            raise ErrorDatos("sin_credenciales")
        return key, secret

    def _espera(self, intento: int, respuesta) -> float:
        headers = getattr(respuesta, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            ra = headers.get("Retry-After")
            if isinstance(ra, str) and ra.strip().isdigit():
                return min(ESPERA_MAX_S, float(ra.strip()))
        return min(ESPERA_MAX_S, 0.4 * (2 ** intento))

    def get(self, path: str, params: dict | None = None) -> dict:
        url = armar_url(path)
        key, secret = self._credenciales()
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        ultimo = "sin_respuesta"
        respuesta = None
        for intento in range(self.reintentos):
            if intento:
                self._dormir(self._espera(intento - 1, respuesta))
            try:
                respuesta = self._transport(
                    url, params=params or {}, headers=headers, timeout=self.timeout,
                )
            except requests.RequestException as ex:
                ultimo = "red"
                respuesta = None
                # Solo el tipo. El mensaje de requests a veces incluye la URL.
                log.warning("datos: fallo de red en %s (intento %d, %s)", path, intento + 1, type(ex).__name__)
                continue
            status = getattr(respuesta, "status_code", None)
            if status == 429 or (isinstance(status, int) and 500 <= status <= 599):
                ultimo = f"http_{status}"
                log.warning("datos: HTTP %s en %s (intento %d)", status, path, intento + 1)
                continue
            if status in (401, 403):
                raise ErrorDatos("auth")
            if isinstance(status, int) and status >= 400:
                raise ErrorDatos(f"http_{status}")
            try:
                cuerpo = respuesta.json()
            except ValueError:
                raise ErrorDatos("cuerpo") from None
            if not isinstance(cuerpo, dict):
                raise ErrorDatos("cuerpo")
            return cuerpo
        raise ErrorDatos(ultimo)

    def paginas(self, path: str, params: dict, tope: int) -> tuple[list[dict], bool]:
        """Todas las páginas, o las primeras `tope` si el token no se
        acaba. El segundo valor es True cuando se cortó por el tope: esa
        serie está incompleta y hay que decirlo, no tratarla como entera."""
        if tope < 1:
            raise ErrorDatos("paginacion")
        token = None
        out: list[dict] = []
        for _ in range(tope):
            q = dict(params)
            if token:
                q["page_token"] = token
            cuerpo = self.get(path, q)
            out.append(cuerpo)
            siguiente = cuerpo.get("next_page_token")
            if not isinstance(siguiente, str) or not siguiente:
                return out, False
            token = siguiente
        return out, True
