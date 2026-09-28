"""Metadata (nombre, bolsa, market cap, ETF) desde Finnhub, solo de DATOS.

Se activa con `MOMENTUM_METADATA_PROVIDER=finnhub` (default `yahoo`; ver
`data/fuente.py`). Antes de activarlo corre en SOMBRA cinco sesiones
(`momentum_hunter/sombra_metadata.py`) para comparar campo a campo con
Yahoo. Este módulo no decide nada ni escribe la watchlist.

Endpoints (plan gratis, 60 pedidos/min; se usan 30):
  - `/stock/profile2?symbol=X`: `name`, `exchange`, `marketCapitalization`
    (en MILLONES de USD), `shareOutstanding` (millones), `ticker`.
    Un símbolo desconocido responde `{}` (200): eso es "Finnhub no lo
    tiene", y la metadata queda vacía, no inventada.

LIMITACIÓN HONESTA. El plan gratis no trae el FLOAT ni el short interest.
`shares_float`, `short_pct_float` y `days_to_cover` quedan en None con
este proveedor. Con `float_faltante: excluir` en la v2, usarlo como
único proveedor de metadata excluiría a todo el universo: por eso la
sombra existe y la decisión de cambiar es del dueño, no de una variable.

La clave (`FINNHUB_API_KEY`) va en el parámetro `token`; nunca al log.
Un fallo se registra por tipo y código.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque

import requests

from momentum_hunter.data.provider import _EXCHANGE_MAP, _num, _parece_cef, _parece_spac
from momentum_hunter.models import Metadata

log = logging.getLogger("momentum_hunter.data.finnhub_metadata")

ENV_TOKEN = "FINNHUB_API_KEY"
BASE = "https://finnhub.io/api/v1"
LLAMADAS_POR_MINUTO = 30

# Finnhub escribe la bolsa en largo; el hunter usa NYSE | NASDAQ | AMEX.
_BOLSAS_FINNHUB = {
    "NASDAQ": "NASDAQ",
    "NEW YORK STOCK EXCHANGE": "NYSE",
    "NYSE": "NYSE",
    "NYSE MKT": "AMEX",
    "NYSE AMERICAN": "AMEX",
    "AMEX": "AMEX",
}


def bolsa_de(texto: object) -> str | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    t = texto.strip().upper()
    # El prefijo más largo primero: "NYSE MKT" antes que "NYSE".
    for clave, bolsa in sorted(_BOLSAS_FINNHUB.items(), key=lambda kv: -len(kv[0])):
        if t.startswith(clave):
            return bolsa
    return _EXCHANGE_MAP.get(t, None)


def metadata_de_perfil(ticker: str, perfil: dict) -> Metadata:
    """`{}` (símbolo desconocido) da una Metadata vacía; un market cap
    en millones se pasa a dólares; lo que Finnhub no trae queda None."""
    if not perfil:
        return Metadata(ticker)
    nombre = perfil.get("name") if isinstance(perfil.get("name"), str) else None
    cap_millones = _num(perfil.get("marketCapitalization"))
    tipo = perfil.get("type") if isinstance(perfil.get("type"), str) else None
    return Metadata(
        ticker=ticker,
        nombre=nombre,
        bolsa=bolsa_de(perfil.get("exchange")),
        es_etf=(tipo or "").upper() == "ETF",
        es_spac=_parece_spac(nombre),
        es_cef=_parece_cef(nombre, tipo),
        es_adr="ADR" in (nombre or "").upper(),
        market_cap=cap_millones * 1e6 if cap_millones is not None else None,
        shares_float=None,          # no disponible en el plan gratis -- ver docstring
        short_pct_float=None,
        days_to_cover=None,
        borrow_fee_pct=None,
    )


class FinnhubMetadata:
    """Solo `metadata`. Las barras siguen saliendo del proveedor de
    precios configurado (`data/fuente.py` los combina)."""

    def __init__(self, token: str | None = None, transport=None, timeout: float = 10.0,
                 reloj=time.monotonic, dormir=time.sleep) -> None:
        self._token = token
        self._transport = transport or requests.get
        self.timeout = timeout
        self._reloj = reloj
        self._dormir = dormir
        self._marcas: deque[float] = deque()
        self.fallidos: list[str] = []
        self.ultimo_codigo: str | None = None

    def _clave(self) -> str | None:
        tok = (self._token if self._token is not None else os.environ.get(ENV_TOKEN, "")).strip()
        return tok or None

    def _esperar(self) -> None:
        ahora = self._reloj()
        while self._marcas and ahora - self._marcas[0] >= 60.0:
            self._marcas.popleft()
        if len(self._marcas) >= LLAMADAS_POR_MINUTO:
            self._dormir(60.0 - (ahora - self._marcas[0]))
            ahora = self._reloj()
        self._marcas.append(ahora)

    def perfil(self, ticker: str) -> dict | None:
        """El JSON de profile2, `{}` si Finnhub no conoce el símbolo, None
        si el pedido falló (sin clave, red, HTTP, cuerpo)."""
        tok = self._clave()
        if tok is None:
            self.ultimo_codigo = "sin_credenciales"
            return None
        self._esperar()
        try:
            r = self._transport(f"{BASE}/stock/profile2", params={"symbol": ticker, "token": tok},
                                timeout=self.timeout)
        except requests.RequestException as ex:
            self.ultimo_codigo = "red"
            log.warning("finnhub metadata %s: fallo de red (%s)", ticker, type(ex).__name__)
            return None
        status = getattr(r, "status_code", None)
        if status != 200:
            self.ultimo_codigo = f"http_{status}"
            log.warning("finnhub metadata %s: HTTP %s", ticker, status)
            return None
        try:
            cuerpo = r.json()
        except ValueError:
            self.ultimo_codigo = "cuerpo"
            return None
        if not isinstance(cuerpo, dict):
            self.ultimo_codigo = "cuerpo"
            return None
        return cuerpo

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        out: dict[str, Metadata] = {}
        self.fallidos = []
        for t in tickers:
            perfil = self.perfil(t)
            if perfil is None:
                self.fallidos.append(t)
                out[t] = Metadata(t)
                continue
            out[t] = metadata_de_perfil(t, perfil)
        return out
