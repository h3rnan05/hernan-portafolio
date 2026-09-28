"""Resumen en español de N sesiones de la sombra (B1 y B2).

Lee solo el JSONL que escribió este paquete. No toca la watchlist ni
recalcula catalizadores: cuenta lo que ya quedó anotado. Un valor que
no está no entra en la mediana como cero; si no hay nada que medir, el
texto dice «sin datos».
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from shadow_alpaca.historia import mediana
from shadow_alpaca.jsonl_log import dir_salida, leer_jsonl

log = logging.getLogger("shadow_alpaca.informe")


def _sesiones(directorio: Path, n: int) -> list[str]:
    if n < 1 or not directorio.is_dir():
        return []
    dias = []
    for path in sorted(directorio.iterdir()):
        if not path.is_dir():
            continue
        nombre = path.name
        if len(nombre) == 10 and nombre[4] == "-" and nombre[7] == "-":
            if (path / "noticias.jsonl").exists() or (path / "screener.jsonl").exists():
                dias.append(nombre)
    return dias[-n:]


def _ultimos_por_ticker(lineas: list[dict]) -> list[dict]:
    """La última línea de cada ticker. Una corrida repetida el mismo día
    no cuenta dos veces el mismo nombre."""
    por_ticker: dict[str, dict] = {}
    for linea in lineas:
        if linea.get("tipo") != "noticia":
            continue
        ticker = linea.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            continue
        por_ticker[ticker] = linea
    return list(por_ticker.values())


def _ambos_disponibles(linea: dict) -> bool:
    alpaca = linea.get("alpaca")
    yahoo = linea.get("yahoo")
    return (
        isinstance(alpaca, dict) and alpaca.get("disponible") is True
        and isinstance(yahoo, dict) and yahoo.get("disponible") is True
    )


def acuerdo(filas: list[dict]) -> dict:
    comparables = 0
    acuerdos = 0
    excluidos = 0
    solo_alpaca: set[str] = set()
    solo_yahoo: set[str] = set()
    for linea in filas:
        if not _ambos_disponibles(linea):
            excluidos += 1
            continue
        comparables += 1
        tipo_a = linea["alpaca"].get("catalizador")
        tipo_y = linea["yahoo"].get("catalizador")
        if tipo_a == tipo_y:
            acuerdos += 1
        if isinstance(tipo_a, str) and tipo_a and tipo_a != tipo_y:
            solo_alpaca.add(tipo_a)
        if isinstance(tipo_y, str) and tipo_y and tipo_y != tipo_a:
            solo_yahoo.add(tipo_y)
    pct = None if comparables == 0 else 100.0 * acuerdos / comparables
    return {
        "comparables": comparables,
        "acuerdos": acuerdos,
        "excluidos": excluidos,
        "pct": pct,
        "solo_alpaca": sorted(solo_alpaca),
        "solo_yahoo": sorted(solo_yahoo),
    }


def _lags(filas: list[dict], fuente: str) -> list[float]:
    out: list[float] = []
    for linea in filas:
        bloque = linea.get("lag_min_articulo_a_deteccion")
        if not isinstance(bloque, dict):
            continue
        valor = bloque.get(fuente)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            continue
        out.append(float(valor))
    return out


def _lags_entre(filas: list[dict]) -> list[float]:
    out: list[float] = []
    for linea in filas:
        valor = linea.get("lag_min_entre_fuentes")
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            continue
        out.append(float(valor))
    return out


def _pcts(lineas: list[dict], clave: str) -> list[float]:
    out: list[float] = []
    for linea in lineas:
        if linea.get("tipo") != "screener" or linea.get("en_sesion") is not True:
            continue
        solapes = linea.get("solapes")
        if not isinstance(solapes, dict):
            continue
        bloque = solapes.get(clave)
        if not isinstance(bloque, dict):
            continue
        pct = bloque.get("pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue
        out.append(float(pct))
    return out


def _fmt_pct(valor: float | None) -> str:
    if valor is None:
        return "sin datos"
    return f"{valor:.1f} %"


def _fmt_min(valor: float | None) -> str:
    if valor is None:
        return "sin datos"
    return f"{valor:.1f} min"


def _fmt_lista(valores: list[str]) -> str:
    if not valores:
        return "ninguno"
    return ", ".join(valores)


def resumir(noticias: list[dict], screeners: list[dict], sesiones: list[str]) -> str:
    filas = _ultimos_por_ticker(noticias)
    ac = acuerdo(filas)
    med_a = mediana(_lags(filas, "alpaca"))
    med_y = mediana(_lags(filas, "yahoo"))
    med_entre = mediana(_lags_entre(filas))
    med_slot = mediana(_pcts(screeners, "slot"))
    med_cand = mediana(_pcts(screeners, "candidatos_auditoria"))
    med_movers = mediana(_pcts(screeners, "movers_jsonl"))
    corridas_screener = sum(
        1 for s in screeners if s.get("tipo") == "screener" and s.get("en_sesion") is True
    )
    fuera = sum(1 for s in screeners if s.get("motivo") == "fuera_de_sesion")
    if sesiones:
        rango = f"{sesiones[0]} a {sesiones[-1]}" if len(sesiones) > 1 else sesiones[0]
        titulo = f"Informe sombra Alpaca — {len(sesiones)} sesión(es) ({rango})"
    else:
        titulo = "Informe sombra Alpaca — no hay sesiones para resumir"
    desacuerdos = ac["comparables"] - ac["acuerdos"]
    lineas = [
        titulo,
        "",
        "Noticias (B1)",
        f"  Tickers con registro: {len(filas)}",
        f"  Comparables (Alpaca y Yahoo disponibles): {ac['comparables']}",
        f"  Acuerdo de catalizador: {_fmt_pct(ac['pct'])}"
        + (f" ({ac['acuerdos']}/{ac['comparables']})" if ac["comparables"] else ""),
        f"  Sin acuerdo: {desacuerdos if ac['comparables'] else 'sin datos'}",
        f"  Excluidos porque un lado no estaba disponible: {ac['excluidos']}",
        f"  Catalizadores solo en Alpaca: {_fmt_lista(ac['solo_alpaca'])}",
        f"  Catalizadores solo en Yahoo: {_fmt_lista(ac['solo_yahoo'])}",
        f"  Mediana del retraso artículo→detección, Alpaca: {_fmt_min(med_a)}",
        f"  Mediana del retraso artículo→detección, Yahoo: {_fmt_min(med_y)}",
        f"  Mediana del retraso entre fuentes: {_fmt_min(med_entre)}",
        "",
        "Screener (B2)",
        f"  Corridas dentro de sesión: {corridas_screener}",
        f"  Corridas fuera de sesión (no consultan Alpaca): {fuera}",
        f"  Mediana del solape con el slot del escaneo: {_fmt_pct(med_slot)}",
        f"  Mediana del solape con los candidatos de la auditoría: {_fmt_pct(med_cand)}",
        f"  Mediana del solape con movers.jsonl: {_fmt_pct(med_movers)}",
        "  Market cap: el screener no lo trae. Queda null "
        "(alpaca_screener_no_incluye_market_cap). No se filtra y no se sustituye por 0.",
        "",
        "Esto no alimenta señales, la watchlist ni órdenes. "
        "Un lado en pausa de Yahoo (429) se contó como no disponible, no como cero titulares.",
    ]
    return "\n".join(lineas) + "\n"


def cargar(directorio: Path, n: int) -> tuple[list[str], list[dict], list[dict]]:
    sesiones = _sesiones(directorio, n)
    noticias: list[dict] = []
    screeners: list[dict] = []
    for dia in sesiones:
        noticias.extend(leer_jsonl(directorio / dia / "noticias.jsonl"))
        screeners.extend(leer_jsonl(directorio / dia / "screener.jsonl"))
    return sesiones, noticias, screeners


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resumen en español de N sesiones de la sombra B1/B2.")
    ap.add_argument("--sesiones", type=int, default=5, help="cuántas sesiones (días con JSONL) tomar, las más recientes")
    ap.add_argument("--salida", type=Path, default=None, help="directorio raíz de la sombra")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.sesiones < 1:
        log.warning("informe: --sesiones tiene que ser >= 1")
        return 2
    sesiones, noticias, screeners = cargar(dir_salida(args.salida), args.sesiones)
    print(resumir(noticias, screeners, sesiones), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
