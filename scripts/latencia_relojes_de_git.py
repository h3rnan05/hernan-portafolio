#!/usr/bin/env python3
"""Reconstruye del historial de git los relojes del hunter para
`python -m momentum_paper_trader.latencia_e2e --relojes SALIDA.json`
cuando la entrada ya se purgó de la watchlist (retención de 7 días).

Por cada orden de revisiones.json, busca en el historial git
de watchlist.json la entrada TRIGGERED y copia sus relojes."""
import json, subprocess, sys
from datetime import datetime, timedelta
revs = json.load(open("momentum_paper_trader/revisiones.json"))["revisiones"]
ords = [r for r in revs if r.get("entro") and r.get("order_id") and r.get("market_event_ts")]
out = {}
for r in ords:
    ts = datetime.fromisoformat(r["timestamp"])
    commits = subprocess.run(["git", "log", "--format=%H", f"--since={(ts - timedelta(minutes=5)).isoformat()}",
                              f"--until={(ts + timedelta(hours=6)).isoformat()}", "--reverse", "origin/main",
                              "--", "momentum_hunter/watchlist.json"], capture_output=True, text=True).stdout.split()
    hallado = None
    for c in commits[:60]:
        try:
            wl = json.loads(subprocess.run(["git", "show", f"{c}:momentum_hunter/watchlist.json"],
                                           capture_output=True, text=True).stdout)
        except json.JSONDecodeError:
            continue
        for e in wl.get("entradas", []):
            if e.get("ticker") == r["ticker"] and e.get("creado_en") == r["creado_en"] and e.get("evaluador_ts"):
                hallado = {k: e.get(k) for k in ("market_event_ts", "data_received_ts", "evaluador_ts",
                                                 "mensaje_generado_ts", "telegram_enviado_ts",
                                                 "watchlist_escrito_ts", "velas_desde_ruptura", "estado")}
                break
        if hallado:
            break
    out[f"{r['ticker']}|{r['creado_en']}"] = hallado
    print(r["ticker"], r["timestamp"], "OK" if hallado else "no encontrado", len(commits), file=sys.stderr)
json.dump(out, open(sys.argv[1], "w"), indent=1)  # uso: scripts/latencia_relojes_de_git.py SALIDA.json
