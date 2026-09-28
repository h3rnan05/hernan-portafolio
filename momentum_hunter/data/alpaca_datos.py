"""Precios y volumen del feed de datos de Alpaca (REST, solo lectura).

POR QUÉ EXISTE. El hunter y el vigía leían todo de Yahoo. El dueño
contrató el feed SIP y el 2026-09-28 se comprobó que las mismas claves
paper (`ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_API_SECRET`) reciben HTTP
200 de `https://data.alpaca.markets` con la vela de 1 minuto a ~1 minuto
de retraso. Esta clase es la otra implementación de `DataProvider` que
el docstring de `provider.py` dejaba prevista. No coloca órdenes: el
host de trading paper sigue siendo otro módulo y otro host, a propósito,
para que un error acá no pueda apuntar a la cuenta.

QUÉ NO TRAE ESTE FEED. Float, nombre, ETF/SPAC y short interest no están
en las barras. `metadata` sigue delegando en Yahoo: si no, el filtro de
universo cambiaría el día que se encienda el feed, y eso no es un cambio
de fuente de precio. Noticias y catalizadores ni se tocan.

AJUSTE. `adjustment=split`: precio y volumen ajustados por split, no por
dividendo. Es lo más cerca del `quote` del chart de Yahoo (el `adjclose`
de Yahoo sí descuenta dividendos; el hunter no lo usa). `raw` dejaría
un split viejo como si fuera un crash y movería el ATR.

VELA EN FORMACIÓN. Se reutiliza `_velas_finales_en_formacion`: un 0 de
volumen al final no es "no entró dinero", es el minuto que todavía no
cerró. Un campo ausente se descarta entero; nunca se convierte en cero.

PRE/POST. Las velas de minuto de este endpoint incluyen la sesión
extendida dentro de la ventana pedida (4:00–20:00 ET). La vela diaria
es solo la sesión regular, igual que el chart diario de Yahoo. Si un
símbolo vuelve sin premarket, `maximo_premarket` queda en None y el
patrón que lo necesita no dispara: no se inventa un máximo.

HISTORIA DIARIA. Con `dias <= 365` se piden 365 días de calendario (el
`range=1y` de Yahoo), no 280: el máximo de 52 semanas mira ~252 sesiones.
Acortar esa ventana cambiaría un factor sin que nadie lo hubiera pedido.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import requests

from momentum_hunter.data.provider import DataProvider, _velas_finales_en_formacion
from momentum_hunter.models import Barras, BarraIntradia, Metadata

log = logging.getLogger("momentum_hunter.data.alpaca")

DATA_BASE = "https://data.alpaca.markets"
# Split, no dividendo: ver el docstring del módulo.
AJUSTE = "split"
FEEDS_VALIDOS = ("sip", "iex")
LIMITE_PAGINA = 10_000
# Tope para no seguir un `next_page_token` que no termina. Por debajo de
# esto cabe 1 año diario de un lote y 5 días de minuto de un lote chico.
# Si se agota, el lote se trata como fallo y cae al respaldo: una serie
# truncada movería el máximo de 52 semanas y el volumen promedio.
MAX_PAGINAS = 40
LOTE_DIARIO = 100
LOTE_INTRADIA = 15
ESPERA_MAX_S = 8.0

_TIMEFRAMES = {
    "1m": "1Min",
    "1min": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}


class ErrorDatosAlpaca(Exception):
    """Fallo de un lote o del ciclo. `codigo` es una etiqueta corta, nunca
    el cuerpo de la respuesta ni una URL (ahí no van las claves, van en
    headers, pero el cuerpo tampoco se registra)."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


def _momento(valor: object) -> datetime | None:
    """Timestamp del feed a UTC. Sin zona no se asume UTC: un dato
    ambiguo no es una vela."""
    if not isinstance(valor, str) or not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def _numero(valor: object) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        n = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def parsear_barra(cruda: object) -> tuple[datetime, float, float, float, float, float] | None:
    """Una vela del feed, o None si falta OHLC, volumen o el tiempo.
    El volumen ausente no se reemplaza por 0."""
    if not isinstance(cruda, dict):
        return None
    momento = _momento(cruda.get("t"))
    o, h, lo, c, v = (
        _numero(cruda.get("o")), _numero(cruda.get("h")), _numero(cruda.get("l")),
        _numero(cruda.get("c")), _numero(cruda.get("v")),
    )
    if momento is None or None in (o, h, lo, c, v):
        return None
    return momento, o, h, lo, c, v


