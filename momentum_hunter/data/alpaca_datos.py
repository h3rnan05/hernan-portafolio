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

SÍMBOLOS CON GUION. El universo (NASDAQ Trader) escribe la clase y la
preferida al estilo Yahoo (`BH-A`, `CMS-PB`, `LZM-WT`, `KCAC-UN`).
Alpaca las pide con punto y responde HTTP 400 al LOTE entero si uno
no le sirve: un slot lleno de preferidas marcaba las 1000 como
fallidas y las mandaba a Yahoo. La traducción es solo del query; la
clave que ve el pipeline sigue siendo la pedida. Un sufijo no
verificado no se manda (`-P` pelado incluido: no es `X.P`). Un 400 se
parte en mitades hasta aislar el símbolo, salvo que el lote sea grande
y las dos mitades también sean 400: eso no es un símbolo suelto y no
vale la pena bajar hasta el singleton. 429, 5xx y auth no se parten.

CATÁLOGO LOCAL. Si el JSON de activos está fresco, el símbolo del
query es el que figura ahí (`BRK-B` pedido como `BRK.B` porque el
archivo tiene `BRK.B`). Si el archivo no lista esa forma, no se manda
la traducción adivinada: un símbolo que el catálogo no tiene es el
mismo 400 de lote que esta sección existe para evitar. Si el archivo
falta o está viejo, sigue valiendo la traducción de arriba y no se
filtra nada por él.

RESPALDO IEX (2026-10-06, aprobado por el dueño). Si un pedido con
`feed=sip` recibe HTTP 403 de PLAN (cuerpo con "subscription" o "SIP",
p. ej. "subscription does not permit querying recent SIP data"), se
repite el MISMO pedido con `feed=iex`. Solo ese caso: un 401, un 403
sin ese texto, un 429/5xx o la red siguen fallando igual que antes
(fail-closed). Si IEX también falla, el error es el de IEX y el caller
corta como hoy. Nada de Yahoo.

Pegajoso por proceso: tras el primer 403 de plan, el resto de los
pedidos SIP de ESTE proceso van directo a IEX durante
`PEGAJOSO_IEX_S` (más que un escaneo), para no duplicar llamadas. Cada
escaneo y cada tick del vigía es un proceso nuevo, así que el siguiente
vuelve a probar SIP y, si SIP volvió, se queda en SIP solo. Un proceso
largo re-prueba SIP al vencer la ventana.

`MOMENTUM_FALLBACK_IEX=0` lo apaga sin deploy (default 1). Los que
necesitan SIP de verdad (subastas, halts por condiciones de la quote,
comparadores stream/REST) construyen con `respaldo_iex=False`.
`feeds_usados` dice qué feed contestó de verdad en esta instancia.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta

import requests

from momentum_hunter.data.provider import DataProvider, _velas_finales_en_formacion
from momentum_hunter.models import Barras, BarraIntradia, Metadata
try:
    from uso_api.contador import DATOS
    from uso_api.contador import registrar as registrar_uso
except ImportError:   # medir nunca puede impedir una consulta
    DATOS = "datos"

    def registrar_uso(host: str) -> None:
        return None

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
# Un 400 en un lote de este tamaño (o más) se parte una sola vez. Si
# las dos mitades también devuelven 400, se rinde el lote entero:
# seguir hasta el singleton son ~2n pedidos y el feed está rechazando
# el corte, no un símbolo. Por debajo sí se aísla al culpable.
UMBRAL_CORTE_400 = 8
ESPERA_MAX_S = 8.0

# Respaldo IEX ante un 403 de plan en SIP: ver el docstring del módulo.
ENV_RESPALDO_IEX = "MOMENTUM_FALLBACK_IEX"
# Ventana del modo pegajoso dentro de un mismo proceso. Más larga que
# un escaneo (~9 min); el proceso siguiente arranca de cero y prueba SIP.
PEGAJOSO_IEX_S = 15 * 60
# Estado del proceso, compartido por todas las instancias (el escaneo
# crea varias: velas, snapshots). `desde` es monotónico o None.
_IEX_PEGAJOSO: dict = {"desde": None, "activaciones": 0}

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


