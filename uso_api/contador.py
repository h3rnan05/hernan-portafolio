"""Consultas por minuto a Alpaca, separadas por host, fuera de git.

LÍMITES DEL PLAN (por cuenta, por minuto):
  - datos   (`data.alpaca.markets`, Algo Trader Plus): 10.000
  - trading (`paper-api.alpaca.markets`):                200
Al llegar al 70 % de cualquiera de los dos en un minuto se avisa por
Telegram, como máximo una vez cada 15 min por host (un minuto saturado
no manda 3.000 mensajes).

CÓMO CUENTA. Cada intento HTTP es una consulta (un reintento tras un 429
también gasta cupo), así que `registrar` se llama justo antes de cada
`requests.*` / `urlopen`. Varios procesos cuentan a la vez (vigía,
escaneo, panel), así que el archivo del día se actualiza con `flock`:
`AAAA-MM-DD.json` = {"datos": {"HH:MM": n}, "trading": {...}, "avisos": {...}},
con el minuto en UTC.

LO QUE NO VE (anotado, no maquillado):
  - Solo los procesos de esta máquina. Una corrida de respaldo en GitHub
    Actions gasta el mismo cupo de la cuenta y aquí no aparece (no hay
    directorio de estado ahí, así que ni lo intenta).
  - Los websockets (trade_updates, stream SIP) no son consultas REST y
    tienen su propio límite de conexiones.
  - Fuera de main todavía: las ramas de sombra (#196, #198) usan su
    propio cliente; cuando entren, deben llamar a `registrar` también.

NUNCA ROMPE UNA CONSULTA. Cualquier error al contar se traga (debug):
medir no puede costar una orden ni una vela. Sin directorio de estado
(pruebas, GitHub) no hace nada.

Dónde: `MOMENTUM_USO_API_DIR`, o `/var/lib/momentum/uso_api` si
`/var/lib/momentum` existe (el VPS).

Informe: `python -m uso_api.contador [AAAA-MM-DD]`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("uso_api")

DATOS = "datos"
TRADING = "trading"
LIMITES = {DATOS: 10_000, TRADING: 200}
UMBRAL_AVISO = 0.70
MINUTOS_ENTRE_AVISOS = 15

ENV_DIR = "MOMENTUM_USO_API_DIR"
DIR_DEFAULT = Path("/var/lib/momentum/uso_api")
MTY = ZoneInfo("America/Monterrey")


def _dir() -> Path | None:
    crudo = os.environ.get(ENV_DIR, "").strip()
    if crudo:
        return Path(crudo)
    if "PYTEST_CURRENT_TEST" in os.environ:
        # Una suite corrida en el VPS no debe sumar sus consultas falsas
        # al registro real. Las pruebas de este módulo fijan ENV_DIR.
        return None
    return DIR_DEFAULT if DIR_DEFAULT.parent.is_dir() else None


def _minuto(ahora: datetime) -> str:
    return ahora.astimezone(UTC).strftime("%H:%M")


def _vacio() -> dict:
    return {DATOS: {}, TRADING: {}, "avisos": {}}


def _leer(ruta: Path) -> dict:
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _vacio()
    if not isinstance(data, dict):
        return _vacio()
    for k in (DATOS, TRADING, "avisos"):
        if not isinstance(data.get(k), dict):
            data[k] = {}
    return data


def _incrementar(carpeta: Path, host: str, ahora: datetime) -> tuple[int, bool]:
    """(consultas en este minuto, ¿toca avisar?) bajo un lock de archivo
    compartido por todos los procesos."""
    import fcntl

    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{ahora.astimezone(UTC).date().isoformat()}.json"
    with open(carpeta / ".lock", "a+") as candado:
        fcntl.flock(candado, fcntl.LOCK_EX)
        try:
            data = _leer(ruta)
            minuto = _minuto(ahora)
            n = int(data[host].get(minuto, 0)) + 1
            data[host][minuto] = n
            avisar = False
            if n >= LIMITES[host] * UMBRAL_AVISO:
                ultimo = data["avisos"].get(host)
                try:
                    lejos = (ultimo is None or ahora - datetime.fromisoformat(ultimo)
                             >= timedelta(minutes=MINUTOS_ENTRE_AVISOS))
                except (TypeError, ValueError):
                    lejos = True
                if lejos:
                    data["avisos"][host] = ahora.astimezone(UTC).isoformat(timespec="seconds")
                    avisar = True
            temporal = ruta.with_suffix(".json.tmp")
            temporal.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            temporal.replace(ruta)
        finally:
            fcntl.flock(candado, fcntl.LOCK_UN)
    return n, avisar


def texto_aviso(host: str, n: int, ahora: datetime) -> str:
    limite = LIMITES[host]
    utc = ahora.astimezone(UTC).strftime("%H:%M")
    mty = ahora.astimezone(MTY).strftime("%H:%M")
    return (f"⚠️ Uso de la API de Alpaca ({host}): {n:,} consultas en el minuto {utc} UTC / "
            f"{mty} Monterrey (UTC−6) = {n / limite:.0%} del límite de {limite:,}/min. "
            f"Cuenta solo lo que corre en el VPS.").replace(",", ".")


def _avisar(texto: str) -> None:
    """Telegram directo, sin importar al hunter ni al ejecutor. Mismas
    variables que el resto del sistema. Nunca levanta."""
    token = os.getenv("MOMENTUM_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("MOMENTUM_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("uso_api: sin Telegram configurado; aviso solo en el log: %s", texto)
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": texto}, timeout=10)
    except Exception as ex:   # noqa: BLE001 -- el TIPO, nunca el texto (trae la URL con el token)
        log.warning("uso_api: el aviso por Telegram falló (%s)", type(ex).__name__)


def registrar(host: str, ahora: datetime | None = None) -> None:
    """Una consulta a `host` ("datos" | "trading"). Nunca levanta."""
    try:
        if host not in LIMITES:
            return
        carpeta = _dir()
        if carpeta is None:
            return
        ahora = ahora or datetime.now(UTC)
        n, avisar = _incrementar(carpeta, host, ahora)
        if avisar:
            texto = texto_aviso(host, n, ahora)
            log.warning("uso_api: %s", texto)
            _avisar(texto)
    except Exception as ex:   # noqa: BLE001 -- medir nunca rompe la consulta
        log.debug("uso_api: no se pudo contar (%s)", type(ex).__name__)


# --------------------------------------------------------------- informe


def resumen(dia: str, carpeta: Path | None = None) -> dict:
    carpeta = carpeta or _dir()
    if carpeta is None:
        return {}
    ruta = carpeta / f"{dia}.json"
    if not ruta.exists():
        return {}
    data = _leer(ruta)
    out = {}
    for host, limite in LIMITES.items():
        por_minuto = {m: int(n) for m, n in data[host].items()}
        if not por_minuto:
            out[host] = {"total": 0, "minutos_con_uso": 0, "pico": None, "pico_minuto": None,
                         "pico_pct": None, "minutos_sobre_70": 0}
            continue
        pico_minuto = max(por_minuto, key=lambda m: (por_minuto[m], m))
        pico = por_minuto[pico_minuto]
        out[host] = {
            "total": sum(por_minuto.values()),
            "minutos_con_uso": len(por_minuto),
            "pico": pico,
            "pico_minuto": pico_minuto,
            "pico_pct": round(pico / limite, 4),
            "minutos_sobre_70": sum(1 for n in por_minuto.values() if n >= limite * UMBRAL_AVISO),
        }
    out["avisos"] = data.get("avisos", {})
    return out


def formatear(dia: str, r: dict) -> str:
    if not r:
        return f"{dia}: sin registro de uso (¿existe {ENV_DIR} o /var/lib/momentum?)."
    lineas = [f"Uso de la API de Alpaca, {dia} (minutos en UTC):"]
    for host, limite in LIMITES.items():
        h = r.get(host) or {}
        if not h.get("total"):
            lineas.append(f"  {host}: sin consultas registradas")
            continue
        lineas.append(
            f"  {host}: {h['total']} consultas en {h['minutos_con_uso']} min · pico {h['pico']}/{limite} "
            f"({h['pico_pct']:.1%}) a las {h['pico_minuto']} · minutos ≥70 %: {h['minutos_sobre_70']}")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    dia = args[0] if args else datetime.now(UTC).date().isoformat()
    print(formatear(dia, resumen(dia)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
