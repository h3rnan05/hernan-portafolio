"""Velas de 1 minuto para el panel, con caché en disco y freno ante Yahoo.
Solo lectura: no escribe nada fuera de su carpeta de caché.

PRIMERO EL FEED DE DATOS, NO EL DE ÓRDENES. `fuente_alpaca` pide barras a
`AlpacaProvider` (`https://data.alpaca.markets`), el mismo cliente que el
hunter, con el feed de `ALPACA_DATA_FEED` o `sip`. Las claves las lee ese
cliente (`ALPACA_PAPER_API_KEY` / `_SECRET`); acá no se copian ni se
escriben. Yahoo queda de respaldo: una sola petición, el mismo parseo de
siempre (`parsear_chart_intradia` + `barras_de_hoy`).

MENOS DE 5 VELAS DE HOY NO ALCANZA. `MINIMO_VELAS` es el piso del gráfico,
no un cero disfrazado: por debajo de eso (o si falta una vela) se cae a
Yahoo. Un campo ausente no se rellena. Si las dos fuentes fallan, no hay
barras: "Sin datos", o la copia vieja marcada como vieja.

POR QUÉ YAHOO SIGUE SIENDO UNA SOLA PETICIÓN. `YahooProvider.barras_intradia`
se traga el error (incluido un 429) y reintenta. Desde el panel eso
martillaría a Yahoo justo cuando está limitando. El feed de Alpaca sí se
pide con su `barras_intradia`: ese cliente ya limita los reintentos y no
comparte el cupo de Yahoo.

EL PANEL NO PUEDE PERJUDICAR AL HUNTER EN YAHOO. Esa fuente se comparte
con el bot desde la misma IP. La pausa de 429 (y la pausa que el bot
anota en su propio archivo) frena SOLO la rama Yahoo: con el feed de
datos en pie, un castigo de Yahoo no deja al gráfico en blanco. Tres
frenos, todos de esa rama:
  1. caché con TTL: un ticker se pide como mucho una vez por
     `DASH_VELAS_TTL_SEG` (120 s), aunque el panel se regenere cada minuto;
  2. tope de tickers por corrida (`DASH_VELAS_MAX_TICKERS`, en build_dashboard);
  3. PAUSA: si Yahoo responde 429, el panel deja de pedirle velas de TODOS
     los tickers durante `DASH_VELAS_PAUSA_SEG` (900 s) y, si el feed
     tampoco respondió, muestra la copia vieja marcada como vieja, o
     "Sin datos" si no hay copia. La pausa se guarda en disco
     (`yahoo_pausa.json`) porque cada regeneración es un proceso nuevo.
Una copia vieja es un dato real, viejo, nunca uno inventado."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

CAMPOS = ("timestamps", "open", "high", "low", "close", "volume")
ARCHIVO_PAUSA = "yahoo_pausa.json"
# Piso del gráfico, no un valor inventado. El hunter descarta una serie
# intradía de menos de 5 velas; acá el corte es sobre HOY (después de
# `barras_de_hoy`): 1–4 minutos no son una sesión dibujable y no se
# estiran con ceros. Por debajo, `obtener` cae a Yahoo.
MINIMO_VELAS = 5
ORIGEN_YAHOO = "yahoo (respaldo)"


class LimiteDePeticiones(Exception):
    """Yahoo respondió 429: hay que dejar de pedir un rato."""


def fuente_hunter(ticker: str) -> dict | None:
    """Velas de hoy del ticker, con la misma petición y el mismo parseo que
    el hunter, en UNA sola petición. None si Yahoo no devolvió velas
    utilizables (ticker sin velas, menos de 5: el hunter también las
    descarta). Lanza `LimiteDePeticiones` ante un 429 y `requests.HTTPError`
    ante otro error HTTP."""
    from momentum_hunter.config import CONFIG
    from momentum_hunter.data import provider
    from momentum_hunter.factors.intradia import barras_de_hoy

    prov = provider.YahooProvider()
    r = requests.get(
        prov.CHART.format(t=ticker),
        params=prov.params_intradia(CONFIG.intervalo_intradia, CONFIG.periodo_intradia),
        headers=prov.HEADERS, timeout=15,
    )
    if r.status_code == 429:
        raise LimiteDePeticiones(f"Yahoo respondió 429 para {ticker}")
    r.raise_for_status()
    b = provider.parsear_chart_intradia(ticker, r.json())
    if b is None or len(b) < MINIMO_VELAS:
        return None
    hoy = barras_de_hoy(b)
    return {campo: list(getattr(hoy, campo)) for campo in CAMPOS}


def feed_de_datos() -> str:
    """`ALPACA_DATA_FEED` o `sip`. Vacío no es un feed: es el de siempre."""
    return os.environ.get("ALPACA_DATA_FEED") or "sip"


def origen_alpaca(feed: str | None = None) -> str:
    """Etiqueta que se guarda con las velas. `sip` → `alpaca-sip`."""
    return f"alpaca-{feed if feed is not None else feed_de_datos()}"


def fuente_alpaca(ticker: str) -> dict | None:
    """Velas de hoy del ticker desde el feed de DATOS de Alpaca.

    `AlpacaProvider(feed=...).barras_intradia` y después `barras_de_hoy`,
    la misma forma `CAMPOS` que Yahoo. None si el feed no trae el ticker
    o hoy tiene menos de `MINIMO_VELAS` velas: eso no es un error, es
    "no hay sesión usable" y `obtener` pide el respaldo. Lanza lo que
    lance el provider (HTTP 4xx/5xx, red, sin claves). No lee ni escribe
    las claves: las toma `AlpacaProvider` del entorno, igual que el hunter.
    """
    from momentum_hunter.config import CONFIG
    from momentum_hunter.data.alpaca_datos import AlpacaProvider
    from momentum_hunter.factors.intradia import barras_de_hoy

    feed = feed_de_datos()
    prov = AlpacaProvider(feed=feed)
    barras = prov.barras_intradia([ticker], CONFIG.intervalo_intradia, CONFIG.periodo_intradia)
    b = barras.get(ticker) if isinstance(barras, dict) else None
    if b is None:
        return None
    hoy = barras_de_hoy(b)
    if len(hoy) < MINIMO_VELAS:
        return None
    return {campo: list(getattr(hoy, campo)) for campo in CAMPOS}


def _velas_validas(velas) -> bool:
    if not isinstance(velas, dict):
        return False
    listas = [velas.get(c) for c in CAMPOS]
    if any(not isinstance(lst, list) for lst in listas):
        return False
    n = len(listas[0])
    return n > 0 and all(len(lst) == n for lst in listas)


def _usables(velas, minimo: int = MINIMO_VELAS) -> bool:
    """Serie alineada y con al menos `minimo` velas de hoy. Menos que eso
    no se cachea ni se dibuja: no se rellena hasta el piso."""
    return _velas_validas(velas) and len(velas["close"]) >= minimo


def _ruta_cache(cache_dir: Path, ticker: str) -> Path:
    limpio = "".join(ch for ch in ticker.upper() if ch.isalnum() or ch in ".-")
    return cache_dir / f"velas_{limpio}.json"


def _leer_json(ruta: Path) -> dict | None:
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return crudo if isinstance(crudo, dict) else None


def _escribir_json(ruta: Path, datos: dict) -> None:
    """Best-effort y atómico. Si no se puede escribir, el panel sigue: la
    caché evita repetir el pedido, no es una fuente."""
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_name(ruta.name + ".tmp")
        temporal.write_text(json.dumps(datos), encoding="utf-8")
        os.replace(temporal, ruta)
    except OSError:
        pass


def _fecha(valor) -> datetime | None:
    try:
        momento = datetime.fromisoformat(valor) if isinstance(valor, str) else None
    except ValueError:
        return None
    return momento if momento is not None and momento.tzinfo is not None else None


def _leer_cache(ruta: Path) -> tuple[dict | None, datetime | None, str | None]:
    crudo = _leer_json(ruta)
    if crudo is None:
        return None, None, None
    momento, velas = _fecha(crudo.get("obtenido")), crudo.get("velas")
    if momento is None or not _velas_validas(velas):
        return None, None, None
    origen = crudo.get("origen_fuente")
    if not isinstance(origen, str) or not origen:
        origen = None
    return velas, momento, origen


def pausa_hasta(cache_dir: Path) -> datetime | None:
    """Hasta cuándo el panel no debe pedir velas a Yahoo, o None."""
    crudo = _leer_json(cache_dir / ARCHIVO_PAUSA)
    return _fecha(crudo.get("hasta")) if crudo else None


def _pausar(cache_dir: Path, ahora: datetime, segundos: float, motivo: str) -> datetime:
    hasta = ahora + timedelta(seconds=segundos)
    _escribir_json(cache_dir / ARCHIVO_PAUSA, {"hasta": hasta.isoformat(timespec="seconds"),
                                              "desde": ahora.isoformat(timespec="seconds"),
                                              "motivo": motivo})
    return hasta


def _resultado(velas, obtenido, origen, error, origen_fuente) -> dict:
    return {"velas": velas, "obtenido": obtenido, "origen": origen,
            "origen_fuente": origen_fuente, "error": error}


def _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg, error, origen_fuente=None) -> dict:
    if velas_cache is None:
        # Sin copia no hay fuente que marcar: un origen colgado de la nada
        # parecería que sí llegaron velas.
        return _resultado(None, None, None, error, None)
    vigente = (ahora - obtenido_cache).total_seconds() <= ttl_seg
    return _resultado(velas_cache, obtenido_cache,
                      "cache" if vigente else "cache vencida", error, origen_fuente)


def pausa_del_bot(ruta: Path | None) -> datetime | None:
    """Hasta cuándo el BOT dejó de pedir a Yahoo (su propio archivo,
    MOMENTUM_YAHOO_PAUSA_ARCHIVO en el VPS). El panel lo lee y tampoco
    le pide a Yahoo: si el escaneo ya recibió un 429 desde esta IP,
    sumar el respaldo del gráfico solo alarga el castigo. No frena el
    feed de datos. Solo lectura: el panel jamás escribe ese archivo, y
    el bot nunca lee el del panel."""
    if ruta is None:
        return None
    crudo = _leer_json(Path(ruta))
    return _fecha(crudo.get("hasta")) if crudo else None


def _guardar(ruta: Path, ahora: datetime, velas: dict, origen_fuente: str) -> None:
    _escribir_json(ruta, {"obtenido": ahora.isoformat(timespec="seconds"),
                          "velas": velas, "origen_fuente": origen_fuente})


def obtener(ticker: str, ahora: datetime, cache_dir: Path, ttl_seg: float,
            fuente=None, pausa_seg: float = 900.0, pausa_bot: Path | None = None,
            alpaca=None) -> dict:
    """{"velas": dict | None, "obtenido": datetime | None,
        "origen": "fuente" | "cache" | "cache vencida" | None,
        "origen_fuente": "alpaca-sip" | "yahoo (respaldo)" | None,
        "error": str | None}

    Caché vigente → no se toca ninguna fuente. Si no, primero el feed de
    datos. Yahoo solo si ese feed lanza o no deja `MINIMO_VELAS` velas de
    hoy. La pausa de 429 (la del panel y la del bot) se mira recién en
    esa rama: un castigo de Yahoo no bloquea al feed. Si las dos fallan
    y había copia, se devuelve la copia CON el error; si no había, velas
    es None. Nunca una serie de ceros.

    `fuente` y `alpaca` se resuelven al llamar (no en la firma) para que
    un doble de prueba tape el nombre del módulo.
    """
    pedir_yahoo = fuente_hunter if fuente is None else fuente
    pedir_alpaca = fuente_alpaca if alpaca is None else alpaca
    ruta = _ruta_cache(cache_dir, ticker)
    velas_cache, obtenido_cache, origen_cache = _leer_cache(ruta)
    if velas_cache is not None and (ahora - obtenido_cache).total_seconds() <= ttl_seg:
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg, None, origen_cache)

    # El feed no comparte el cupo de Yahoo: se pide aunque haya pausa.
    try:
        velas_alpaca = pedir_alpaca(ticker)
    except Exception:
        # El texto de la excepción no se guarda: puede traer una URL.
        # El tipo tampoco hace falta si Yahoo todavía puede responder.
        velas_alpaca = None
    if _usables(velas_alpaca):
        origen_fuente = origen_alpaca()
        _guardar(ruta, ahora, velas_alpaca, origen_fuente)
        return _resultado(velas_alpaca, ahora, "fuente", None, origen_fuente)

    hasta = pausa_hasta(cache_dir)
    if hasta is not None and ahora < hasta:
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                          f"Yahoo limitó peticiones (429): sin pedir velas hasta las {hasta:%H:%M} UTC",
                          origen_cache)
    hasta_bot = pausa_del_bot(pausa_bot)
    if hasta_bot is not None and ahora < hasta_bot:
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                          f"el bot está en pausa con Yahoo (429): sin pedir velas hasta las {hasta_bot:%H:%M} UTC",
                          origen_cache)

    try:
        velas = pedir_yahoo(ticker)
    except LimiteDePeticiones:
        hasta = _pausar(cache_dir, ahora, pausa_seg, "429")
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                          f"Yahoo limitó peticiones (429): sin pedir velas hasta las {hasta:%H:%M} UTC",
                          origen_cache)
    except Exception as exc:  # la fuente es red: nunca tumba el panel
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                          f"la fuente de velas falló ({type(exc).__name__})", origen_cache)
    if _velas_validas(velas):
        _guardar(ruta, ahora, velas, ORIGEN_YAHOO)
        return _resultado(velas, ahora, "fuente", None, ORIGEN_YAHOO)
    return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                      "la fuente no devolvió velas de hoy", origen_cache)
