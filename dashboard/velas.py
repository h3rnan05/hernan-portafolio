"""Velas de 1 minuto para el panel, con caché en disco y freno ante Yahoo.
Solo lectura: no escribe nada fuera de su carpeta de caché.

MISMA FUENTE QUE EL HUNTER. La petición a Yahoo es la misma que hace
`YahooProvider._intradia_una` (misma URL, mismos headers, mismos parámetros
con el intervalo y el periodo de `momentum_hunter.config`) y el JSON se
parsea con `provider.parsear_chart_intradia`, la misma función que usa el
bot, y se recorta a hoy con `factors.intradia.barras_de_hoy`. Lo que muestra
el panel es lo que vio el bot, incluida la vela en formación que se descarta.

POR QUÉ NO SE LLAMA A `barras_intradia` DIRECTO. Ese método se traga
cualquier error (incluido un 429) y reintenta tres veces con pausas: desde
el panel eso sería martillar a Yahoo justo cuando está limitando. Acá la
petición se hace UNA vez, sin reintentos, y el código de estado se mira.

EL PANEL NO PUEDE PERJUDICAR AL HUNTER. Yahoo se comparte con el bot desde
la misma IP, y un límite de peticiones agotado por el panel dejaría al bot
sin datos. Tres frenos:
  1. caché con TTL: un ticker se pide como mucho una vez por
     `DASH_VELAS_TTL_SEG` (120 s), aunque el panel se regenere cada minuto;
  2. tope de tickers por corrida (`DASH_VELAS_MAX_TICKERS`, en build_dashboard);
  3. PAUSA: si Yahoo responde 429, el panel deja de pedir velas de TODOS los
     tickers durante `DASH_VELAS_PAUSA_SEG` (900 s) y muestra la copia
     vieja marcada como vieja, o "Sin datos" si no hay copia. La pausa se
     guarda en disco (`yahoo_pausa.json`) porque cada regeneración es un
     proceso nuevo.
Una copia vieja es un dato real, viejo, nunca uno inventado."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

CAMPOS = ("timestamps", "open", "high", "low", "close", "volume")
ARCHIVO_PAUSA = "yahoo_pausa.json"
MINIMO_VELAS = 5   # mismo umbral que YahooProvider.barras_intradia


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


def _velas_validas(velas) -> bool:
    if not isinstance(velas, dict):
        return False
    listas = [velas.get(c) for c in CAMPOS]
    if any(not isinstance(lst, list) for lst in listas):
        return False
    n = len(listas[0])
    return n > 0 and all(len(lst) == n for lst in listas)


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
    caché es una cortesía con Yahoo, no una fuente."""
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


def _leer_cache(ruta: Path) -> tuple[dict | None, datetime | None]:
    crudo = _leer_json(ruta)
    if crudo is None:
        return None, None
    momento, velas = _fecha(crudo.get("obtenido")), crudo.get("velas")
    if momento is None or not _velas_validas(velas):
        return None, None
    return velas, momento


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


def _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg, error) -> dict:
    if velas_cache is None:
        return {"velas": None, "obtenido": None, "origen": None, "error": error}
    vigente = (ahora - obtenido_cache).total_seconds() <= ttl_seg
    return {"velas": velas_cache, "obtenido": obtenido_cache,
            "origen": "cache" if vigente else "cache vencida", "error": error}


def obtener(ticker: str, ahora: datetime, cache_dir: Path, ttl_seg: float,
            fuente=fuente_hunter, pausa_seg: float = 900.0) -> dict:
    """{"velas": dict | None, "obtenido": datetime | None,
        "origen": "fuente" | "cache" | "cache vencida" | None, "error": str | None}

    Caché vigente → no se toca la fuente. Pausa activa (429 reciente) → no
    se toca la fuente y se sirve lo que haya, marcado. Si no, se pide una
    vez; si la fuente falla y había copia, se devuelve la copia CON el
    error, para que el panel diga las dos cosas."""
    ruta = _ruta_cache(cache_dir, ticker)
    velas_cache, obtenido_cache = _leer_cache(ruta)
    if velas_cache is not None and (ahora - obtenido_cache).total_seconds() <= ttl_seg:
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg, None)

    hasta = pausa_hasta(cache_dir)
    if hasta is not None and ahora < hasta:
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                          f"Yahoo limitó peticiones (429): sin pedir velas hasta las {hasta:%H:%M} UTC")

    try:
        velas = fuente(ticker)
    except LimiteDePeticiones:
        hasta = _pausar(cache_dir, ahora, pausa_seg, "429")
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                          f"Yahoo limitó peticiones (429): sin pedir velas hasta las {hasta:%H:%M} UTC")
    except Exception as exc:  # la fuente es red: nunca tumba el panel
        return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg,
                          f"la fuente de velas falló ({type(exc).__name__})")
    if _velas_validas(velas):
        _escribir_json(ruta, {"obtenido": ahora.isoformat(timespec="seconds"), "velas": velas})
        return {"velas": velas, "obtenido": ahora, "origen": "fuente", "error": None}
    return _con_cache(velas_cache, obtenido_cache, ahora, ttl_seg, "la fuente no devolvió velas de hoy")
