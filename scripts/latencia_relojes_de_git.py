#!/usr/bin/env python3
"""Reconstruye del historial de git los relojes del hunter para
`python -m momentum_paper_trader.latencia_e2e --relojes SALIDA.json`
cuando la entrada ya se purgó de la watchlist (retención de 7 días).

Por cada orden de `revisiones.json`, recorre las versiones de
`momentum_hunter/watchlist.json` en `origin/main` desde 5 min antes de
la orden hasta 6 h después y copia los relojes de la primera que trae
esa entrada ya evaluada. Solo lee git; no escribe nada más que SALIDA.

Uso: python scripts/latencia_relojes_de_git.py SALIDA.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta

CAMPOS = ("market_event_ts", "data_received_ts", "evaluador_ts", "mensaje_generado_ts",
          "telegram_enviado_ts", "watchlist_escrito_ts", "velas_desde_ruptura", "estado")
MAX_VERSIONES = 60


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def relojes_de(revision: dict) -> dict | None:
    ts = datetime.fromisoformat(revision["timestamp"])
    commits = _git("log", "--format=%H", f"--since={(ts - timedelta(minutes=5)).isoformat()}",
                   f"--until={(ts + timedelta(hours=6)).isoformat()}", "--reverse", "origin/main",
                   "--", "momentum_hunter/watchlist.json").split()
    for c in commits[:MAX_VERSIONES]:
        try:
            wl = json.loads(_git("show", f"{c}:momentum_hunter/watchlist.json"))
        except json.JSONDecodeError:
            continue
        for e in wl.get("entradas", []):
            if (e.get("ticker") == revision["ticker"] and e.get("creado_en") == revision["creado_en"]
                    and e.get("evaluador_ts")):
                return {k: e.get(k) for k in CAMPOS}
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    revisiones = json.load(open("momentum_paper_trader/revisiones.json"))["revisiones"]
    ordenes = [r for r in revisiones if r.get("entro") and r.get("order_id") and r.get("market_event_ts")]
    out = {}
    for r in ordenes:
        hallado = relojes_de(r)
        out[f"{r['ticker']}|{r['creado_en']}"] = hallado
        print(r["ticker"], r["timestamp"], "ok" if hallado else "no encontrado", file=sys.stderr)
    with open(sys.argv[1], "w") as fh:
        json.dump(out, fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
