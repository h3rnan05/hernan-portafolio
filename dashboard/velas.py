"""Velas de 1 minuto para el panel, con caché en disco. Solo lectura.

MISMA FUENTE QUE EL HUNTER. Las velas salen de `YahooProvider.barras_intradia`
con el intervalo y el periodo de `momentum_hunter.config`, y se recortan a la
sesión de hoy con `factors.intradia.barras_de_hoy`: es la misma llamada y el
mismo recorte que hace el bot al evaluar, así que lo que muestra el panel es lo
que vio el bot (incluida la vela en formación que el proveedor descarta).

POR QUÉ HAY CACHÉ. El panel se regenera cada minuto. Sin caché pediría cada
ticker en operación cada minuto, a la misma IP que ya usa el hunter, y Yahoo
recorta sin avisar. Con caché un ticker se pide como mucho una vez por TTL
(`DASH_VELAS_TTL_SEG`, 120 s por defecto). Si Yahoo falla y hay una copia
anterior, se muestra ESA copia marcada como vencida y con su antigüedad: es un
dato real, viejo, nunca uno inventado. Si no hay copia, "Sin datos".

La caché vive fuera de git (en el VPS junto al HTML, en /var/lib/momentum) y
solo guarda lo que Yahoo devolvió, tal cual."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

CAMPOS = ("timestamps", "open", "high", "low", "close", "volume")


def fuente_hunter(ticker: str) -> dict | None:
    """Velas de hoy del ticker, por el mismo camino que el hunter. None si
    el proveedor no devolvió nada utilizable (Yahoo caído, ticker sin velas,
    menos de 5 velas: el proveedor las descarta igual que en el escaneo)."""
    from momentum_hunter.config import CONFIG
    from momentum_hunter.data.provider import YahooProvider
    from momentum_hunter.factors.intradia import barras_de_hoy

    barras = YahooProvider(pausa=0.0).barras_intradia(
        [ticker], CONFIG.intervalo_intradia, CONFIG.periodo_intradia)
    b = barras.get(ticker)
    if b is None:
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


def _leer_cache(ruta: Path) -> tuple[dict | None, datetime | None]:
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(crudo, dict):
        return None, None
    obtenido = crudo.get("obtenido")
    try:
        momento = datetime.fromisoformat(obtenido) if isinstance(obtenido, str) else None
    except ValueError:
        momento = None
    velas = crudo.get("velas")
    if momento is None or momento.tzinfo is None or not _velas_validas(velas):
        return None, None
    return velas, momento


def _escribir_cache(ruta: Path, velas: dict, obtenido: datetime) -> None:
    """Best-effort y atómico. Si no se puede escribir, el panel sigue: la
    caché es una cortesía con Yahoo, no una fuente."""
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_name(ruta.name + ".tmp")
        temporal.write_text(json.dumps({"obtenido": obtenido.isoformat(timespec="seconds"),
                                        "velas": velas}), encoding="utf-8")
        os.replace(temporal, ruta)
    except OSError:
        pass


def obtener(ticker: str, ahora: datetime, cache_dir: Path, ttl_seg: float,
            fuente=fuente_hunter) -> dict:
    """{"velas": dict | None, "obtenido": datetime | None,
        "origen": "fuente" | "cache" | "cache vencida" | None, "error": str | None}

    Caché vigente → no se toca la fuente. Vencida o ausente → se pide; si la
    fuente falla y había copia, se devuelve la copia vencida CON el error,
    para que el panel diga las dos cosas."""
    ruta = _ruta_cache(cache_dir, ticker)
    velas_cache, obtenido_cache = _leer_cache(ruta)
    if velas_cache is not None and (ahora - obtenido_cache).total_seconds() <= ttl_seg:
        return {"velas": velas_cache, "obtenido": obtenido_cache, "origen": "cache", "error": None}

    error = None
    try:
        velas = fuente(ticker)
    except Exception as exc:  # la fuente es red: nunca tumba el panel
        velas, error = None, f"la fuente de velas falló ({type(exc).__name__})"
    if _velas_validas(velas):
        _escribir_cache(ruta, velas, ahora)
        return {"velas": velas, "obtenido": ahora, "origen": "fuente", "error": None}
    if error is None:
        error = "la fuente no devolvió velas de hoy"
    if velas_cache is not None:
        return {"velas": velas_cache, "obtenido": obtenido_cache, "origen": "cache vencida", "error": error}
    return {"velas": None, "obtenido": None, "origen": None, "error": error}
