"""Lectura de lo que el hunter ya escribió. No abre nada en escritura.

El slot se reconstruye con el mismo corte que `universe.ventana_rotativa`
a partir del `slot` y de `universo_escaneado` que la telemetría guardó.
Si el slot no cabe en el universo de ahora, no se inventa un corte
`[:N]`: ese corte fijo era el sesgo de capitalización que la rotación
existe para evitar.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from momentum_hunter.universe import ventana_rotativa
from momentum_hunter.watchlist import parsear as parsear_watchlist

log = logging.getLogger("shadow_alpaca.lectura")


def ventana_por_slot(simbolos: list[str], limite: int, slot: int) -> list[str] | None:
    """La misma ventana que el hunter escanearía en ese slot.

    Se delega en `ventana_rotativa` solo para el caso en que el reloj
    cae en el slot pedido: así no hay una segunda copia de la aritmética
    del wrap. El slot ya viene de la telemetría; el reloj no se vuelve a
    consultar porque un segundo de más en el borde cambiaría la ranura.
    """
    if limite <= 0 or slot < 0 or not simbolos:
        return None
    inicio = slot * limite
    if inicio >= len(simbolos):
        return None
    # `ventana_rotativa` elige el slot por reloj. Acá el slot ya está
    # decidido: se aplica el mismo corte (y el mismo wrap de la última
    # ventana) que esa función, sin volver a mirar la hora.
    ventana = simbolos[inicio:inicio + limite]
    if len(ventana) < limite:
        ventana = ventana + simbolos[: limite - len(ventana)]
    return ventana


def _normalizar(ticker: object) -> str | None:
    if not isinstance(ticker, str):
        return None
    limpio = ticker.strip().upper()
    return limpio or None


def ultimo_escaneo(dir_telemetria: Path, dia: str) -> dict | None:
    """La corrida `modo=escaneo` más reciente de ese día, o None si no
    hay ninguna legible. None no es un universo vacío."""
    if not dir_telemetria.is_dir():
        return None
    mejor: dict | None = None
    mejor_ts = ""
    for path in sorted(dir_telemetria.glob(f"{dia}/*/events.jsonl")):
        try:
            texto = path.read_text(encoding="utf-8")
        except OSError as ex:
            log.warning("events.jsonl no se pudo leer (%s)", type(ex).__name__)
            continue
        for linea in texto.splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                obj = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("modo") != "escaneo":
                continue
            ts = obj.get("timestamp")
            marca = ts if isinstance(ts, str) else ""
            if mejor is None or marca >= mejor_ts:
                mejor = obj
                mejor_ts = marca
    return mejor


def tickers_del_slot(
    evento: dict | None, simbolos: list[str] | None,
) -> tuple[list[str] | None, str | None]:
    """Tickers que el escaneo de `evento` evaluó, reconstruidos.

    Devuelve `(None, motivo)` cuando no se puede saber cuáles fueron.
    Una lista vacía solo sale si el evento dice que se escaneó y el
    universo resultante queda vacío de verdad — hoy eso no ocurre con
    un slot válido, y preferimos el motivo a un `[]` inventado.
    """
    if evento is None:
        return None, "sin_escaneo"
    if simbolos is None:
        return None, "universo_no_disponible"
    limite = evento.get("universo_escaneado")
    if isinstance(limite, bool) or not isinstance(limite, int) or limite <= 0:
        return None, "universo_escaneado_ausente"
    slot = evento.get("slot")
    if slot is None:
        if limite == len(simbolos):
            return list(simbolos), None
        return None, "slot_ausente_no_se_inventa_el_corte"
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        return None, "slot_ilegible"
    ventana = ventana_por_slot(simbolos, limite, slot)
    if ventana is None:
        return None, "slot_no_cabe_en_el_universo"
    return ventana, None


def ventana_como_el_hunter(simbolos: list[str], limite: int, ahora: datetime) -> list[str]:
    """Atajo de prueba y de diagnóstico: la ventana que `ventana_rotativa`
    elegiría en `ahora`. No se usa para operar; fija que nuestro corte
    por slot no se desvíe del hunter."""
    return ventana_rotativa(simbolos, limite, ahora)


def tickers_de_watchlist(path: Path) -> tuple[list[str] | None, str | None]:
    """Todos los tickers del archivo, en el orden en que están.

    Archivo ausente o ilegible → `(None, motivo)`. Un archivo válido con
    `entradas: []` → `([], None)`: ahí el cero es real.
    """
    if not path.exists():
        return None, "sin_archivo"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        log.warning("watchlist no se pudo leer (%s)", type(ex).__name__)
        return None, "ilegible"
    if not isinstance(data, dict) or not isinstance(data.get("entradas"), list):
        return None, "formato"
    vistos: list[str] = []
    ya: set[str] = set()
    for entrada in parsear_watchlist(data):
        ticker = _normalizar(getattr(entrada, "ticker", None))
        if ticker is None or ticker in ya:
            continue
        ya.add(ticker)
        vistos.append(ticker)
    return vistos, None


def _tickers_de_objetos(items: object, clave: str) -> set[str]:
    out: set[str] = set()
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        ticker = _normalizar(item.get(clave))
        if ticker is not None:
            out.add(ticker)
    return out


def tickers_movers_jsonl(dir_telemetria: Path, dia: str) -> tuple[set[str] | None, str | None]:
    """Unión de `candidatas` de la sombra de movers de ese día.

    Sin archivo → None (`sin_movers_jsonl`), no el conjunto vacío. Un
    archivo cuyas líneas no traen `candidatas` tampoco es 'nadie se
    movió'.
    """
    if not dir_telemetria.is_dir():
        return None, "sin_movers_jsonl"
    paths = sorted(dir_telemetria.glob(f"{dia}/*/movers.jsonl"))
    if not paths:
        return None, "sin_movers_jsonl"
    union: set[str] = set()
    lineas_validas = 0
    for path in paths:
        try:
            texto = path.read_text(encoding="utf-8")
        except OSError as ex:
            log.warning("movers.jsonl no se pudo leer (%s)", type(ex).__name__)
            continue
        for linea in texto.splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                obj = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or "candidatas" not in obj:
                continue
            if not isinstance(obj.get("candidatas"), list):
                continue
            lineas_validas += 1
            union |= _tickers_de_objetos(obj.get("candidatas"), "ticker")
    if lineas_validas == 0:
        return None, "movers_jsonl_sin_candidatas"
    return union, None


def tickers_candidatos_auditoria(
    dir_auditoria: Path, dia: str,
) -> tuple[set[str] | None, str | None]:
    """Tickers de la última corrida del día en `auditoria/{dia}.json`.

    Son los que llegaron a evaluarse (catalizador ya confirmado), no el
    slot entero. Si el archivo no está, None: no se finge que el escaneo
    no tuvo candidatos.
    """
    path = dir_auditoria / f"{dia}.json"
    if not path.exists():
        return None, "sin_auditoria"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        log.warning("auditoría no se pudo leer (%s)", type(ex).__name__)
        return None, "auditoria_ilegible"
    if not isinstance(data, dict) or not isinstance(data.get("corridas"), list):
        return None, "auditoria_sin_corridas"
    ultima = None
    for corrida in data["corridas"]:
        if isinstance(corrida, dict) and isinstance(corrida.get("candidatos"), list):
            ultima = corrida
    if ultima is None:
        return None, "auditoria_sin_candidatos"
    return _tickers_de_objetos(ultima.get("candidatos"), "ticker"), None