def respaldo_iex_habilitado() -> bool:
    """`MOMENTUM_FALLBACK_IEX` (default 1). Solo `0/false/no/off` lo apaga."""
    valor = os.environ.get(ENV_RESPALDO_IEX, "1").strip().lower()
    return valor not in ("0", "false", "no", "off")


def iex_pegajoso_activo(reloj=None) -> bool:
    """True si en este proceso SIP ya dio 403 de plan hace menos de
    `PEGAJOSO_IEX_S`. Vencida la ventana, el próximo pedido prueba SIP."""
    desde = _IEX_PEGAJOSO.get("desde")
    if desde is None:
        return False
    if (reloj or time.monotonic)() - desde >= PEGAJOSO_IEX_S:
        _IEX_PEGAJOSO["desde"] = None
        return False
    return True


def _activar_iex_pegajoso(path: str, reloj=None) -> None:
    _IEX_PEGAJOSO["desde"] = (reloj or time.monotonic)()
    _IEX_PEGAJOSO["activaciones"] = int(_IEX_PEGAJOSO.get("activaciones") or 0) + 1
    log.warning(
        "datos: feed_usado=iex fallback_iex=true -- SIP respondió 403 de suscripción en %s; "
        "se repite con feed=iex y el resto de este ciclo va por IEX (%s=0 lo apaga)",
        path, ENV_RESPALDO_IEX,
    )


def fallback_iex_en_proceso() -> bool:
    """True si en este proceso algún pedido SIP cayó a IEX."""
    return int(_IEX_PEGAJOSO.get("activaciones") or 0) > 0


def reiniciar_respaldo_iex() -> None:
    """Para pruebas: el proceso vuelve a empezar en SIP."""
    _IEX_PEGAJOSO["desde"] = None
    _IEX_PEGAJOSO["activaciones"] = 0


def _es_403_de_plan(respuesta) -> bool:
    """403 por el plan/suscripción SIP, según el `message` del cuerpo
    (o el texto). Sin ese texto no se adivina: no hay respaldo. El
    cuerpo solo se mira acá; nunca se registra."""
    texto = ""
    try:
        cuerpo = respuesta.json()
    except Exception:  # noqa: BLE001 -- cuerpo ilegible: no es de plan
        cuerpo = None
    if isinstance(cuerpo, dict):
        msg = cuerpo.get("message")
        if isinstance(msg, str):
            texto = msg
    if not texto:
        crudo = getattr(respuesta, "text", None)
        if isinstance(crudo, str):
            texto = crudo[:500]
    t = texto.lower()
    return "subscription" in t or "sip" in t


class _Sip403Plan(Exception):
    """Interno: 403 de plan en un pedido con feed=sip. Nunca sale de
    `_get`: se convierte en el reintento IEX o en `http_403`."""


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


# Una sola letra es clase, salvo las que ya significan otra cosa y no
# tienen traducción verificada. P es preferida sin serie (`ETI-P`):
# pedirla como `ETI.P` puede enganchar otra serie, y una clase P de
# verdad es rara. R es rights. Las dos van a fallidos.
_NO_SON_CLASE = frozenset({"P", "R"})


