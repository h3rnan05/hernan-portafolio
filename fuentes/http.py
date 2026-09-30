"""GET con reintentos, límite de tasa y errores por código.

Cada fuente arma su `Cliente` con su `Limitador` (SEC: 10/s; Nasdaq
Trader: 1/min; Finnhub: 60/min en el plan gratis). El transporte se
inyecta en las pruebas; en producción es `requests.get`. Un fallo se
registra por su TIPO y código, nunca por su texto.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable

import requests

from fuentes.comun import ErrorFuente

log = logging.getLogger("fuentes.http")

ESPERA_MAX_S = 30.0


class Limitador:
    """Como máximo `llamadas` en cualquier ventana de `segundos`.
    `reloj` y `dormir` se inyectan en las pruebas."""

    def __init__(self, llamadas: int, segundos: float, reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], None] = time.sleep) -> None:
        if llamadas < 1 or segundos <= 0:
            raise ValueError("limitador")
        self.llamadas = llamadas
        self.segundos = segundos
        self._reloj = reloj
        self._dormir = dormir
        self._marcas: deque[float] = deque()

    def esperar(self) -> float:
        """Duerme lo necesario y devuelve cuánto durmió."""
        ahora = self._reloj()
        while self._marcas and ahora - self._marcas[0] >= self.segundos:
            self._marcas.popleft()
        dormido = 0.0
        if len(self._marcas) >= self.llamadas:
            dormido = self.segundos - (ahora - self._marcas[0])
            if dormido > 0:
                self._dormir(dormido)
            ahora = self._reloj()
            while self._marcas and ahora - self._marcas[0] >= self.segundos:
                self._marcas.popleft()
        self._marcas.append(ahora)
        return max(dormido, 0.0)


class Respuesta:
    def __init__(self, status: int, headers: dict, texto: str, url: str, contenido: bytes | None = None) -> None:
        self.status = status
        self.headers = headers
        self.texto = texto
        self.url = url
        # Bytes crudos cuando el transporte los da: `requests` decodifica
        # `text` como ISO-8859-1 si el Content-Type no trae charset, y un
        # BOM UTF-8 queda como "ï»¿" (Nasdaq Trader, 2026-09-28).
        self.contenido = contenido

    def texto_utf8(self) -> str:
        """El cuerpo como UTF-8 sin BOM, decodificando los bytes si están."""
        if isinstance(self.contenido, bytes):
            return self.contenido.decode("utf-8-sig", errors="replace")
        t = self.texto.lstrip("\ufeff")
        return t[3:] if t.startswith("ï»¿") else t


class Cliente:
    def __init__(self, fuente: str, user_agent: str, limitador: Limitador | None = None,
                 timeout: float = 20.0, reintentos: int = 3, transport=None,
                 dormir: Callable[[float], None] = time.sleep, headers: dict | None = None,
                 transport_post=None) -> None:
        if not user_agent or not user_agent.strip():
            raise ErrorFuente("sin_user_agent", fuente)
        self.fuente = fuente
        self.user_agent = user_agent.strip()
        self.limitador = limitador
        self.timeout = timeout
        self.reintentos = max(1, reintentos)
        self._transport = transport or requests.get
        self._transport_post = transport_post or requests.post
        self._dormir = dormir
        self._headers = dict(headers or {})
        self.llamadas = 0

    def _espera(self, intento: int, respuesta) -> float:
        headers = getattr(respuesta, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            ra = headers.get("Retry-After")
            if isinstance(ra, str) and ra.strip().isdigit():
                return min(ESPERA_MAX_S, float(ra.strip()))
        return min(ESPERA_MAX_S, 0.5 * (2 ** intento))

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> Respuesta:
        return self._pedir("GET", url, params, headers, None)

    def post(self, url: str, cuerpo: dict, headers: dict | None = None) -> Respuesta:
        """POST con cuerpo JSON (FINRA filtra así). Mismos reintentos y
        códigos que `get`."""
        return self._pedir("POST", url, None, headers, cuerpo)

    def _pedir(self, metodo: str, url: str, params: dict | None, headers: dict | None, cuerpo: dict | None) -> Respuesta:
        h = {"User-Agent": self.user_agent, **self._headers, **(headers or {})}
        ultimo = "sin_respuesta"
        respuesta = None
        for intento in range(self.reintentos):
            if intento:
                self._dormir(self._espera(intento - 1, respuesta))
            if self.limitador is not None:
                self.limitador.esperar()
            self.llamadas += 1
            try:
                if metodo == "POST":
                    respuesta = self._transport_post(url, json=cuerpo, headers=h, timeout=self.timeout)
                else:
                    respuesta = self._transport(url, params=params or {}, headers=h, timeout=self.timeout)
            except requests.RequestException as ex:
                ultimo, respuesta = "red", None
                log.warning("%s: fallo de red (intento %d, %s)", self.fuente, intento + 1, type(ex).__name__)
                continue
            status = getattr(respuesta, "status_code", None)
            if status == 429 or (isinstance(status, int) and 500 <= status <= 599):
                ultimo = f"http_{status}"
                log.warning("%s: HTTP %s (intento %d)", self.fuente, status, intento + 1)
                continue
            if status in (401, 403):
                raise ErrorFuente("auth", self.fuente)
            if isinstance(status, int) and status >= 400:
                raise ErrorFuente(f"http_{status}", self.fuente)
            if not isinstance(status, int):
                raise ErrorFuente("sin_status", self.fuente)
            texto = getattr(respuesta, "text", None)
            if not isinstance(texto, str):
                raise ErrorFuente("cuerpo", self.fuente)
            hdrs = getattr(respuesta, "headers", None) or {}
            contenido = getattr(respuesta, "content", None)
            return Respuesta(status, dict(hdrs) if hasattr(hdrs, "items") else {}, texto, url,
                             contenido if isinstance(contenido, bytes) else None)
        raise ErrorFuente(ultimo, self.fuente)

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        import json
        r = self.get(url, params, headers)
        try:
            return json.loads(r.texto)
        except ValueError:
            raise ErrorFuente("cuerpo", self.fuente) from None
