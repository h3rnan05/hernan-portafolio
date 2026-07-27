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
solo de los trades que el usuario decide registrar a mano").

Pivote 2026-07-26 (pedido explícito: "quiero que el sistema tenga
MEMORIA... con el tiempo quiero que el bot se adapte usando únicamente
resultados reales... no quiero optimizar eso todavía, solo quiero que
toda la arquitectura quede preparada"): `AlertaRegistrada` ahora también
guarda la materia prima de cada alerta -- patrón, hora del día, tipo de
catalizador, float, gap, RVOL -- para que `stats.py` pueda responder
"¿qué patrón gana más? ¿qué horario funciona mejor?" cuando haya
suficientes datos reales. Nada de esto se usa todavía para ajustar
scoring ni umbrales -- es solo la memoria, no el aprendizaje en sí."""

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
    # -- Memoria para el aprendizaje futuro (ver docstring del módulo) --
    hora_utc: int | None = None
    catalizador_tipo: str | None = None
    float_acciones: float | None = None
    gap_pct: float | None = None
    rvol: float | None = None
    # -- Refinamiento "Head Trader" (2026-07-27) --
    ultimo_estado: str | None = None      # vigilancia.py: último estado observado post-alerta
    diario_escrito: bool = False           # diario.py: la página de aprendizaje ya se generó


def desde_oportunidad(o: Oportunidad) -> AlertaRegistrada:
    """`AlertaRegistrada` conserva el esquema de dos objetivos/estrategia
    de antes del pivote a formato trader (2026-07-26) para no romper
    `outcomes.py`/`stats.py` ni el historial ya persistido -- pero el
    nuevo `Oportunidad` solo tiene un objetivo y ya no decide una
    estrategia de opciones por alerta (ver `report.py`), así que
    `objetivo2` y `estrategia` quedan vacíos aquí a propósito.
    `clasificacion` guarda `o.patron_clave` (clave interna, ej.
    "gap_and_go") en vez de una etiqueta con emoji -- más limpio para
    agrupar en `stats.py`."""
    return AlertaRegistrada(
        id=uuid.uuid4().hex[:10], ticker=o.ticker, fecha=o.fecha,
        precio_entrada=o.entrada, stop=o.stop, objetivo1=o.objetivo,
        objetivo2=None, clasificacion=o.patron_clave, estrategia="", score=o.score,
        hora_utc=o.hora_utc, catalizador_tipo=o.catalizador_tipo,
        float_acciones=o.float_acciones, gap_pct=o.gap_pct, rvol=o.rvol,
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