def _listas(velas: list[tuple[datetime, float, float, float, float, float]], intradia: bool):
    """Orden cronológico, sin timestamps repetidos (la paginación no
    debería repetir, pero una vela duplicada contaría dos veces en el
    VWAP). Recorta el 0 final igual que Yahoo."""
    ordenadas = sorted(velas, key=lambda v: v[0])
    vistas: set[datetime] = set()
    marcas, o, h, lo, c, vol = [], [], [], [], [], []
    for momento, op, hi, low, cl, v in ordenadas:
        if momento in vistas:
            continue
        vistas.add(momento)
        if intradia:
            marcas.append(momento.isoformat(timespec="seconds"))
        else:
            # Mismo contrato que Yahoo: epoch en segundos, como texto.
            # `run._cierre_anterior` y `outcomes` hacen int(...) y lo
            # leen en UTC. Un ISO acá rompería el gap y el seguimiento.
            marcas.append(str(int(momento.timestamp())))
        o.append(op)
        h.append(hi)
        lo.append(low)
        c.append(cl)
        vol.append(v)
    recortar = _velas_finales_en_formacion(vol)
    if recortar:
        marcas, o, h, lo, c, vol = (
            marcas[:-recortar], o[:-recortar], h[:-recortar], lo[:-recortar],
            c[:-recortar], vol[:-recortar],
        )
    return marcas, o, h, lo, c, vol


def dias_de_periodo(periodo: str) -> int:
    """`5d` / `1d` como los pide el hunter. Otro texto no se adivina."""
    texto = (periodo or "").strip().lower()
    if texto.endswith("d") and texto[:-1].isdigit():
        n = int(texto[:-1])
        if n >= 1:
            return n
    if texto.endswith("mo") and texto[:-2].isdigit():
        n = int(texto[:-2])
        if n >= 1:
            return n * 30
    raise ErrorDatosAlpaca("periodo")


def timeframe_de(intervalo: str) -> str:
    tf = _TIMEFRAMES.get((intervalo or "").strip().lower())
    if tf is None:
        raise ErrorDatosAlpaca("intervalo")
    return tf


def _lotes(tickers: list[str], n: int) -> list[list[tuple[str, str]]]:
    """(símbolo para el query, ticker tal como lo pidió el caller).
    La clave del dict de salida tiene que coincidir con lo pedido: el
    resto del pipeline busca `barras[ticker]` con esa cadena."""
    vistos: set[str] = set()
    pares: list[tuple[str, str]] = []
    for t in tickers:
        if not isinstance(t, str):
            continue
        pedido = t.strip()
        if not pedido:
            continue
        clave = pedido.upper()
        if clave in vistos:
            continue
        vistos.add(clave)
        pares.append((clave, pedido))
    return [pares[i:i + n] for i in range(0, len(pares), n)]


def parsear_snapshot(crudo: object) -> dict:
    """Último trade y vela de minuto, si vienen. Un campo ausente queda
    en None: el script de comparación lo usa para ver el atraso, el
    hunter no opera con esto."""
    out: dict = {"precio": None, "minuto": None, "volumen_minuto": None}
    if not isinstance(crudo, dict):
        return out
    trade = crudo.get("latestTrade")
    if isinstance(trade, dict):
        out["precio"] = _numero(trade.get("p"))
    minuto = crudo.get("minuteBar")
    if isinstance(minuto, dict):
        momento = _momento(minuto.get("t"))
        if momento is not None:
            out["minuto"] = momento.isoformat(timespec="seconds")
        out["volumen_minuto"] = _numero(minuto.get("v"))
    return out