def _simbolo_para_query(ticker: str) -> str | None:
    """Símbolo del query, o None si ese guion no se manda al feed.

    Solo se traducen sufijos comprobados en vivo. Otro guion no se
    adivina: podría pedir otra serie, y un ausente no es un cero. El
    ticker sin guion pasa igual.

    Orden cerrado, del sufijo más específico al más corto. La clase es
    exactamente una letra que no esté en `_NO_SON_CLASE`; un patrón
    flojo se comería `-PB`, `-WT`, `-UN` o el `-P` pelado.
      - `WT` → `.WS` (warrant: `LZM-WT`)
      - `UN` → `.U` (unit: `KCAC-UN`)
      - `P` + una letra → `.PR` + esa letra (`CMS-PB` → `CMS.PRB`)
      - una letra de clase → `.` + esa letra (`BH-A` → `BH.A`)
      - cualquier otro (`-P`, `-R`, `-RI`, `-WTA`, varios guiones) → None
    """
    if "-" not in ticker:
        return ticker
    base, _, sufijo = ticker.partition("-")
    if not base or not sufijo or "-" in sufijo:
        return None
    if sufijo == "WT":
        return f"{base}.WS"
    if sufijo == "UN":
        return f"{base}.U"
    if len(sufijo) == 2 and sufijo[0] == "P" and sufijo[1].isalpha():
        return f"{base}.PR{sufijo[1]}"
    if len(sufijo) == 1 and sufijo.isalpha() and sufijo not in _NO_SON_CLASE:
        return f"{base}.{sufijo}"
    return None


def _resolver_query(ticker: str) -> str | None:
    """Símbolo que va en `symbols`. Con el catálogo frío es la
    traducción de `_simbolo_para_query`. Con el catálogo fresco es la
    forma que el archivo trae, o None si no está: no se inventa una."""
    from momentum_hunter.catalogo_activos import resolver_simbolo

    veredicto = resolver_simbolo(ticker)
    if veredicto.catalogo_fresco:
        return veredicto.simbolo_feed
    return _simbolo_para_query(ticker)


def _pares(tickers: list[str]) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """(query, pedidos) enviables, y pedidos que van directo a fallidos.

    La clave del dict de salida tiene que coincidir con lo pedido: el
    resto del pipeline busca `barras[ticker]` con esa cadena. El query
    es otra cosa y solo vive en el parámetro `symbols`.

    Dos tickers que caen en el mismo símbolo (`BH-A` y `BH.A`, `X-WT`
    y `X.WS`) se piden una vez. La serie vuelve a cada clave: descartar
    una en silencio dejaría un hueco que el caller no puede distinguir
    de "el feed no la tiene".

    Con el catálogo local fresco, el query es el símbolo de ese
    archivo. Sin catálogo, la traducción de clase de siempre."""
    vistos: set[str] = set()
    por_query: dict[str, list[str]] = {}
    orden: list[str] = []
    directos: list[str] = []
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
        query = _resolver_query(clave)
        if query is None:
            directos.append(pedido)
            continue
        if query not in por_query:
            por_query[query] = []
            orden.append(query)
        por_query[query].append(pedido)
    return [(query, por_query[query]) for query in orden], directos


