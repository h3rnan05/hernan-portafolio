"""¿Tiene que actuar GitHub como RESPALDO del VPS? (2026-09-21)

Desde hoy el escaneo completo y el rechequeo corren en el VPS. Los
workflows de GitHub conservan su cron, pero solo actúan si el VPS está
callado. Regla (decisión del dueño):

  respaldo = han pasado >= 20 min desde el inicio de la ventana del timer
             (13:00 UTC) Y el VPS lleva > 20 min sin commitear telemetría
             de hoy.

La "voz" del VPS es la telemetría que él mismo commitea cada 5 min:
`momentum_paper_trader/telemetria/<hoy>/vps/events.jsonl` (el rechequeo) y
`momentum_hunter/telemetria/<hoy>/vps/events.jsonl` (el escaneo). Se lee
del checkout, sin git log ni secrets. Un archivo ilegible o sin
timestamps cuenta como silencio: ante la duda, GitHub respalda (un
escaneo de más es barato; una sesión sin escaneos no).

Uso: python .github/scripts/respaldo_gha.py  → imprime `respaldo=true|false`
y la razón; con GITHUB_OUTPUT definido escribe ahí `respaldo=...`.
`--forzar` devuelve true siempre (workflow_dispatch manual)."""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

INICIO_VENTANA_UTC = time(13, 0)
GRACIA = timedelta(minutes=20)
SILENCIO = timedelta(minutes=20)
RUTAS = (
    Path("momentum_paper_trader/telemetria"),
    Path("momentum_hunter/telemetria"),
)


def _parse_ts(valor) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=UTC)


def ultimo_latido_vps(ahora: datetime, raices=RUTAS) -> datetime | None:
    """Timestamp más reciente de la telemetría del VPS de hoy (fecha UTC)."""
    fecha = ahora.astimezone(UTC).date().isoformat()
    ultimo = None
    for raiz in raices:
        ruta = Path(raiz) / fecha / "vps" / "events.jsonl"
        try:
            lineas = ruta.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for linea in lineas:
            try:
                r = json.loads(linea)
            except ValueError:
                continue
            ts = _parse_ts(r.get("timestamp")) if isinstance(r, dict) else None
            if ts is not None and (ultimo is None or ts > ultimo):
                ultimo = ts
    return ultimo


def decidir(ahora: datetime, latido: datetime | None, forzar: bool = False) -> tuple[bool, str]:
    if forzar:
        return True, "forzado a mano (workflow_dispatch)"
    ahora_utc = ahora.astimezone(UTC)
    apertura = datetime.combine(ahora_utc.date(), INICIO_VENTANA_UTC, tzinfo=UTC)
    if ahora_utc < apertura + GRACIA:
        return False, f"faltan {int((apertura + GRACIA - ahora_utc).total_seconds() // 60)} min de gracia desde las 13:00 UTC"
    if latido is None:
        return True, "el VPS no dejó telemetría hoy"
    silencio = ahora_utc - latido
    if silencio > SILENCIO:
        return True, f"el VPS lleva {int(silencio.total_seconds() // 60)} min sin commitear (último latido {latido:%H:%M} UTC)"
    return False, f"el VPS está vivo (último latido {latido:%H:%M} UTC, hace {int(silencio.total_seconds() // 60)} min)"


def main(argv: list[str]) -> int:
    forzar = "--forzar" in argv or os.environ.get("MOMENTUM_RESPALDO_FORZAR", "").strip().lower() in {"1", "true", "yes", "on"}
    ahora = datetime.now(UTC)
    respaldo, razon = decidir(ahora, ultimo_latido_vps(ahora), forzar)
    print(f"respaldo={'true' if respaldo else 'false'}  ({razon})")
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as fh:
            fh.write(f"respaldo={'true' if respaldo else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
