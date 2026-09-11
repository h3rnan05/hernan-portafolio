"""Telemetría paper-only de latencia e2e -- qué tan tarde llega el bot.

POR QUÉ. El problema abierto A (CLAUDE.md, septiembre 2026) no es que
el pipeline no dispare: es que dispara tarde frente a un presupuesto
de ~8 velas de 1 minuto (`velas_maximas_desde_patron`). Sin timestamps
en cada hop y sin contadores diarios, esa frase es una corazonada.
Esto la vuelve un número: descubrimiento (vela → persistir TRIGGERED)
vs. punta a punta (vela → orden paper o rechazo de la IA), comparado
contra ese presupuesto. No cambia umbrales, score, criterio ni modo
Alpaca -- solo mide.

QUÉ MIDE, por corrida y agregado de sesión (el día de mercado):

  - `triggered_nuevos`: TRIGGERED que este proceso todavía no había
    revisado (lo que el ejecutor *vio*, no lo que el buscador escribió).
  - `revisiones`: revisiones persistidas en esta corrida (sí o no).
  - `ordenes_colocadas`: brackets paper aceptados.
  - `paper_step_success_zero_orders`: 1 si la corrida terminó bien y
    no colocó nada -- distingue "el paso corrió y no había nada" de
    "el paso reventó" (eso no llega a persistirse: si `ejecutar`
    lanza, el caller no registra).
  - muestras de latencia para p50/p95 -- solo las que se midieron de
    verdad. Un extremo ausente no se rellena con cero.

CÓMO. Mismo patrón que `momentum_hunter.telemetria`: un `Metricas` que
se llena como efecto secundario, un archivo `telemetria/{fecha}.json`
por día, y si esto falla se traga el error. Medir no puede tumbar una
corrida paper.

NUNCA CAMBIA EL COMPORTAMIENTO. Ni el fail-closed, ni la watchlist
(este módulo no la escribe), ni el endpoint paper."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter.config import CONFIG

log = logging.getLogger("momentum_paper_trader.telemetria")

DIR_TELEMETRIA = Path(__file__).resolve().parent / "telemetria"

# El presupuesto es el mismo que ya usa early_opportunity para decir
# "tarde": N velas de 1 minuto. Se LEE, no se cambia. 60s por vela
# porque `intervalo_intradia` es "1m" -- si eso cambiara, esta cuenta
# quedaría desfasada a propósito (mejor un número honesto y fijo que
# una conversión inventada sobre otro intervalo).
PRESUPUESTO_VELAS = CONFIG.velas_maximas_desde_patron
MS_POR_VELA = 60_000
PRESUPUESTO_MS = PRESUPUESTO_VELAS * MS_POR_VELA


def delta_ms(inicio_iso: str | None, fin_iso: str | None) -> float | None:
    """Diferencia en ms, o None si falta un extremo o no parsea.
    Nunca se inventa un 0 a partir de un dato ausente."""
    if inicio_iso is None or fin_iso is None:
        return None
    try:
        inicio = datetime.fromisoformat(inicio_iso)
        fin = datetime.fromisoformat(fin_iso)
    except (TypeError, ValueError):
        return None
    return round((fin - inicio).total_seconds() * 1000.0, 1)


def percentil(muestras: list[float], p: float) -> float | None:
    """Percentil por interpolación lineal. None si no hay muestras --
    un p50 de lista vacía no es 0, es 'no se midió'."""
    if not muestras:
        return None
    ordenados = sorted(muestras)
    n = len(ordenados)
    if n == 1:
        return float(ordenados[0])
    idx = (n - 1) * p
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return round(ordenados[lo] + (ordenados[hi] - ordenados[lo]) * frac, 1)


def instrumentar_revision(
    r, e, *, executor_leido_ts: str, ia_decision_ts: str | None,
) -> None:
    """Copia los relojes de la watchlist y calcula las latencias de
    esta revisión. Mutación in-place: el caller ya construyó el
    registro de decisión; esto solo agrega la cinta de tiempos.

    `watchlist_escrito_ts` cae a `actualizado_en` si la entrada nació
    antes de que existiera el campo -- ese timestamp SÍ se midió (la
    transición a TRIGGERED). No se fabrica un reloj nuevo."""
    r.market_event_ts = getattr(e, "market_event_ts", None)
    escrito = getattr(e, "watchlist_escrito_ts", None) or getattr(e, "actualizado_en", None)
    r.watchlist_escrito_ts = escrito
    r.executor_leido_ts = executor_leido_ts
    r.ia_decision_ts = ia_decision_ts
    r.latencia_descubrimiento_ms = delta_ms(r.market_event_ts, r.watchlist_escrito_ts)
    r.latencia_e2e_ms = delta_ms(r.market_event_ts, r.timestamp)


@dataclass
class Metricas:
    """Contadores de UNA corrida paper. Solo suben o se anexan."""
    timestamp: str = ""
    triggered_nuevos: int = 0
    revisiones: int = 0
    ordenes_colocadas: int = 0
    paper_step_success_zero_orders: int = 0
    latencias_descubrimiento_ms: list[float] = field(default_factory=list)
    latencias_alerta_ms: list[float] = field(default_factory=list)
    latencias_e2e_ms: list[float] = field(default_factory=list)

    def anotar_revision(self, r, signal_latency_ms: float | None = None) -> None:
        self.revisiones += 1
        if r.entro and r.order_id:
            self.ordenes_colocadas += 1
        if r.latencia_descubrimiento_ms is not None:
            self.latencias_descubrimiento_ms.append(r.latencia_descubrimiento_ms)
        if r.latencia_e2e_ms is not None:
            self.latencias_e2e_ms.append(r.latencia_e2e_ms)
        if signal_latency_ms is not None:
            self.latencias_alerta_ms.append(signal_latency_ms)

    def cerrar_corrida(self) -> None:
        """Se llama al terminar bien: un paso que no lanzó. Cero
        órdenes no es un fallo -- es el caso más común del embudo."""
        self.paper_step_success_zero_orders = 1 if self.ordenes_colocadas == 0 else 0

    def como_dict(self) -> dict:
        return {
            "timestamp": self.timestamp or _ahora().isoformat(timespec="seconds"),
            "triggered_nuevos": self.triggered_nuevos,
            "revisiones": self.revisiones,
            "ordenes_colocadas": self.ordenes_colocadas,
            "paper_step_success_zero_orders": self.paper_step_success_zero_orders,
            "presupuesto_velas": PRESUPUESTO_VELAS,
            "presupuesto_ms": PRESUPUESTO_MS,
            "latencias_ms": {
                "descubrimiento": list(self.latencias_descubrimiento_ms),
                "alerta": list(self.latencias_alerta_ms),
                "e2e": list(self.latencias_e2e_ms),
            },
            "sobre_presupuesto": {
                "descubrimiento": _sobre_presupuesto(self.latencias_descubrimiento_ms),
                "alerta": _sobre_presupuesto(self.latencias_alerta_ms),
                "e2e": _sobre_presupuesto(self.latencias_e2e_ms),
            },
        }


def _sobre_presupuesto(muestras: list[float]) -> int:
    return sum(1 for x in muestras if x > PRESUPUESTO_MS)


def _ahora() -> datetime:
    return datetime.now(UTC)


def resumir_sesion(corridas: list[dict]) -> dict:
    """Agrega el día: sumas de contadores + p50/p95 de todas las
    muestras. Una corrida ilegible se omite (no tumba el resumen)."""
    triggered = revisiones = ordenes = ceros = 0
    por_serie: dict[str, list[float]] = {
        "descubrimiento": [], "alerta": [], "e2e": [],
    }
    for c in corridas:
        if not isinstance(c, dict):
            continue
        triggered += int(c.get("triggered_nuevos") or 0)
        revisiones += int(c.get("revisiones") or 0)
        ordenes += int(c.get("ordenes_colocadas") or 0)
        ceros += int(c.get("paper_step_success_zero_orders") or 0)
        lat = c.get("latencias_ms") or {}
        if not isinstance(lat, dict):
            continue
        for clave in por_serie:
            for v in lat.get(clave) or []:
                if isinstance(v, (int, float)):
                    por_serie[clave].append(float(v))
    return {
        "triggered_nuevos": triggered,
        "revisiones": revisiones,
        "ordenes_colocadas": ordenes,
        "paper_step_success_zero_orders": ceros,
        "muestras_latencia": {k: len(vs) for k, vs in por_serie.items()},
        "latencia_p50_ms": {k: percentil(vs, 0.50) for k, vs in por_serie.items()},
        "latencia_p95_ms": {k: percentil(vs, 0.95) for k, vs in por_serie.items()},
        "sobre_presupuesto": {k: _sobre_presupuesto(vs) for k, vs in por_serie.items()},
        "presupuesto_velas": PRESUPUESTO_VELAS,
        "presupuesto_ms": PRESUPUESTO_MS,
    }


def registrar_corrida(
    m: Metricas, dir_telemetria: Path = DIR_TELEMETRIA, ahora: datetime | None = None,
) -> Path | None:
    """Añade esta corrida al archivo del día y reescribe el resumen de
    sesión. None si falló -- nunca propaga: medir no puede tumbar."""
    try:
        ahora = ahora or _ahora()
        m.timestamp = m.timestamp or ahora.isoformat(timespec="seconds")
        dir_telemetria.mkdir(parents=True, exist_ok=True)
        path = dir_telemetria / f"{ahora.date().isoformat()}.json"

        data: dict = {"corridas": []}
        if path.exists():
            try:
                cargado = json.loads(path.read_text())
                if isinstance(cargado, dict) and isinstance(cargado.get("corridas"), list):
                    data = cargado
            except (json.JSONDecodeError, OSError):
                log.warning("telemetría paper del día ilegible, se empieza de nuevo: %s", path.name)

        data["corridas"].append(m.como_dict())
        data["sesion"] = resumir_sesion(data["corridas"])
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return path
    except Exception as ex:
        log.warning("no se pudo guardar la telemetría paper: %s", type(ex).__name__)
        return None
