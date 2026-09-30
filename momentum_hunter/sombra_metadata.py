"""Sombra de metadata: Yahoo vs Finnhub, cinco sesiones, solo telemetría.

`python -m momentum_hunter.sombra_metadata` corre una vez al día (timer
`momentum-metadata-sombra.timer`, 13:00 UTC, NO se instala solo; lo
despliega otro agente). Pide la metadata de una muestra de tickers a
los DOS proveedores y escribe una línea JSON por ticker en
`$MOMENTUM_ESTADO_DIR/sombra_metadata/<fecha>.jsonl` con los campos de
cada uno y las diferencias. No toca la watchlist, no manda Telegram, no
cambia `MOMENTUM_METADATA_PROVIDER`.

Apagada salvo `MOMENTUM_METADATA_SOMBRA=1`. Cuando ya hay 5 archivos de
sesión, no pide nada más: imprime `SOMBRA COMPLETA` y sale 0 (el timer
puede seguir instalado sin gastar cuota).

Qué se compara: nombre, bolsa, market cap (diferencia relativa), ETF.
El float NO se compara: Finnhub no lo trae en el plan gratis y decir
"Finnhub da None y Yahoo 12M" no es una discrepancia, es la limitación
documentada en `data/finnhub_metadata.py`. Se cuenta aparte cuántos
tickers quedarían sin float con Finnhub.

Muestra: `MOMENTUM_METADATA_SOMBRA_TICKERS` (lista separada por comas)
o, si no está, los primeros `MUESTRA` símbolos del universo cacheado.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter.data.finnhub_metadata import FinnhubMetadata
from momentum_hunter.data.provider import YahooProvider
from momentum_hunter.models import Metadata
from momentum_hunter.rutas_estado import resolver

log = logging.getLogger("momentum_hunter.sombra_metadata")

ENV_ACTIVA = "MOMENTUM_METADATA_SOMBRA"
ENV_TICKERS = "MOMENTUM_METADATA_SOMBRA_TICKERS"
SESIONES = 5
MUESTRA = 40
RELATIVO = "sombra_metadata"
TOLERANCIA_CAP = 0.10


def activa() -> bool:
    return os.environ.get(ENV_ACTIVA, "0").strip() == "1"


def directorio() -> Path:
    return resolver(RELATIVO, es_dir=True)


def sesiones_hechas(d: Path) -> int:
    return len(list(d.glob("*.jsonl")))


def muestra(universo_cargar=None) -> list[str]:
    raw = os.environ.get(ENV_TICKERS, "").strip()
    if raw:
        return [t.strip().upper() for t in raw.split(",") if t.strip()]
    if universo_cargar is None:
        from momentum_hunter import universe
        universo_cargar = universe.cargar
    return [s.ticker for s in universo_cargar()][:MUESTRA]


def comparar(y: Metadata, f: Metadata) -> dict:
    """Diferencias campo a campo. Un None de un lado no es una
    discrepancia de valor: se anota como `falta_<lado>`."""
    dif: dict = {}
    for campo in ("nombre", "bolsa", "es_etf"):
        a, b = getattr(y, campo), getattr(f, campo)
        if a is None or b is None:
            if a is None:
                dif[f"{campo}_falta_yahoo"] = True
            if b is None:
                dif[f"{campo}_falta_finnhub"] = True
        elif campo == "nombre":
            if a.strip().upper()[:12] != b.strip().upper()[:12]:
                dif["nombre_distinto"] = True
        elif a != b:
            dif[f"{campo}_distinto"] = True
    cy, cf = y.market_cap, f.market_cap
    if cy is None or cf is None:
        if cy is None:
            dif["market_cap_falta_yahoo"] = True
        if cf is None:
            dif["market_cap_falta_finnhub"] = True
    elif cy > 0 and abs(cf - cy) / cy > TOLERANCIA_CAP:
        dif["market_cap_dif_rel"] = round((cf - cy) / cy, 4)
    dif["float_faltaria_con_finnhub"] = f.shares_float is None and y.shares_float is not None
    return dif


def correr(tickers: list[str], yahoo, finnhub, salida: Path, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(UTC)
    my = yahoo.metadata(tickers)
    mf = finnhub.metadata(tickers)
    fallidos_f = set(getattr(finnhub, "fallidos", []))
    resumen = {"fecha": ahora.date().isoformat(), "tickers": len(tickers), "finnhub_fallidos": len(fallidos_f),
               "con_discrepancia": 0, "float_faltaria_con_finnhub": 0}
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8") as fh:
        for t in tickers:
            y, f = my.get(t) or Metadata(t), mf.get(t) or Metadata(t)
            dif = comparar(y, f) if t not in fallidos_f else {"finnhub_fallo": True}
            if dif.get("float_faltaria_con_finnhub"):
                resumen["float_faltaria_con_finnhub"] += 1
            if any(k.endswith(("_distinto", "_dif_rel")) for k in dif):
                resumen["con_discrepancia"] += 1
            fh.write(json.dumps({"t": ahora.isoformat(timespec="seconds"), "ticker": t, "yahoo": asdict(y),
                                 "finnhub": asdict(f), "dif": dif}, ensure_ascii=False, sort_keys=True) + "\n")
    log.info("sombra metadata: %s", resumen)
    return resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=None, help="lista separada por comas (pruebas)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not activa():
        print(f"INFO: {ENV_ACTIVA} no es 1: la sombra de metadata está apagada (no-op)")
        return 0
    d = directorio()
    hechas = sesiones_hechas(d)
    if hechas >= SESIONES:
        print(f"SOMBRA COMPLETA: {hechas} sesiones en {d}; no se pide nada más")
        return 0
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else muestra()
    if not tickers:
        print("WARN: sin tickers para la muestra; no se corre")
        return 1
    hoy = datetime.now(UTC)
    salida = d / f"{hoy.date().isoformat()}.jsonl"
    if salida.exists():
        print(f"INFO: la sesión {hoy.date()} ya está; no se repite")
        return 0
    resumen = correr(tickers, YahooProvider(), FinnhubMetadata(), salida, hoy)
    print(f"sesión {hechas + 1}/{SESIONES}: {json.dumps(resumen, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
