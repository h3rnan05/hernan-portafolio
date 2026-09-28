"""Reporte de cierre con las subastas oficiales (después de las 16:00 ET).

POR QUÉ EXISTE. El resumen de fin de día del ejecutor
(`notify.formatear_cierre_dia`) sale a las 15:50 ET, cuando se liquida.
La subasta de cierre todavía no ocurrió a esa hora, así que ningún
reporte del sistema conocía el cierre OFICIAL ni la apertura oficial de
las señales del día. Este módulo corre después del cierre y, para cada
señal disparada hoy y cada trade de hoy, pone lado a lado:

  - apertura y cierre oficiales (subastas SIP, `momentum_hunter.data.subastas`);
  - gap oficial vs. el gap que usó el hunter al disparar;
  - para los trades: salida real vs. cierre oficial. Es la diferencia
    entre liquidar a las 15:50 y aguantar hasta el cierre.

Es solo lectura e informativo. No decide nada, no cambia ningún límite
y no toca órdenes: no importa el cliente del bróker. Una cifra que
falta se escribe "sin dato", nunca 0.

Salida fuera de git: `/var/lib/momentum/reportes_cierre/AAAA-MM-DD.json`
y `.txt` (otra carpeta con `MOMENTUM_REPORTE_CIERRE_DIR`). Con
`--telegram` también manda el texto; sin esa bandera no avisa nada.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum_hunter import watchlist
from momentum_hunter.data import subastas
from momentum_paper_trader import estado

log = logging.getLogger("momentum_paper_trader.reporte_cierre")

NY = ZoneInfo("America/New_York")
MTY = ZoneInfo("America/Monterrey")
ENV_DIR = "MOMENTUM_REPORTE_CIERRE_DIR"
DIR_DEFAULT = Path("/var/lib/momentum/reportes_cierre")


@dataclass
class FilaReporte:
    ticker: str
    origen: str                      # "señal" | "trade" | "señal+trade"
    apertura_oficial: float | None
    cierre_oficial: float | None
    cierre_previo_oficial: float | None
    gap_oficial: float | None
    gap_hunter: float | None         # el que usó el hunter (congelado al disparar)
    movimiento_dia: float | None     # cierre oficial / apertura oficial - 1
    cantidad: int | None = None
    precio_entrada: float | None = None
    precio_salida: float | None = None
    resultado: str | None = None
    pnl: float | None = None
    # (cierre oficial - salida) x cantidad. Positivo: aguantar hasta el
    # cierre habría dado más. Solo con los tres números reales.
    diferencia_vs_cierre: float | None = None


def _fecha_ny(ahora: datetime) -> str:
    return ahora.astimezone(NY).date().isoformat()


def _es_de_hoy(ts: str | None, hoy_ny: str) -> bool:
    if not isinstance(ts, str) or not ts:
        return False
    try:
        momento = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(NY).date().isoformat() == hoy_ny


def construir(
    entradas: list, revisiones: list, dias_por_ticker: dict[str, dict], hoy: str,
) -> list[FilaReporte]:
    """Núcleo puro: todo inyectado, sin red ni disco."""
    senales = {e.ticker: e for e in entradas if _es_de_hoy(getattr(e, "market_event_ts", None), hoy)}
    trades = {r.ticker: r for r in revisiones
              if r.entro and r.order_id and _es_de_hoy(r.timestamp, hoy)}
    filas: list[FilaReporte] = []
    for t in sorted(set(senales) | set(trades)):
        dias = dias_por_ticker.get(t, {})
        d = dias.get(hoy)
        previas = sorted(f for f in dias if f < hoy)
        previo = dias[previas[-1]] if previas else None
        ap = d.apertura if d else None
        ci = d.cierre if d else None
        mov = (ci / ap - 1) if ap and ci else None
        e = senales.get(t)
        r = trades.get(t)
        origen = "señal+trade" if e and r else ("trade" if r else "señal")
        fila = FilaReporte(
            ticker=t, origen=origen,
            apertura_oficial=ap, cierre_oficial=ci,
            cierre_previo_oficial=previo.cierre if previo else None,
            gap_oficial=subastas.gap_oficial(dias, hoy),
            gap_hunter=getattr(e, "gap_pct_congelado", None) if e else None,
            movimiento_dia=mov,
        )
        if r is not None:
            fila.cantidad = r.cantidad
            fila.precio_entrada = r.precio_entrada
            fila.precio_salida = r.precio_salida
            fila.resultado = r.resultado
            fila.pnl = r.pnl
            if ci is not None and r.precio_salida is not None and r.cantidad:
                fila.diferencia_vs_cierre = round((ci - r.precio_salida) * r.cantidad, 2)
        filas.append(fila)
    return filas


def _p(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "sin dato"


def _pct(v: float | None) -> str:
    return f"{v:+.2%}" if v is not None else "sin dato"


def _usd(v: float | None) -> str:
    return f"{'+' if v >= 0 else '−'}${abs(v):,.2f}" if v is not None else "sin dato"


def formatear(filas: list[FilaReporte], hoy: str, ahora: datetime) -> str:
    utc = ahora.astimezone(UTC).strftime("%H:%M")
    mty = ahora.astimezone(MTY).strftime("%H:%M")
    cab = f"Reporte de cierre {hoy} — generado {utc} UTC / {mty} Monterrey (UTC−6)"
    if not filas:
        return cab + "\nHoy no hubo señales disparadas ni trades."
    lineas = [cab]
    for f in filas:
        lineas.append(f"\n{f.ticker} ({f.origen})")
        lineas.append(f"  apertura oficial {_p(f.apertura_oficial)} · cierre oficial {_p(f.cierre_oficial)} "
                      f"· día {_pct(f.movimiento_dia)}")
        lineas.append(f"  gap oficial {_pct(f.gap_oficial)} · gap que usó el hunter {_pct(f.gap_hunter)}")
        if f.origen != "señal":
            lineas.append(f"  {f.cantidad or 'sin dato'} acc · entrada {_p(f.precio_entrada)} · salida "
                          f"{_p(f.precio_salida)} ({f.resultado or 'sin resultado'}) · P&L {_usd(f.pnl)}")
            lineas.append(f"  aguantar hasta el cierre oficial habría dado {_usd(f.diferencia_vs_cierre)} más")
    if any(f.cierre_oficial is None for f in filas):
        lineas.append("\n(\"sin dato\" en el cierre: la subasta todavía no se publicó o el feed no respondió; "
                      "no se reemplaza por el último trade.)")
    return "\n".join(lineas)


def _dir_salida() -> Path | None:
    crudo = os.environ.get(ENV_DIR, "").strip()
    if crudo:
        return Path(crudo)
    return DIR_DEFAULT if DIR_DEFAULT.parent.is_dir() else None


def guardar(filas: list[FilaReporte], texto: str, hoy: str) -> Path | None:
    carpeta = _dir_salida()
    if carpeta is None:
        return None
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"{hoy}.json").write_text(
            json.dumps({"fecha": hoy, "filas": [asdict(f) for f in filas]}, ensure_ascii=False, indent=2))
        (carpeta / f"{hoy}.txt").write_text(texto + "\n")
    except OSError as ex:
        log.warning("reporte de cierre: no se pudo escribir (%s)", type(ex).__name__)
        return None
    return carpeta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--telegram", action="store_true", help="además del archivo, manda el texto por Telegram")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    ahora = datetime.now(UTC)
    hoy = _fecha_ny(ahora)
    entradas = (watchlist.cargar(apply_vps_state=True) if watchlist.vps_state_habilitado()
                else watchlist.cargar())
    revisiones = estado.cargar()
    tickers = sorted(
        {e.ticker for e in entradas if _es_de_hoy(getattr(e, "market_event_ts", None), hoy)}
        | {r.ticker for r in revisiones if r.entro and r.order_id and _es_de_hoy(r.timestamp, hoy)})
    dias = subastas.descargar(tickers, ahora) if tickers else {}
    filas = construir(entradas, revisiones, dias, hoy)
    texto = formatear(filas, hoy, ahora)
    print(texto)
    carpeta = guardar(filas, texto, hoy)
    if carpeta is not None:
        log.info("reporte de cierre escrito en %s", carpeta)
    if args.telegram and filas:
        from momentum_paper_trader import notify
        notify.enviar(notify.escapar(texto))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
