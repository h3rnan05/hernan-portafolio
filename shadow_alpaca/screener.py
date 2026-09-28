"""B2. Screener de Alpaca en sombra, cada ~5 minutos de sesión.

`/v1beta1/screener/stocks/most-actives` (top 100) y
`/v1beta1/screener/stocks/movers` (top 50). No hay filtro de market cap:
ese campo no viene en la respuesta, se anota null con el motivo, y una
fila no se tira por no traerlo. Un 0 ahí sería un market cap inventado.

El solape se calcula contra el slot del escaneo de hoy, contra los
candidatos de la auditoría y contra `movers.jsonl` de la sombra que ya
existe. Si una de esas bases no está, el solape es null, no cero.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter.sesion import en_sesion
from momentum_hunter.universe import tickers as tickers_universo

from shadow_alpaca.cliente import ClienteDatos, ErrorDatos
from shadow_alpaca.jsonl_log import append_linea, dir_salida
from shadow_alpaca.lectura import (
    tickers_candidatos_auditoria,
    tickers_del_slot,
    tickers_movers_jsonl,
    ultimo_escaneo,
)
from shadow_alpaca.numeros import numero

log = logging.getLogger("shadow_alpaca.screener")

RUTA_ACTIVOS = "/v1beta1/screener/stocks/most-actives"
RUTA_MOVERS = "/v1beta1/screener/stocks/movers"
TOP_ACTIVOS = 100
TOP_MOVERS = 50
MOTIVO_MARKET_CAP = "alpaca_screener_no_incluye_market_cap"


def _simbolo(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    simbolo = item.get("symbol")
    if not isinstance(simbolo, str) or not simbolo.strip():
        return None
    return simbolo.strip().upper()


def fila_activo(item: object) -> dict | None:
    """Una fila de most-actives. El market cap del payload, si viniera,
    se ignora: este endpoint no es una fuente de capitalización y un 0
    de relleno no puede colarse al registro."""
    simbolo = _simbolo(item)
    if simbolo is None or not isinstance(item, dict):
        return None
    return {
        "symbol": simbolo,
        "volume": numero(item.get("volume")),
        "trade_count": numero(item.get("trade_count")),
        "market_cap": None,
        "motivo_market_cap": MOTIVO_MARKET_CAP,
    }


def fila_mover(item: object, lado: str) -> dict | None:
    simbolo = _simbolo(item)
    if simbolo is None or not isinstance(item, dict):
        return None
    return {
        "symbol": simbolo,
        "lado": lado,
        "percent_change": numero(item.get("percent_change")),
        "change": numero(item.get("change")),
        "price": numero(item.get("price")),
        "market_cap": None,
        "motivo_market_cap": MOTIVO_MARKET_CAP,
    }


def _recortar(filas: list[dict], tope: int) -> tuple[list[dict], bool]:
    if len(filas) <= tope:
        return filas, False
    return filas[:tope], True


def parsear_activos(cuerpo: object, tope: int = TOP_ACTIVOS) -> dict:
    if not isinstance(cuerpo, dict) or "most_actives" not in cuerpo:
        return {"disponible": False, "motivo": "most_actives_ausente", "n": None, "filas": None, "truncado": None}
    raw = cuerpo.get("most_actives")
    if not isinstance(raw, list):
        return {"disponible": False, "motivo": "most_actives_ilegible", "n": None, "filas": None, "truncado": None}
    filas: list[dict] = []
    ilegibles = 0
    for item in raw:
        fila = fila_activo(item)
        if fila is None:
            ilegibles += 1
        else:
            filas.append(fila)
    filas, truncado = _recortar(filas, tope)
    return {
        "disponible": True,
        "motivo": None,
        "n": len(filas),
        "ilegibles": ilegibles,
        "filas": filas,
        "truncado": truncado,
        "top_pedido": tope,
    }


def parsear_lado(cuerpo: object, clave: str, lado: str, tope: int) -> dict:
    if not isinstance(cuerpo, dict) or clave not in cuerpo:
        return {"disponible": False, "motivo": f"{clave}_ausente", "n": None, "filas": None, "truncado": None}
    raw = cuerpo.get(clave)
    if not isinstance(raw, list):
        return {"disponible": False, "motivo": f"{clave}_ilegible", "n": None, "filas": None, "truncado": None}
    filas: list[dict] = []
    ilegibles = 0
    for item in raw:
        fila = fila_mover(item, lado)
        if fila is None:
            ilegibles += 1
        else:
            filas.append(fila)
    filas, truncado = _recortar(filas, tope)
    return {
        "disponible": True,
        "motivo": None,
        "n": len(filas),
        "ilegibles": ilegibles,
        "filas": filas,
        "truncado": truncado,
        "top_pedido": tope,
    }


def simbolos_de(bloque: dict | None) -> set[str] | None:
    """None si el bloque no está disponible: no es el conjunto vacío."""
    if not isinstance(bloque, dict) or not bloque.get("disponible"):
        return None
    filas = bloque.get("filas")
    if not isinstance(filas, list):
        return None
    return {f["symbol"] for f in filas if isinstance(f, dict) and isinstance(f.get("symbol"), str)}


def solape(screener: set[str] | None, base: set[str] | None, motivo_base: str | None) -> dict:
    """Porcentaje sobre el screener. Denominador vacío o base ausente →
    el porcentaje es null. El cero solo aparece cuando las dos bases
    existen y no comparten ningún ticker."""
    if screener is None:
        return {"n": None, "pct": None, "base_n": None, "motivo": "screener_no_disponible", "tickers": None}
    if base is None:
        return {"n": None, "pct": None, "base_n": None, "motivo": motivo_base or "base_ausente", "tickers": None}
    inter = sorted(screener & base)
    if not screener:
        return {"n": 0, "pct": None, "base_n": len(base), "motivo": "screener_vacio", "tickers": inter}
    pct = 100.0 * len(inter) / len(screener)
    return {"n": len(inter), "pct": pct, "base_n": len(base), "motivo": None, "tickers": inter}


def _union_disponible(*conjuntos: set[str] | None) -> set[str] | None:
    presentes = [c for c in conjuntos if c is not None]
    if not presentes:
        return None
    union: set[str] = set()
    for c in presentes:
        union |= c
    return union


def correr(
    *,
    cliente: ClienteDatos,
    dir_salida_path: Path,
    dir_telemetria: Path,
    dir_auditoria: Path,
    ahora: datetime | None = None,
    cargar_simbolos=tickers_universo,
    forzar: bool = False,
) -> int:
    ahora = ahora or datetime.now(UTC)
    dia = ahora.astimezone(UTC).date().isoformat()
    destino = dir_salida_path / dia / "screener.jsonl"
    if not forzar and not en_sesion(ahora):
        append_linea(destino, {
            "tipo": "screener",
            "ts": ahora.isoformat(timespec="seconds"),
            "en_sesion": False,
            "motivo": "fuera_de_sesion",
            "most_actives": None,
            "movers": None,
            "solapes": None,
            "market_cap": None,
            "motivo_market_cap": MOTIVO_MARKET_CAP,
        })
        log.info("screener sombra: fuera de sesión, no se consulta Alpaca")
        return 0

    try:
        cuerpo_activos = cliente.get(RUTA_ACTIVOS, {"by": "volume", "top": TOP_ACTIVOS})
        cuerpo_movers = cliente.get(RUTA_MOVERS, {"top": TOP_MOVERS})
    except ErrorDatos as ex:
        append_linea(destino, {
            "tipo": "screener",
            "ts": ahora.isoformat(timespec="seconds"),
            "en_sesion": True,
            "motivo": ex.codigo,
            "most_actives": None,
            "movers": None,
            "solapes": None,
            "market_cap": None,
            "motivo_market_cap": MOTIVO_MARKET_CAP,
        })
        log.warning("screener: Alpaca no respondió (%s)", ex.codigo)
        return 2

    activos = parsear_activos(cuerpo_activos, TOP_ACTIVOS)
    gainers = parsear_lado(cuerpo_movers, "gainers", "gainer", TOP_MOVERS)
    losers = parsear_lado(cuerpo_movers, "losers", "loser", TOP_MOVERS)
    set_activos = simbolos_de(activos)
    set_gainers = simbolos_de(gainers)
    set_losers = simbolos_de(losers)
    universo = _union_disponible(set_activos, set_gainers, set_losers)

    evento = ultimo_escaneo(dir_telemetria, dia)
    try:
        crudos = cargar_simbolos()
        simbolos = [s.strip().upper() for s in crudos if isinstance(s, str) and s.strip()]
    except Exception as ex:
        log.warning("universo no disponible (%s)", type(ex).__name__)
        simbolos = None
    slot, motivo_slot = tickers_del_slot(evento, simbolos)
    candidatos, motivo_aud = tickers_candidatos_auditoria(dir_auditoria, dia)
    movers, motivo_movers = tickers_movers_jsonl(dir_telemetria, dia)

    registro = {
        "tipo": "screener",
        "ts": ahora.isoformat(timespec="seconds"),
        "en_sesion": True,
        "motivo": None,
        "most_actives": activos,
        "movers": {"gainers": gainers, "losers": losers},
        "n_screener": None if universo is None else len(universo),
        "market_cap": None,
        "motivo_market_cap": MOTIVO_MARKET_CAP,
        "filtro_market_cap": False,
        "solapes": {
            "slot": solape(universo, set(slot) if slot is not None else None, motivo_slot),
            "candidatos_auditoria": solape(universo, candidatos, motivo_aud),
            "movers_jsonl": solape(universo, movers, motivo_movers),
        },
    }
    append_linea(destino, registro)
    log.info(
        "screener sombra: activos=%s gainers=%s losers=%s",
        activos.get("n"), gainers.get("n"), losers.get("n"),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sombra B2: most-actives y movers de Alpaca, solo JSONL.",
    )
    ap.add_argument("--salida", type=Path, default=None)
    ap.add_argument("--telemetria", type=Path, default=None)
    ap.add_argument("--auditoria", type=Path, default=None)
    ap.add_argument("--forzar", action="store_true",
                    help="consultar aunque la sesión regular esté cerrada")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from momentum_hunter.telemetria import DIR_TELEMETRIA

    raiz = Path(__file__).resolve().parents[1]
    try:
        cliente = ClienteDatos()
        cliente._credenciales()
    except ErrorDatos as ex:
        log.warning("screener: %s", ex.codigo)
        return 2
    return correr(
        cliente=cliente,
        dir_salida_path=dir_salida(args.salida),
        dir_telemetria=args.telemetria or DIR_TELEMETRIA,
        dir_auditoria=args.auditoria or (raiz / "momentum_hunter" / "auditoria"),
        forzar=args.forzar,
    )


if __name__ == "__main__":
    raise SystemExit(main())
