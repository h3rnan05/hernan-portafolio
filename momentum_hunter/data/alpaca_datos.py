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

SUBASTA. Las velas de 1 minuto de SIP no traen el cruce de apertura ni
el de cierre: Yahoo sí los mete en la vela de las 9:30 y en la de las
15:59. En KOD (2026-09-28) eso dejó el VWAP del bot en 78.98 contra
75.38 de Yahoo, porque el stop se ancla al VWAP y el cruce (5.47M a
61.87) nunca entró en el precio típico. Se leen con
`GET /v2/stocks/auctions` (solo existe en SIP) y se pliegan en la vela
de minuto que `vwap_real` ya consume. No se toca la fórmula del VWAP,
ni las barras diarias: el agregado diario es otro número, y sumarle el
cruce otra vez movería el filtro de volumen sin que se haya medido.
El size que falta no es cero. Si el endpoint de subastas no responde,
las velas siguen (sin el cruce); no se cae el ciclo a Yahoo, porque el
minuto continuo sí llegó.

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
from momentum_hunter.factors.intradia import es_sesion_regular
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
# Códigos del ejemplo oficial de GET /v2/stocks/auctions (docs Alpaca):
# apertura 'Q' (Market Center Official Open) y 'O' (Opening Prints);
# cierre 'M' (Market Center Official Close) y '6' (Closing Prints).
# 'O' y 'Q' del mismo sitio son el mismo cruce dicho dos veces; igual
# '6' y 'M'. No se suman. Sitios distintos sí son cruces distintos.
CONDICIONES_APERTURA = frozenset({"O", "Q"})
CONDICIONES_CIERRE = frozenset({"6", "M"})
# La impresión de volumen y el precio oficial salen a milisegundos de
# distancia (en el ejemplo, <1 ms). Dos segundos alcanzan para juntarlos
# sin tragarse otro print del minuto.
VENTANA_MISMO_CRUCE_S = 2.0

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


def _impresion(cruda: object) -> dict | None:
    """Un print de subasta. El size no viene en el esquema obligatorio:
    si falta, queda None. No se reemplaza por 0."""
    if not isinstance(cruda, dict):
        return None
    momento = _momento(cruda.get("t"))
    precio = _numero(cruda.get("p"))
    cond = cruda.get("c")
    if momento is None or precio is None or precio <= 0:
        return None
    if not isinstance(cond, str) or not cond.strip():
        return None
    exch = cruda.get("x")
    if not isinstance(exch, str):
        exch = ""
    if "s" not in cruda or cruda.get("s") is None:
        size = None
    else:
        size = _numero(cruda.get("s"))
    return {"t": momento, "p": precio, "s": size, "c": cond.strip().upper(), "x": exch}


def _impresiones(crudo: object) -> list[dict]:
    if not isinstance(crudo, list):
        return []
    return [imp for item in crudo if (imp := _impresion(item)) is not None]


def _size_util(imp: dict | None) -> float | None:
    """Size que se puede sumar al volumen. 0 o negativo no es un cruce
    (y un ausente tampoco): no se convierten en volumen."""
    if imp is None:
        return None
    size = imp.get("s")
    if size is None or size <= 0:
        return None
    return size


def _grupos_de_cruce(prints: list[dict]) -> list[list[dict]]:
    """Agrupa prints del mismo exchange que salen juntos. 'O'+'Q' (o
    '6'+'M') son un solo cruce; juntarlos por exchange y por ventana
    corta evita contar esas acciones dos veces."""
    ordenadas = sorted(prints, key=lambda imp: (imp["x"], imp["t"]))
    grupos: list[list[dict]] = []
    actual: list[dict] = []
    for imp in ordenadas:
        if not actual:
            actual = [imp]
            continue
        mismo_sitio = imp["x"] == actual[0]["x"]
        delta = (imp["t"] - actual[0]["t"]).total_seconds()
        if mismo_sitio and 0 <= delta <= VENTANA_MISMO_CRUCE_S:
            actual.append(imp)
        else:
            grupos.append(actual)
            actual = [imp]
    if actual:
        grupos.append(actual)
    return grupos


