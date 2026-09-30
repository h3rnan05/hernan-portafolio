"""Zonas horarias y formato de horas del paquete.

Todo dato con hora se guarda aware en UTC. Nueva York es la zona de las
fuentes (EDGAR, bolsas, Fed, BLS). Monterrey es UTC−6 fijo: Nuevo León
ya no cambia de hora, y por eso aquí no se usa `America/Monterrey` de
la base tz (según la versión trae o no el horario de verano viejo).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MTY = timezone(timedelta(hours=-6), "Monterrey")


def a_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("hora naive: hay que decir de qué zona es antes de convertir")
    return dt.astimezone(UTC)


def ny(d: date, h: time) -> datetime:
    """Fecha + hora de Nueva York -> aware en UTC."""
    return datetime.combine(d, h, tzinfo=NY).astimezone(UTC)


def fecha_ny(dt: datetime) -> date:
    return a_utc(dt).astimezone(NY).date()


def fmt_utc_mty(dt: datetime) -> str:
    """'2026-09-25 20:05 UTC (14:05 Monterrey, UTC−6)'."""
    u = a_utc(dt)
    m = u.astimezone(MTY)
    return f"{u:%Y-%m-%d %H:%M} UTC ({m:%H:%M} Monterrey, UTC−6)"


def leer_iso_utc(texto: object) -> datetime | None:
    """ISO 8601 con zona -> UTC. Sin zona o ilegible -> None (no se
    asume ninguna zona aquí: cada fuente sabe la suya y la declara)."""
    if not isinstance(texto, str) or not texto.strip():
        return None
    t = texto.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def leer_fecha(texto: object) -> date | None:
    if not isinstance(texto, str):
        return None
    try:
        return date.fromisoformat(texto.strip()[:10])
    except ValueError:
        return None


def dias_habiles_despues(d: date, n: int) -> date:
    """Suma n días lunes-viernes. NO descuenta feriados de NY: cuando una
    fuente necesita el calendario real lo dice en su docstring."""
    cursor = d
    while n > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            n -= 1
    return cursor
