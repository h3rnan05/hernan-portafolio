"""Confirmación de fin de día -- pedido explícito del dueño del producto
(2026-07-27, justo después de configurar el bot en Telegram): "antes de
acabar el día, que si no encontró nada, me escriba y me diga 'Hoy no
hubo nada, bonito día' -- mínimo para saber que sí está funcionando".

Distinto del silencio de `alerts.py`/`radar.py` durante el día (correcto
seguir en silencio en cada corrida sin alertas -- eso sí sería ruido,
Prompt 1/2 del pivote de momentum): esto es UN mensaje, una sola vez,
cerca del cierre del mercado, solo en los días donde no se mandó
NINGUNA alerta. Confirma que el bot corrió y decidió activamente que no
había nada bueno -- no que se cayó y por eso no avisó.

Estado persistido en un JSON chico (mismo patrón que `tracker.py`) para
no mandar el mensaje más de una vez el mismo día, sin importar cuántas
corridas del cron caigan dentro de la ventana de cierre."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

PATH = Path(__file__).resolve().parent / "estado_diario.json"

# ~20:00 UTC -- cerca del cierre de la sesión regular (mismo horario que
# ya asume `report._HORA_CIERRE_UTC` y `factors/intradia.py`). Basta con
# que UNA corrida del cron caiga en o después de esta hora.
HORA_UTC_CIERRE_RESUMEN = 19.9

MENSAJE_SIN_ALERTAS = (
    "👋 Hoy no encontré ninguna oportunidad que mereciera arriesgar tu dinero. "
    "Sin alertas no es que esté roto -- es que hoy decidí que nada valía la pena. "
    "Bonito día."
)


@dataclass
class EstadoDiario:
    fecha: str
    resumen_enviado: bool = False


def cargar_estado(path: Path = PATH) -> EstadoDiario | None:
    if not path.exists():
        return None
    try:
        return EstadoDiario(**json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def guardar_estado(estado: EstadoDiario, path: Path = PATH) -> None:
    path.write_text(json.dumps(asdict(estado), ensure_ascii=False))


def necesita_resumen_cierre(
    hoy: date, hora_utc: float, alertas_hoy: int, estado: EstadoDiario | None,
    hora_cierre: float = HORA_UTC_CIERRE_RESUMEN,
) -> bool:
    """True solo si: ya está cerca el cierre, hoy no se mandó ninguna
    alerta todavía, y este resumen no se mandó ya hoy."""
    if hora_utc < hora_cierre:
        return False
    if alertas_hoy > 0:
        return False
    if estado is not None and estado.fecha == hoy.isoformat() and estado.resumen_enviado:
        return False
    return True


def registrar_enviado(hoy: date, path: Path = PATH) -> None:
    guardar_estado(EstadoDiario(fecha=hoy.isoformat(), resumen_enviado=True), path)
