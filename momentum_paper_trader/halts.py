"""Qué hace el ejecutor con un halt o una banda LULD.

La lectura sale de `momentum_hunter.data.halts` (solo el host de
datos). Acá se decide, y se anota en el estado del ejecutor
(`$MOMENTUM_ESTADO_DIR/halts/`, default `/var/lib/momentum/estado`),
nunca en `watchlist.json`.

`MOMENTUM_HALTS`:
  - `observar` (default): registra y, si hay halt con posición
    abierta, avisa por Telegram. No frena la entrada.
  - `enforce`: además no manda la orden de entrada si el símbolo
    está en halt, si el dato falta o si está viejo, o si la banda
    LULD fresca deja el precio afuera. La razón es `BLOQUEO_HALT`.
    No se marca la señal como revisada: el halt se levanta.
  - `off`: no consulta y no avisa. Tiene que ser explícito; un typo
    se queda en observar (un texto desconocido no apaga el registro
    ni empieza a bloquear todo).

Un halt con posición abierta se avisa una vez por símbolo y por
episodio. No se vende y no se cancela el stop: este módulo no recibe
el cliente de órdenes.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter.data.halts import (
    LecturaHalt,
    consultar,
    desconocido,
    directorio_estado,
)
from momentum_paper_trader import dedupe_avisos, estado, notify

log = logging.getLogger("momentum_paper_trader.halts")

ENV_MODO = "MOMENTUM_HALTS"
MODO_OBSERVAR = "observar"
MODO_ENFORCE = "enforce"
MODO_OFF = "off"
_MODOS = frozenset({MODO_OBSERVAR, MODO_ENFORCE, MODO_OFF})


def modo(valor: str | None = None) -> str:
    """`observar` si la variable no está o dice cualquier otra cosa."""
    if valor is None:
        valor = os.environ.get(ENV_MODO, MODO_OBSERVAR)
    s = valor.strip().lower() if isinstance(valor, str) else ""
    if s in _MODOS:
        return s
    if s:
        log.warning("MOMENTUM_HALTS=%s no es observar|enforce|off; se queda en observar", s)
    return MODO_OBSERVAR


def impide_entrada(lectura: LecturaHalt | None, modo_efectivo: str) -> bool:
    """Solo enforce frena la orden. Sin lectura, en enforce, también:
    no haber podido mirar no es 'no hay halt'."""
    if modo_efectivo != MODO_ENFORCE:
        return False
    if lectura is None:
        return True
    return lectura.debe_bloquear


def directorio_halts(base: Path | None = None) -> Path:
    return directorio_estado(base) / "halts"


def _dentro_del_repo(path: Path) -> bool:
    raiz = Path(__file__).resolve().parents[1]
    try:
        path.resolve().relative_to(raiz.resolve())
    except ValueError:
        return False
    return True


def registrar(lectura: LecturaHalt, modo_efectivo: str, accion: str, ahora: datetime,
              *, base: Path | None = None) -> None:
    """Una línea JSON por lectura que importa. Un desconocido en
    observar no se anota en cada tick: llenaría el día de ruido.
    En enforce sí, porque esa línea es el porqué no salió la orden.

    Si el directorio caería dentro del checkout, no se escribe."""
    if (
        lectura.situacion == "desconocido"
        and accion != "bloqueada"
        and modo_efectivo != MODO_ENFORCE
    ):
        return
    path_dir = directorio_halts(base)
    if _dentro_del_repo(path_dir):
        log.warning("no se escribe el registro de halts dentro del repo")
        return
    try:
        from zoneinfo import ZoneInfo
        dia = ahora.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        path_dir.mkdir(parents=True, exist_ok=True)
        path = path_dir / f"{dia}.jsonl"
        linea = {
            "tipo": "lectura",
            "ticker": lectura.ticker,
            "situacion": lectura.situacion,
            "en_halt": lectura.en_halt,
            "restringe_luld": lectura.restringe_luld,
            "fresco": lectura.fresco,
            "fuente": lectura.fuente,
            "evento_en": lectura.evento_en,
            "halt_id": lectura.halt_id,
            "limit_up": lectura.limit_up,
            "limit_down": lectura.limit_down,
            "indicador_luld": lectura.indicador_luld,
            "detalle": lectura.detalle,
            "modo": modo_efectivo,
            "accion": accion,
            "registrado_en": ahora.astimezone(UTC).isoformat(timespec="seconds"),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
            f.flush()
    except Exception as ex:
        log.warning("no se pudo registrar el halt (%s)", type(ex).__name__)


def lecturas_de(
    tickers: list[str],
    ahora: datetime,
    precios: dict[str, float | None] | None = None,
    *,
    directorio: Path | None = None,
    traer=None,
) -> dict[str, LecturaHalt]:
    """Nunca lanza: un fallo de la consulta es desconocido por ticker."""
    try:
        return consultar(tickers, ahora, precios, directorio=directorio, traer=traer)
    except Exception as ex:
        log.warning("consulta de halts falló (%s)", type(ex).__name__)
        return {
            t.strip().upper(): desconocido(t.strip().upper(), fuente="ausente", detalle="consulta fallida")
            for t in tickers if isinstance(t, str) and t.strip()
        }


def _ruta_episodios(base: Path | None) -> Path:
    return directorio_halts(base) / "episodios.json"


def _cargar_episodios(base: Path | None) -> dict:
    try:
        data = json.loads(_ruta_episodios(base).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _guardar_episodios(data: dict, base: Path | None) -> None:
    path = _ruta_episodios(base)
    if _dentro_del_repo(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, path)


def episodio_de(lectura: LecturaHalt, ahora: datetime, *, base: Path | None = None) -> str | None:
    """Id estable del halt en curso. Se abre al ver `en_halt` y se
    cierra solo con un 'no halt' fresco (reanudación o print regular).
    Un desconocido no abre ni cierra: no es evidencia en ninguna de
    las dos direcciones. None si ahora no hay un episodio abierto."""
    if _dentro_del_repo(_ruta_episodios(base)):
        # Sin disco no hay dedupe durable. El id de la lectura alcanza
        # para esta corrida; la siguiente puede repetir el aviso.
        if lectura.en_halt is True:
            return lectura.halt_id or f"sin_reloj:{lectura.ticker}"
        return None
    estado_ep = _cargar_episodios(base)
    clave = lectura.ticker
    actual = estado_ep.get(clave) if isinstance(estado_ep.get(clave), dict) else None
    abierto = isinstance(actual, dict) and actual.get("abierto") is True
    if lectura.en_halt is True:
        if abierto and isinstance(actual.get("id"), str) and actual.get("id"):
            return actual["id"]
        nuevo = lectura.halt_id or lectura.evento_en or f"sin_reloj:{ahora.astimezone(UTC).isoformat(timespec='seconds')}"
        estado_ep[clave] = {"abierto": True, "id": nuevo}
        try:
            _guardar_episodios(estado_ep, base)
        except Exception as ex:
            log.warning("no se pudo guardar el episodio de halt (%s)", type(ex).__name__)
        return nuevo
    if lectura.en_halt is False and lectura.fresco:
        if abierto:
            estado_ep[clave] = {"abierto": False, "id": actual.get("id")}
            try:
                _guardar_episodios(estado_ep, base)
            except Exception as ex:
                log.warning("no se pudo cerrar el episodio de halt (%s)", type(ex).__name__)
        return None
    if abierto and isinstance(actual.get("id"), str):
        return actual["id"]
    return None


def posiciones_abiertas(revisiones: list | None = None) -> list[str]:
    """Tickers con revisión `abierta` y `entro`. Un resultado ausente
    no es una posición. No consulta al bróker."""
    if revisiones is None:
        revisiones = estado.cargar()
    vistos: list[str] = []
    ya: set[str] = set()
    for r in revisiones:
        if getattr(r, "resultado", None) != "abierta" or getattr(r, "entro", None) is not True:
            continue
        ticker = getattr(r, "ticker", None)
        if not isinstance(ticker, str) or not ticker.strip():
            continue
        clave = ticker.strip().upper()
        if clave in ya:
            continue
        ya.add(clave)
        vistos.append(clave)
    return vistos


def avisar_halt(
    lectura: LecturaHalt,
    ahora: datetime,
    *,
    base: Path | None = None,
    enviar=None,
) -> bool:
    """True si este tick mandó el Telegram. Como mucho uno por
    símbolo y episodio. No vende ni cancela nada."""
    if lectura.en_halt is not True:
        episodio_de(lectura, ahora, base=base)
        return False
    episodio = episodio_de(lectura, ahora, base=base)
    if not episodio:
        return False
    # Sin la fecha de sesión en la clave: el tope es el episodio, no
    # el día. `dedupe_avisos.ya_avisada` solo mira si la clave está.
    clave = f"halt:{lectura.ticker}:{episodio}"
    if dedupe_avisos.ya_avisada(clave):
        return False
    dedupe_avisos.marcar(clave, ahora)
    texto = notify.formatear_halt(
        ticker=lectura.ticker, detalle=lectura.detalle, fuente=lectura.fuente,
    )
    mandar = enviar if enviar is not None else notify.enviar
    try:
        mandar(texto)
    except Exception as ex:
        log.warning("no se pudo avisar el halt de %s (%s)", lectura.ticker, type(ex).__name__)
        return False
    registrar(lectura, modo(), "aviso", ahora, base=base)
    return True


def revisar_posiciones_abiertas(
    ahora: datetime,
    *,
    revisiones: list | None = None,
    directorio: Path | None = None,
    traer=None,
    base: Path | None = None,
    enviar=None,
) -> list[str]:
    """Avisos de las posiciones abiertas. Con el flag en `off` no
    mira nada. No coloca, no vende, no cancela."""
    if modo() == MODO_OFF:
        return []
    tickers = posiciones_abiertas(revisiones)
    if not tickers:
        return []
    try:
        lecturas = lecturas_de(tickers, ahora, directorio=directorio, traer=traer)
    except Exception as ex:
        log.warning("no se pudieron leer halts de posiciones (%s)", type(ex).__name__)
        return []
    avisados: list[str] = []
    for ticker in tickers:
        lectura = lecturas.get(ticker) or desconocido(
            ticker, fuente="ausente", detalle="sin lectura de la posición",
        )
        try:
            if avisar_halt(lectura, ahora, base=base, enviar=enviar):
                avisados.append(ticker)
            elif lectura.en_halt is True or lectura.situacion == "luld":
                registrar(lectura, modo(), "observada", ahora, base=base)
        except Exception as ex:
            log.warning("aviso de halt omitido en %s (%s)", ticker, type(ex).__name__)
    return avisados
