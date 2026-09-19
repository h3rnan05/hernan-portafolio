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