def _colapsar_grupo(grupo: list[dict], cond_precio: str, cond_volumen: str) -> dict:
    """Un cruce por grupo. El precio y el size salen de la impresión que
    trae las acciones ('O' o '6'): es el print que se negoció. Si esa no
    trae size, se usa la oficial ('Q' o 'M'). Nunca se suman las dos."""
    def primera(cond: str) -> dict | None:
        for imp in grupo:
            if imp["c"] == cond:
                return imp
        return None

    de_volumen = primera(cond_volumen)
    oficial = primera(cond_precio)
    precio = None
    size = _size_util(de_volumen)
    if size is not None and de_volumen is not None:
        precio = de_volumen["p"]
    else:
        size = _size_util(oficial)
        if size is not None and oficial is not None:
            precio = oficial["p"]
        else:
            for imp in grupo:
                size = _size_util(imp)
                if size is not None:
                    precio = imp["p"]
                    break
            else:
                size = None
    if precio is None:
        fuente = oficial or de_volumen or grupo[0]
        precio = fuente["p"]
    return {"p": precio, "s": size, "t": min(imp["t"] for imp in grupo), "x": grupo[0]["x"]}


def cruces_de_lado(prints: list[dict], condiciones: frozenset[str], cond_precio: str, cond_volumen: str) -> list[dict]:
    """Cruces de un lado (apertura o cierre), uno por exchange. Una
    condición que no sea la del cruce oficial se ignora: no se inventa
    una subasta a partir de otro flag."""
    validas = [imp for imp in prints if imp["c"] in condiciones]
    if not validas:
        return []
    return [_colapsar_grupo(g, cond_precio, cond_volumen) for g in _grupos_de_cruce(validas)]


def minuto_para_vwap(momento: datetime, *, cierre: bool) -> str | None:
    """Minuto de la vela que `vwap_real` sí mira.

    La apertura cae en su propio minuto (9:30 ET entra en la sesión).
    El cierre se imprime en el segundo 16:00:00 ET y `es_sesion_regular`
    corta en las 20:00 UTC sin incluirlas: una vela con ese timestamp
    no pesaría en el VWAP ni en el volumen de sesión. Se agrega al
    minuto anterior (15:59), que es donde Yahoo mete el closing cross.
    Si ese minuto tampoco es sesión regular —el corte está fijo en
    horario de verano— no se elige otro balde: meterlo en la vela
    equivocada movería el VWAP a propósito.
    """
    piso = momento.astimezone(UTC).replace(second=0, microsecond=0)
    iso = piso.isoformat(timespec="seconds")
    if es_sesion_regular(iso):
        return iso
    if cierre:
        previo = (piso - timedelta(minutes=1)).isoformat(timespec="seconds")
        if es_sesion_regular(previo):
            return previo
    return None


def _resumen_lado(aplicados: list[dict], vistos: list[dict]) -> dict | None:
    """Lo que se plegó. Si no se plegó, el volumen queda None (no 0):
    o nadie mandó size, o el print cae fuera de la sesión que el VWAP
    mira. `sin_size` separa esos dos casos para el script."""
    if aplicados:
        primario = min(aplicados, key=lambda a: (-a["s"], a["t"]))
        return {
            "precio": primario["p"],
            "volumen": sum(a["s"] for a in aplicados),
            "minuto": primario["minuto"],
            "sin_size": False,
        }
    if not vistos:
        return None
    con_size = [c for c in vistos if c["s"] is not None and c["s"] > 0]
    fuente = max(con_size, key=lambda c: c["s"]) if con_size else min(vistos, key=lambda c: c["t"])
    return {"precio": fuente["p"], "volumen": None, "minuto": None, "sin_size": not con_size}