def _pedidos_de(lote: list[tuple[str, list[str]]]) -> list[str]:
    out: list[str] = []
    for _, pedidos in lote:
        out.extend(pedidos)
    return out


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
    no respondieron en la última llamada, más los de sufijo con guion
    que no se enviaron: el respaldo la usa. Un símbolo que el feed
    simplemente no trae no entra ahí (no es un error). `ultimo_codigo`
    es la etiqueta corta del último fallo de esa llamada (nunca el
    cuerpo ni una URL)."""

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
        respaldo_iex: bool | None = None,
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
        self.ultimo_codigo: str | None = None
        # None = lo decide `MOMENTUM_FALLBACK_IEX` en cada pedido.
        self._respaldo_iex = respaldo_iex
        # Feeds que contestaron 200 en esta instancia (telemetría).
        self.feeds_usados: set[str] = set()
        # Yahoo solo para metadata (float / ETF / nombre). No hay respaldo
        # de precios: ver `fuente.ProveedorAlpaca`.
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

    def _iex_permitido(self) -> bool:
        if self._respaldo_iex is not None:
            return bool(self._respaldo_iex)
        return respaldo_iex_habilitado()

    def _get(self, path: str, params: dict) -> dict:
        """Un pedido. Con `feed=sip`: si este proceso ya está pegado a
        IEX, va directo a IEX; si SIP da 403 de plan, se repite el mismo
        pedido con `feed=iex`. Cualquier otro fallo sale igual que antes."""
        q = params
        permitido = params.get("feed") == "sip" and self._iex_permitido()
        if permitido and iex_pegajoso_activo():
            q = {**params, "feed": "iex"}
        try:
            cuerpo = self._get_feed(path, q)
        except _Sip403Plan:
            if not permitido:
                raise ErrorDatosAlpaca("http_403") from None
            _activar_iex_pegajoso(path)
            q = {**params, "feed": "iex"}
            # Si IEX también falla, ese error sale tal cual (fail-closed).
            cuerpo = self._get_feed(path, q)
        feed_q = q.get("feed")
        if isinstance(feed_q, str):
            self.feeds_usados.add(feed_q)
        return cuerpo

    def _get_feed(self, path: str, params: dict) -> dict:
        key, secret = self._credenciales()
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        ultimo = None
        respuesta = None
        for intento in range(self.reintentos):
            if intento:
                self._dormir(self._espera(intento - 1, respuesta))
            # Cada intento gasta cupo del plan (un reintento también).
            registrar_uso(DATOS)
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
                # El código real: 401 son claves inválidas o revocadas,
                # 403 es el plan (p. ej. SIP reciente sin suscripción).
                # Solo el 403 de plan en SIP habilita el respaldo IEX.
                if status == 403 and params.get("feed") == "sip" and _es_403_de_plan(respuesta):
                    raise _Sip403Plan()
                raise ErrorDatosAlpaca(f"http_{status}")
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

    def _pausa_entre_lotes(self) -> None:
        """La misma pausa que hay entre lotes del caller, también entre
        las mitades de un 400: partir no puede martillar el feed."""
        if self.pausa:
            self._dormir(self.pausa)

    def _marcar_fallidos(self, lote: list[tuple[str, list[str]]], codigo: str) -> str:
        simbolos = _pedidos_de(lote)
        self.ultimo_codigo = codigo
        log.warning(
            "datos: lote de %d símbolos falló (%s: %s)",
            len(simbolos), codigo, ", ".join(simbolos[:8]),
        )
        self.fallidos.extend(simbolos)
        return codigo

    def _pedir_barras(
        self, lote: list[tuple[str, list[str]]], params: dict,
    ) -> tuple[list[dict] | None, str | None]:
        q = dict(params)
        q["symbols"] = ",".join(clave for clave, _ in lote)
        try:
            return self._paginas("/v2/stocks/bars", q), None
        except ErrorDatosAlpaca as ex:
            return None, ex.codigo

    def _absorber(
        self, lote: list[tuple[str, list[str]]], paginas: list[dict],
        pedido_de: dict[str, list[str]], crudas: dict[str, list],
    ) -> str | None:
        """None si el 200 tenía la forma esperada. `cuerpo` si no: el
        lote ya está en fallidos y no se queda una serie a medias."""
        for cuerpo in paginas:
            barras = cuerpo.get("bars")
            if not isinstance(barras, dict):
                # 200 sin la forma esperada: no es "no hay velas".
                return self._marcar_fallidos(lote, "cuerpo")
            for clave, serie in barras.items():
                if not isinstance(clave, str):
                    continue
                destinos = pedido_de.get(clave.upper())
                if not destinos:
                    continue
                if not isinstance(serie, list):
                    for destino in destinos:
                        if destino not in self.fallidos:
                            self.fallidos.append(destino)
                    self.ultimo_codigo = "cuerpo"
                    continue
                # Cada clave pedida recibe su propia lista. Compartir
                # la del feed haría que la segunda paginación se
                # escribiera dos veces en la misma.
                for destino in destinos:
                    crudas.setdefault(destino, []).extend(serie)
        return None

    def _tras_400(
        self, lote: list[tuple[str, list[str]]], params: dict,
        pedido_de: dict[str, list[str]], crudas: dict[str, list],
    ) -> str | None:
        """El lote ya respondió 400. None si alguna mitad aportó velas.

        En un lote grande, si las dos mitades también son 400, se deja
        de partir: no es un símbolo suelto y el árbol completo son ~2n
        pedidos. Por debajo del umbral se sigue hasta aislarlo."""
        if len(lote) <= 1:
            return self._marcar_fallidos(lote, "http_400")
        medio = len(lote) // 2
        izq, der = lote[:medio], lote[medio:]
        self._pausa_entre_lotes()
        pag_i, cod_i = self._pedir_barras(izq, params)
        self._pausa_entre_lotes()
        pag_d, cod_d = self._pedir_barras(der, params)
        if (
            len(lote) >= UMBRAL_CORTE_400
            and cod_i == "http_400"
            and cod_d == "http_400"
        ):
            log.info(
                "datos: HTTP 400 en las dos mitades de un lote de %d; no se sigue partiendo",
                len(lote),
            )
            return self._marcar_fallidos(lote, "http_400")
        ri = self._resolver_mitad(izq, pag_i, cod_i, params, pedido_de, crudas)
        rd = self._resolver_mitad(der, pag_d, cod_d, params, pedido_de, crudas)
        if ri is None or rd is None:
            return None
        self.ultimo_codigo = rd or ri
        return self.ultimo_codigo

    def _resolver_mitad(
        self, lote: list[tuple[str, list[str]]], paginas: list[dict] | None,
        codigo: str | None, params: dict, pedido_de: dict[str, list[str]],
        crudas: dict[str, list],
    ) -> str | None:
        if codigo is None:
            return self._absorber(lote, paginas or [], pedido_de, crudas)
        # Solo el 400 se parte. 429, 5xx y auth tumban la mitad entera:
        # partirlos reintentaría un fallo que no es de símbolo.
        if codigo == "http_400":
            return self._tras_400(lote, params, pedido_de, crudas)
        return self._marcar_fallidos(lote, codigo)

    def _incorporar_lote(
        self, lote: list[tuple[str, list[str]]], params: dict,
        pedido_de: dict[str, list[str]], crudas: dict[str, list],
    ) -> str | None:
        """Mete las velas del lote en `crudas`. None si el feed respondió
        (aunque sea con series vacías). Un código si el lote no sirvió:
        esos pedidos ya están en `fallidos`."""
        paginas, codigo = self._pedir_barras(lote, params)
        if codigo is None:
            return self._absorber(lote, paginas or [], pedido_de, crudas)
        if codigo == "http_400" and len(lote) > 1:
            log.info(
                "datos: HTTP 400 en un lote de %d; se parte para aislar el símbolo",
                len(lote),
            )
            return self._tras_400(lote, params, pedido_de, crudas)
        return self._marcar_fallidos(lote, codigo)

    def _velas_de_lotes(
        self, tickers: list[str], params: dict, tamano: int, intradia: bool,
    ) -> dict[str, list]:
        """Mapa ticker -> listas ya armadas. Llena `self.fallidos` con
        los tickers de los lotes que no respondieron y con los que no
        se enviaron. Nunca devuelve una serie a medias: el fallido se
        saca entero, y un volumen ausente no llega a ser 0."""
        self.fallidos = []
        self.ultimo_codigo = None
        enviables, directos = _pares(tickers)
        if directos:
            # No es un 400: ni siquiera se pidieron. El respaldo los ve
            # igual, en `fallidos`, sin envenenar al lote de al lado.
            self.fallidos.extend(directos)
            self.ultimo_codigo = "simbolo"
            log.warning(
                "datos: %d símbolo(s) con sufijo no verificado no se piden al feed (%s)",
                len(directos), ", ".join(directos[:8]),
            )
        lotes = [enviables[i:i + tamano] for i in range(0, len(enviables), tamano)]
        if not lotes:
            return {}
        ok = 0
        # Si ningún lote respondió, se relanza ESE código (auth, sin
        # claves, red), no uno genérico: el respaldo lo anota tal cual.
        ultimo_codigo = self.ultimo_codigo or "ciclo"
        crudas: dict[str, list] = {}
        pedido_de: dict[str, list[str]] = {}
        for lote in lotes:
            for clave, pedidos in lote:
                pedido_de.setdefault(clave, []).extend(pedidos)
            codigo = self._incorporar_lote(lote, params, pedido_de, crudas)
            if codigo is None:
                ok += 1
            else:
                ultimo_codigo = codigo
            if self.pausa and codigo is None:
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

    def snapshots_crudos(self, tickers: list[str]) -> dict[str, dict]:
        """Cuerpo del snapshot por ticker pedido, sin resumir.

        Lo usa el comparador (vía `snapshots`) y la lectura de halts:
        las condiciones del trade y de la quote viven en el cuerpo, y
        resumirlas a un precio las tiraría. Un símbolo que el feed no
        trae no entra en el mapa: ausencia no es un snapshot vacío.
        Si ningún lote respondió, lanza `ErrorDatosAlpaca`. El 400 no
        se parte: este camino no alimenta el escaneo."""
        self.fallidos = []
        # Si no se limpia, el aviso del respaldo leería el código de
        # la llamada de barras anterior.
        self.ultimo_codigo = None
        enviables, directos = _pares(tickers)
        if directos:
            self.fallidos.extend(directos)
            self.ultimo_codigo = "simbolo"
        lotes = [enviables[i:i + LOTE_DIARIO] for i in range(0, len(enviables), LOTE_DIARIO)]
        if not lotes:
            return {}
        out: dict[str, dict] = {}
        ok = 0
        ultimo_codigo = self.ultimo_codigo or "ciclo"
        for lote in lotes:
            try:
                cuerpo = self._get("/v2/stocks/snapshots", {
                    "symbols": ",".join(clave for clave, _ in lote),
                    "feed": self.feed,
                })
            except ErrorDatosAlpaca as ex:
                ultimo_codigo = ex.codigo
                self.ultimo_codigo = ex.codigo
                log.warning("datos: snapshots falló (%s)", ex.codigo)
                self.fallidos.extend(_pedidos_de(lote))
                continue
            ok += 1
            mapa = cuerpo.get("snapshots") if isinstance(cuerpo.get("snapshots"), dict) else cuerpo
            if not isinstance(mapa, dict):
                self.fallidos.extend(_pedidos_de(lote))
                self.ultimo_codigo = "cuerpo"
                ultimo_codigo = "cuerpo"
                ok -= 1
                continue
            por_clave = {k.upper(): v for k, v in mapa.items() if isinstance(k, str)}
            for clave, pedidos in lote:
                crudo = por_clave.get(clave)
                if crudo is None:
                    continue
                for pedido in pedidos:
                    out[pedido] = crudo
        if ok == 0:
            raise ErrorDatosAlpaca(ultimo_codigo)
        return out

    def snapshots(self, tickers: list[str]) -> dict[str, dict]:
        """Último trade / vela de minuto. Solo lectura, para comparar.
        Si el ciclo falla, lanza `ErrorDatosAlpaca` (el caller decide)."""
        crudos = self.snapshots_crudos(tickers)
        return {t: parsear_snapshot(v) for t, v in crudos.items()}

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        """Float, ETF y nombre no vienen en el feed de precios. Siguen
        en Yahoo para que encender SIP no cambie el universo."""
        if self._meta is None:
            from momentum_hunter.data.provider import YahooProvider
            self._meta = YahooProvider()
        return self._meta.metadata(tickers)
