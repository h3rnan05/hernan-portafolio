"""Prompt 10: "Guardar todas las alertas." Persistencia pura -- trabaja
sobre `AlertaRegistrada` en memoria + un archivo JSON, sin tocar red
(mismo principio que `journal/store.py`: separar la escritura de estado
de la actualización de resultados, que sí necesita datos de mercado y
vive en `outcomes.py`).

A diferencia de `journal/` (donde el USUARIO reporta manualmente el
resultado de una operación que decidió tomar), esto registra TODAS las
alertas que el modelo mandó, automáticamente, se hayan operado o no --
es la pieza de "Learning Engine" que el ROADMAP describe como pendiente
para el bot hermano ("medir el desempeño de las alertas EN SÍ MISMAS, no
solo de los trades que el usuario decide registrar a mano")."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from momentum_hunter.models import Oportunidad

PATH = Path(__file__).resolve().parent / "alertas_enviadas.json"


@dataclass
class AlertaRegistrada:
    id: str
    ticker: str
    fecha: str                            # ISO datetime de cuando se mandó la alerta
    precio_entrada: float
    stop: float | None
    objetivo1: float | None
    objetivo2: float | None
    clasificacion: str
    estrategia: str
    score: float
    resultados_pct: dict[str, float | None] = field(default_factory=dict)  # "1d"/"3d"/"5d"/"10d"
    precio_maximo_pct: float | None = None   # mejor movimiento a favor visto hasta ahora
    precio_minimo_pct: float | None = None   # peor movimiento en contra visto hasta ahora
    resuelta: bool = False                    # True cuando ya se conoce el resultado del horizonte más largo


def desde_oportunidad(o: Oportunidad) -> AlertaRegistrada:
    """`AlertaRegistrada` conserva el esquema de dos objetivos/estrategia
    de antes del pivote a formato trader (2026-07-26) para no romper
    `outcomes.py`/`stats.py` ni el historial ya persistido -- pero el
    nuevo `Oportunidad` solo tiene un objetivo y ya no decide una
    estrategia de opciones por alerta (ver `report.py`), así que
    `objetivo2` y `estrategia` quedan vacíos aquí a propósito."""
    return AlertaRegistrada(
        id=uuid.uuid4().hex[:10], ticker=o.ticker, fecha=o.fecha,
        precio_entrada=o.entrada, stop=o.stop, objetivo1=o.objetivo,
        objetivo2=None, clasificacion=o.patron, estrategia="", score=o.score,
    )


def cargar(path: Path = PATH) -> list[AlertaRegistrada]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [AlertaRegistrada(**a) for a in data.get("alertas", [])]


def guardar(alertas: list[AlertaRegistrada], path: Path = PATH) -> None:
    path.write_text(json.dumps(
        {"alertas": [asdict(a) for a in alertas]}, indent=2, ensure_ascii=False,
    ))


def registrar(oportunidades: list[Oportunidad], path: Path = PATH) -> list[AlertaRegistrada]:
    """Añade las oportunidades de hoy al historial persistido y devuelve
    SOLO las nuevas (para que quien llame pueda loggear cuántas se
    agregaron sin tener que releer el archivo completo)."""
    existentes = cargar(path)
    nuevas = [desde_oportunidad(o) for o in oportunidades]
    guardar(existentes + nuevas, path)
    return nuevas