def incorporar_subastas(listas, dias_crudos: list) -> tuple[tuple, list]:
    """Mete los cruces en las velas de minuto ya parseadas.

    El VWAP usa (H+L+C)/3 ponderado por volumen, no el open. Por eso el
    precio del cruce entra al high y al low (si no, el volumen nuevo
    pesaría al precio del continuo y el gapper seguiría con el VWAP
    alto) y el open de esa vela pasa a ser el del cruce más grande, que
    es la apertura que Yahoo muestra. El cierre oficial reemplaza el
    close de las 15:59 por la misma razón.

    Exchanges distintos se suman: son cruces distintos y el volumen
    consolidado de Yahoo los incluye. 'O' y 'Q' del mismo exchange, no.

    Devuelve las listas (marcas, o, h, low, c, vol) y un aporte por día
    para el script de comparación. Las barras diarias no pasan por acá.
    """
    marcas, o, h, lo, c, vol = (list(x) for x in listas)
    # Si el minuto no existía, la vela es solo el cruce: no hay un close
    # del continuo que conservar. Si existía, el close de las 9:30 se
    # queda (es el último trade del minuto, como en Yahoo).
    originales = set(marcas)

    def sumar(minuto: str, precio: float, size: float) -> None:
        nonlocal marcas, o, h, lo, c, vol
        if minuto in marcas:
            i = marcas.index(minuto)
            if precio > h[i]:
                h[i] = precio
            if precio < lo[i]:
                lo[i] = precio
            vol[i] = vol[i] + size
            return
        marcas.append(minuto)
        o.append(precio)
        h.append(precio)
        lo.append(precio)
        c.append(precio)
        vol.append(float(size))
        filas = sorted(zip(marcas, o, h, lo, c, vol, strict=True), key=lambda r: r[0])
        marcas, o, h, lo, c, vol = (list(col) for col in zip(*filas, strict=True))

    def fijar(minuto: str, precio: float, lado: str) -> None:
        i = marcas.index(minuto)
        if lado == "apertura":
            o[i] = precio
        else:
            c[i] = precio

    por_dia: dict[str, dict] = {}
    for dia in dias_crudos:
        if not isinstance(dia, dict):
            continue
        fecha = dia.get("d")
        if not isinstance(fecha, str) or not fecha:
            continue
        slot = por_dia.setdefault(fecha, {"o": [], "c": []})
        if isinstance(dia.get("o"), list):
            slot["o"].extend(dia["o"])
        if isinstance(dia.get("c"), list):
            slot["c"].extend(dia["c"])

    aportes = []
    for fecha in sorted(por_dia):
        slot = por_dia[fecha]
        lados = {
            "apertura": (cruces_de_lado(_impresiones(slot["o"]), CONDICIONES_APERTURA, "Q", "O"), False),
            "cierre": (cruces_de_lado(_impresiones(slot["c"]), CONDICIONES_CIERRE, "M", "6"), True),
        }
        resumen = {}
        for nombre, (cruces, es_cierre) in lados.items():
            aplicados = []
            for cruce in cruces:
                size = cruce["s"]
                if size is None or size <= 0:
                    continue
                minuto = minuto_para_vwap(cruce["t"], cierre=es_cierre)
                if minuto is None:
                    continue
                sumar(minuto, cruce["p"], size)
                aplicados.append({**cruce, "minuto": minuto})
            info = _resumen_lado(aplicados, cruces)
            if info is not None and aplicados:
                fijar(info["minuto"], info["precio"], nombre)
                if info["minuto"] not in originales:
                    # Sin vela continua, open y close son el cruce grande.
                    # Si no, el close se quedaría en el primer exchange
                    # insertado y el precio típico no sería el del cruce.
                    i = marcas.index(info["minuto"])
                    o[i] = info["precio"]
                    c[i] = info["precio"]
            resumen[nombre] = info
        if resumen["apertura"] is None and resumen["cierre"] is None:
            continue
        aportes.append({"dia": fecha, "apertura": resumen["apertura"], "cierre": resumen["cierre"]})
    return (marcas, o, h, lo, c, vol), aportes