class AlpacaProvider(DataProvider):
    """`DataProvider` de barras contra `data.alpaca.markets`. Las claves
    se leen del entorno en cada pedido (o se inyectan en pruebas); no se
    guardan en logs. `fallidos` es la lista de tickers de los lotes que
    no respondieron en la última llamada: el respaldo la usa. Un símbolo
    que el feed simplemente no trae no entra ahí (no es un error)."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        feed: str = "sip",
        timeout: float = 20.0,
        reintentos: int = 3,
        pausa: float = 0.0,
        dormir=time.sleep,
        ahora=None,
    ) -> None:
        if feed not in FEEDS_VALIDOS:
            raise ErrorDatosAlpaca("feed")
        self._api_key = api_key
        self._api_secret = api_secret
        self.feed = feed
        self.timeout = timeout
        self.reintentos = max(1, reintentos)
        self.pausa = pausa
        self._dormir = dormir
        self._ahora = ahora or (lambda: datetime.now(UTC))
        self.fallidos: list[str] = []
        # Yahoo solo para metadata (float / ETF / nombre). No es el
        # respaldo de precios: ese lo pone `fuente.proveedor_configurado`.
        self._meta = None

    def _credenciales(self) -> tuple[str, str]:
        import os
        key = (self._api_key if self._api_key is not None
               else os.environ.get("ALPACA_PAPER_API_KEY", "")).strip()
        secret = (self._api_secret if self._api_secret is not None
                  else os.environ.get("ALPACA_PAPER_API_SECRET", "")).strip()
        if not key or not secret:
            raise ErrorDatosAlpaca("sin_credenciales")
        return key, secret

    def _espera(self, intento: int, respuesta) -> float:
        headers = getattr(respuesta, "headers", None)
        if headers is not None:
            ra = headers.get("Retry-After") if hasattr(headers, "get") else None
            if isinstance(ra, str) and ra.strip().isdigit():
                return min(ESPERA_MAX_S, float(ra.strip()))
        return min(ESPERA_MAX_S, 0.4 * (2 ** intento))

    def _get(self, path: str, params: dict) -> dict:
        key, secret = self._credenciales()
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        ultimo = None
        respuesta = None
        for intento in range(self.reintentos):
            if intento:
                self._dormir(self._espera(intento - 1, respuesta))
            try:
                respuesta = requests.get(
                    f"{DATA_BASE}{path}", params=params, headers=headers, timeout=self.timeout,
                )
            except requests.RequestException:
                ultimo = "red"
                respuesta = None
                log.warning("datos: fallo de red en %s (intento %d, %s)", path, intento + 1, ultimo)
                continue
            status = getattr(respuesta, "status_code", None)
            if status == 429 or (isinstance(status, int) and 500 <= status <= 599):
                ultimo = f"http_{status}"
                log.warning("datos: HTTP %s en %s (intento %d)", status, path, intento + 1)
                continue
            if status in (401, 403):
                raise ErrorDatosAlpaca("auth")
            if isinstance(status, int) and status >= 400:
                raise ErrorDatosAlpaca(f"http_{status}")
            try:
                cuerpo = respuesta.json()
            except ValueError:
                raise ErrorDatosAlpaca("cuerpo") from None
            if not isinstance(cuerpo, dict):
                raise ErrorDatosAlpaca("cuerpo")
            return cuerpo
        raise ErrorDatosAlpaca(ultimo or "sin_respuesta")

    def _paginas(self, path: str, params: dict) -> list[dict]:
        paginas = []
        token = None
        for _ in range(MAX_PAGINAS):
            q = dict(params)
            if token:
                q["page_token"] = token
            cuerpo = self._get(path, q)
            paginas.append(cuerpo)
            token = cuerpo.get("next_page_token")
            if not token:
                return paginas
        raise ErrorDatosAlpaca("paginacion")

    def _velas_de_lotes(
        self, tickers: list[str], params: dict, tamano: int, intradia: bool,
    ) -> dict[str, list]:
        """Mapa ticker -> listas ya armadas. Llena `self.fallidos` con
        los tickers de los lotes que no respondieron."""
        self.fallidos = []
        lotes = _lotes(tickers, tamano)
        if not lotes:
            return {}
        ok = 0
        # Si ningún lote respondió, se relanza ESE código (auth, sin
        # claves, red), no uno genérico: el respaldo lo anota tal cual.
        ultimo_codigo = "ciclo"
        crudas: dict[str, list] = {}
        pedido_de: dict[str, str] = {}
        for lote in lotes:
            for clave, pedido in lote:
                pedido_de[clave] = pedido
            q = dict(params)
            q["symbols"] = ",".join(clave for clave, _ in lote)
            try:
                paginas = self._paginas("/v2/stocks/bars", q)
            except ErrorDatosAlpaca as ex:
                ultimo_codigo = ex.codigo
                log.warning("datos: lote de %d símbolos falló (%s)", len(lote), ex.codigo)
                self.fallidos.extend(pedido for _, pedido in lote)
                continue
            ok += 1
            for cuerpo in paginas:
                barras = cuerpo.get("bars")
                if not isinstance(barras, dict):
                    # 200 sin la forma esperada: no es "no hay velas".
                    self.fallidos.extend(pedido for _, pedido in lote)
                    ok -= 1
                    ultimo_codigo = "cuerpo"
                    break
                for clave, serie in barras.items():
                    if not isinstance(clave, str):
                        continue
                    destino = pedido_de.get(clave.upper())
                    if destino is None:
                        continue
                    if not isinstance(serie, list):
                        if destino not in self.fallidos:
                            self.fallidos.append(destino)
                        continue
                    crudas.setdefault(destino, []).extend(serie)
            if self.pausa:
                self._dormir(self.pausa)
        if ok == 0:
            raise ErrorDatosAlpaca(ultimo_codigo)
        # Un símbolo marcado fallido no se queda con una serie a medias.
        for destino in self.fallidos:
            crudas.pop(destino, None)
        out: dict[str, list] = {}
        for destino, serie in crudas.items():
            velas = [p for cruda in serie if (p := parsear_barra(cruda)) is not None]
            if velas:
                out[destino] = list(_listas(velas, intradia))
        return out

    def _a_barras(self, ticker: str, listas) -> Barras:
        marcas, o, h, lo, c, vol = listas
        return Barras(ticker, marcas, o, c, h, lo, vol)

    def _a_intradia(self, ticker: str, listas) -> BarraIntradia:
        marcas, o, h, lo, c, vol = listas
        return BarraIntradia(ticker, marcas, o, c, h, lo, vol)

    def barras(self, tickers: list[str], dias: int = 280) -> dict[str, Barras]:
        ahora = self._ahora()
        # Misma ventana que Yahoo: 1 año si el caller pide <= 365, 2 si no.
        calendario = 365 * 2 if dias > 365 else 365
        inicio = ahora - timedelta(days=calendario)
        params = {
            "timeframe": "1Day",
            "start": inicio.isoformat(timespec="seconds"),
            "end": ahora.isoformat(timespec="seconds"),
            "limit": LIMITE_PAGINA,
            "adjustment": AJUSTE,
            "feed": self.feed,
            "sort": "asc",
        }
        crudas = self._velas_de_lotes(tickers, params, LOTE_DIARIO, intradia=False)
        out = {}
        for t, listas in crudas.items():
            b = self._a_barras(t, listas)
            if len(b) >= 20:
                out[t] = b
        log.info("barras diarias del feed: %d/%d tickers", len(out), len(tickers))
        return out

    def barras_intradia(
        self, tickers: list[str], intervalo: str = "1m", periodo: str = "5d",
    ) -> dict[str, BarraIntradia]:
        ahora = self._ahora()
        inicio = ahora - timedelta(days=dias_de_periodo(periodo))
        params = {
            "timeframe": timeframe_de(intervalo),
            "start": inicio.isoformat(timespec="seconds"),
            "end": ahora.isoformat(timespec="seconds"),
            "limit": LIMITE_PAGINA,
            "adjustment": AJUSTE,
            "feed": self.feed,
            "sort": "asc",
        }
        crudas = self._velas_de_lotes(tickers, params, LOTE_INTRADIA, intradia=True)
        out = {}
        for t, listas in crudas.items():
            b = self._a_intradia(t, listas)
            if len(b) >= 5:
                out[t] = b
        log.info("barras intradía del feed: %d/%d tickers", len(out), len(tickers))
        return out

    def snapshots(self, tickers: list[str]) -> dict[str, dict]:
        """Último trade / vela de minuto. Solo lectura, para comparar.
        Si el ciclo falla, lanza `ErrorDatosAlpaca` (el caller decide)."""
        self.fallidos = []
        lotes = _lotes(tickers, LOTE_DIARIO)
        if not lotes:
            return {}
        out: dict[str, dict] = {}
        ok = 0
        ultimo_codigo = "ciclo"
        for lote in lotes:
            try:
                cuerpo = self._get("/v2/stocks/snapshots", {
                    "symbols": ",".join(clave for clave, _ in lote),
                    "feed": self.feed,
                })
            except ErrorDatosAlpaca as ex:
                ultimo_codigo = ex.codigo
                log.warning("datos: snapshots falló (%s)", ex.codigo)
                self.fallidos.extend(pedido for _, pedido in lote)
                continue
            ok += 1
            mapa = cuerpo.get("snapshots") if isinstance(cuerpo.get("snapshots"), dict) else cuerpo
            if not isinstance(mapa, dict):
                self.fallidos.extend(pedido for _, pedido in lote)
                ok -= 1
                continue
            por_clave = {k.upper(): v for k, v in mapa.items() if isinstance(k, str)}
            for clave, pedido in lote:
                if clave in por_clave:
                    out[pedido] = parsear_snapshot(por_clave[clave])
        if ok == 0:
            raise ErrorDatosAlpaca(ultimo_codigo)
        return out

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        """Float, ETF y nombre no vienen en el feed de precios. Siguen
        en Yahoo para que encender SIP no cambie el universo."""
        if self._meta is None:
            from momentum_hunter.data.provider import YahooProvider
            self._meta = YahooProvider()
        return self._meta.metadata(tickers)
