"""Registro de eventos para el panel.

Cada llamada agrega una línea JSON a $DASH_EVENTOS (en el VPS:
/var/lib/momentum/events.jsonl, fuera de git; por defecto logs/events.jsonl). Es solo observabilidad:
si falla la escritura, se ignora y el bot sigue exactamente igual.

Tipos que el panel entiende:
    rechequeo       n_tickers=...                      (cada corrida del VPS)
    deteccion       ticker=...                         (el ejecutor ve la señal por primera vez)
    decision        ticker=..., entra=bool, motivo=... (respuesta del LLM)
    orden           ticker=..., lado=..., estado="enviada"|"rechazada",
                    velas=... + medida="ruptura_a_orden" (latencia completa; None si no se midió)
    bloqueo_riesgo  ticker=..., limite=..., motivo=...
    persist_fallido motivo=..., intentos=...            (el VPS no pudo subir su estado a main)

Desde bash (scripts/run_watchlist_paper.sh) se usa la CLI:
    python -m dashboard.events persist_fallido motivo="git persist failed" intentos=5
Tampoco falla nunca: sale con 0 pase lo que pase, para que un `set -e`
del script que la llama no convierta un aviso en una caída del bot.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def ruta_eventos() -> Path:
    return Path(os.environ.get("DASH_EVENTOS", "logs/events.jsonl"))


def log_event(tipo: str, **campos) -> None:
    """Nunca lanza. Cualquier falla (disco, permisos, un campo raro, lo que
    sea) se traga: el panel es secundario y jamás puede afectar una orden."""
    try:
        registro = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tipo": tipo,
            **campos,
        }
        ruta = ruta_eventos()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _campos_cli(argv: list[str]) -> dict:
    """`k=v` → str. Un número se guarda como número para que el panel lo
    pueda contar; cualquier otra cosa queda como texto tal cual."""
    campos = {}
    for arg in argv:
        if "=" not in arg:
            continue
        k, v = arg.split("=", 1)
        try:
            campos[k] = int(v)
        except ValueError:
            campos[k] = v
    return campos


def main(argv: list[str] | None = None) -> int:
    """`python -m dashboard.events <tipo> [k=v ...]`. Siempre devuelve 0."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args and args[0].strip():
            log_event(args[0].strip(), **_campos_cli(args[1:]))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