def _juntar_subastas(paginas: list[dict]) -> dict[str, list]:
    """Símbolo en mayúsculas -> días crudos, en el orden de las páginas."""
    out: dict[str, list] = {}
    for cuerpo in paginas:
        auctions = cuerpo.get("auctions")
        if not isinstance(auctions, dict):
            raise ErrorDatosAlpaca("cuerpo")
        for simbolo, dias in auctions.items():
            if not isinstance(simbolo, str) or not isinstance(dias, list):
                continue
            out.setdefault(simbolo.upper(), []).extend(dias)
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
        # Lo que la última `barras_intradia` plegó, por ticker pedido.
        # Vacío si no hubo cruce o si la subasta no respondió: no es un
        # volumen cero. El script de comparación lo imprime.
        self.aportes_subasta: dict[str, list] = {}
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
        self.aportes_subasta = {}
        ahora = self._ahora()
        inicio = ahora - timedelta(days=dias_de_periodo(periodo))
        tf = timeframe_de(intervalo)
        params = {
            "timeframe": tf,
            "start": inicio.isoformat(timespec="seconds"),
            "end": ahora.isoformat(timespec="seconds"),
            "limit": LIMITE_PAGINA,
            "adjustment": AJUSTE,
            "feed": self.feed,
            "sort": "asc",
        }
        crudas = self._velas_de_lotes(tickers, params, LOTE_INTRADIA, intradia=True)
        # Solo el minuto SIP. En 5m/1h el cruce no cae en un balde que
        # el VWAP de 1 minuto sepa leer, e IEX no tiene este endpoint.
        # Las diarias no se tocan: ver el docstring del módulo.
        if tf == "1Min" and self.feed == "sip" and crudas:
            subastas = self._descargar_subastas(list(crudas), inicio, ahora)
            for t in list(crudas):
                nuevas, aporte = incorporar_subastas(crudas[t], subastas.get(t, []))
                crudas[t] = nuevas
                if aporte:
                    self.aportes_subasta[t] = aporte
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

    def _descargar_subastas(self, tickers: list[str], inicio: datetime, fin: datetime) -> dict[str, list]:
        """Ticker pedido -> días crudos de `GET /v2/stocks/auctions`.

        Un lote que falla se omite y no entra en `fallidos`: las velas
        de ese lote ya llegaron, y marcarlas caídas haría que el
        respaldo las reemplace por Yahoo (otra cinta mezclada con SIP).
        """
        out: dict[str, list] = {}
        for lote in _lotes(tickers, LOTE_INTRADIA):
            pedido_de = {clave: pedido for clave, pedido in lote}
            try:
                paginas = self._paginas("/v2/stocks/auctions", {
                    "symbols": ",".join(clave for clave, _ in lote),
                    "start": inicio.isoformat(timespec="seconds"),
                    "end": fin.isoformat(timespec="seconds"),
                    "limit": LIMITE_PAGINA,
                    "feed": "sip",
                    "sort": "asc",
                })
                juntos = _juntar_subastas(paginas)
            except ErrorDatosAlpaca as ex:
                log.warning(
                    "datos: subasta de %d símbolos no disponible (%s); esas velas quedan sin el cruce",
                    len(lote), ex.codigo,
                )
                continue
            for clave, dias in juntos.items():
                pedido = pedido_de.get(clave)
                if pedido is not None:
                    out.setdefault(pedido, []).extend(dias)
        return out

    def metadata(self, tickers: list[str]) -> dict[str, Metadata]:
        """Float, ETF y nombre no vienen en el feed de precios. Siguen
        en Yahoo para que encender SIP no cambie el universo."""
        if self._meta is None:
            from momentum_hunter.data.provider import YahooProvider
            self._meta = YahooProvider()
        return self._meta.metadata(tickers)
