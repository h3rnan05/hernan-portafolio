"""Refresco diario del calendario de sesión. Solo el lado paper.

Lee `GET /v2/calendar` (un rango ancho: hoy−7 a hoy+400) y `GET /v2/clock`
del host PAPER y escribe el JSON que el resto del sistema solo lee. Si
Alpaca no responde, el archivo anterior se queda como está: un refresco
fallido no borra el último calendario bueno.

El hunter no importa este módulo. El endpoint es el de
`alpaca_client._BASE_URL`, hardcodeado a paper: este job no puede
apuntar a la cuenta real por una variable de entorno.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from momentum_hunter import calendario

from momentum_paper_trader.alpaca_client import AlpacaPaperClient

log = logging.getLogger("momentum_paper_trader.calendario_job")

DIAS_ATRAS = 7
DIAS_ADELANTE = 400


def _filas(crudo: list) -> dict[str, dict[str, str]]:
    """Una fila sin fecha u hora no entra. No se rellena con ceros."""
    dias: dict[str, dict[str, str]] = {}
    for fila in crudo:
        if not isinstance(fila, dict):
            continue
        fecha = fila.get("date")
        abre = fila.get("open")
        cierra = fila.get("close")
        if not isinstance(fecha, str) or calendario._parse_fecha(fecha) is None:
            continue
        if calendario._parse_hora(abre) is None or calendario._parse_hora(cierra) is None:
            continue
        if not isinstance(abre, str) or not isinstance(cierra, str):
            continue
        dias[fecha[:10]] = {"open": abre.strip(), "close": cierra.strip()}
    return dias


def _reloj(crudo: dict) -> dict:
    if not isinstance(crudo, dict):
        raise ValueError("reloj")
    out: dict = {}
    for clave in ("timestamp", "is_open", "next_open", "next_close"):
        if clave in crudo and crudo[clave] is not None:
            out[clave] = crudo[clave]
    return out


def refrescar(client: AlpacaPaperClient, path, ahora: datetime | None = None) -> bool:
    """True si escribió el archivo. False si Alpaca falló: no lo toca."""
    ahora = ahora or datetime.now(UTC)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=UTC)
    hoy = ahora.astimezone(calendario.NY).date()
    inicio = (hoy - timedelta(days=DIAS_ATRAS)).isoformat()
    fin = (hoy + timedelta(days=DIAS_ADELANTE)).isoformat()
    try:
        crudo = client.calendario(inicio, fin)
        reloj = client.reloj_mercado()
        if not isinstance(crudo, list):
            raise ValueError("calendario")
        dias = _filas(crudo)
        if not dias:
            raise ValueError("calendario_vacio")
        clock = _reloj(reloj)
    except Exception as ex:
        # Solo el tipo: el texto puede traer una URL.
        log.warning("calendario: no se pudo refrescar (%s); se conserva el archivo", type(ex).__name__)
        return False
    calendario.guardar(path, {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rango": {"desde": inicio, "hasta": fin},
        "clock": clock,
        "dias": dias,
    })
    log.info("calendario: %d sesiones, %s → %s", len(dias), inicio, fin)
    return True


def refrescar_desde_entorno(ahora: datetime | None = None) -> bool:
    """No hace nada de red si faltan las credenciales paper (fail-closed)."""
    api_key = os.getenv("ALPACA_PAPER_API_KEY")
    api_secret = os.getenv("ALPACA_PAPER_API_SECRET")
    if not api_key or not api_secret:
        log.info("calendario: sin credenciales paper, no se refresca")
        return False
    return refrescar(AlpacaPaperClient(api_key, api_secret), calendario.ruta(), ahora)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return 0 if refrescar_desde_entorno() else 1


if __name__ == "__main__":
    raise SystemExit(main())
