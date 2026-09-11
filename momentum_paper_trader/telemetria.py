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
se llena como efecto secundario. Cada escritor (VPS / GHA / local)
appendea a SU archivo, nunca al del otro:

  telemetria/{fecha}/{fuente}/events.jsonl   # append-only, merge=union
  telemetria/{fecha}/{fuente}/sesion.json    # rollup derivado de ESA fuente

El JSON monolítico `telemetria/{fecha}.json` se sigue LEYENDO (días
viejos y el conflicto de 2026-09-11) pero ya no se reescribe: dos
hosts haciendo read-modify-write del mismo diario reventaban el
rebase de git y, de paso, se perdía la telemetría del hunter.

Si medir falla se traga el error. Medir no puede tumbar una corrida
paper.

NUNCA CAMBIA EL COMPORTAMIENTO. Ni el fail-closed, ni la watchlist
(este módulo no la escribe), ni el endpoint paper."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter.config import CONFIG

log = logging.getLogger("momentum_paper_trader.telemetria")

DIR_TELEMETRIA = Path(__file__).resolve().parent / "telemetria"

# Tres escritores posibles, tres subdirectorios. Un valor ausente o
# desconocido NO se recategoriza como vps/gha: eso inventaría origen.
FUENTES_VALIDAS = ("vps", "gha", "local")
_RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


def resolver_fuente(fuente: str | None = None) -> str:
    """Origen de esta escritura. El env `MOMENTUM_TELEM_FUENTE` manda;
    si no, GITHUB_ACTIONS implica `gha`; el resto es `local`.
    El VPS exporta `vps` en `scripts/run_watchlist_paper.sh` -- no se
    adivina por hostname (un path inventado mezclaría escritores)."""
    candidata = (fuente if fuente is not None else os.getenv("MOMENTUM_TELEM_FUENTE", "")).strip().lower()
    if candidata in FUENTES_VALIDAS:
        return candidata
    if os.getenv("GITHUB_ACTIONS", "").strip().lower() in {"1", "true"}:
        return "gha"
    return "local"


def _es_fecha_iso(nombre: str) -> bool:
    if not _RE_FECHA.match(nombre):
        return False
    try:
        datetime.strptime(nombre, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def ruta_events(dir_telemetria: Path, fecha: str, fuente: str) -> Path:
    return dir_telemetria / fecha / fuente / "events.jsonl"


def ruta_sesion(dir_telemetria: Path, fecha: str, fuente: str) -> Path:
    return dir_telemetria / fecha / fuente / "sesion.json"


def _corridas_de_json_legacy(path: Path) -> list[dict]:
    try:
        cargado = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("telemetría paper ilegible, se omite: %s", path.name)
        return []
    if not isinstance(cargado, dict) or not isinstance(cargado.get("corridas"), list):
        return []
    return [c for c in cargado["corridas"] if isinstance(c, dict)]


def _corridas_de_jsonl(path: Path) -> list[dict]:
    """Una línea = una corrida. Una línea rota se omite: no se inventa
    el objeto ni se tira el resto del archivo."""
    corridas: list[dict] = []
    try:
        texto = path.read_text()
    except OSError:
        log.warning("telemetría paper jsonl ilegible, se omite: %s", path.name)
        return corridas
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError:
            log.warning("línea jsonl paper ilegible, se omite: %s", path.name)
            continue
        if isinstance(obj, dict):
            corridas.append(obj)
    return corridas


def cargar_dias(
    desde: str, hasta: str, dir_telemetria: Path = DIR_TELEMETRIA,
) -> list[dict]:
    """Todas las corridas paper entre dos fechas (ISO, ambas inclusive).

    Lee el layout viejo (`{fecha}.json`) Y el partido
    (`{fecha}/{fuente}/events.jsonl`). Un archivo ilegible se omite
    -- el rollup del día no se fabrica a partir de un hueco."""
    halladas: list[tuple[str, str, dict]] = []
    if not dir_telemetria.exists():
        return []

    for path in sorted(dir_telemetria.glob("*.json")):
        dia = path.stem
        if not _es_fecha_iso(dia) or not (desde <= dia <= hasta):
            continue
        for c in _corridas_de_json_legacy(path):
            c.setdefault("dia", dia)
            halladas.append((dia, c.get("timestamp") or "", c))

    for path in sorted(dir_telemetria.glob("*/*/events.jsonl")):
        dia = path.parent.parent.name
        if not _es_fecha_iso(dia) or not (desde <= dia <= hasta):
            continue
        for c in _corridas_de_jsonl(path):
            c.setdefault("dia", dia)
            c.setdefault("fuente", path.parent.name)
            halladas.append((dia, c.get("timestamp") or "", c))

    halladas.sort(key=lambda x: (x[0], x[1]))
    return [c for _, _, c in halladas]


def cargar_sesion(
    dia: str, dir_telemetria: Path = DIR_TELEMETRIA,
) -> dict:
    """Rollup del día completo (todas las fuentes + legacy)."""
    return resumir_sesion(cargar_dias(dia, dia, dir_telemetria))


def registrar_corrida(
    m: Metricas,
    dir_telemetria: Path = DIR_TELEMETRIA,
    ahora: datetime | None = None,
    fuente: str | None = None,
) -> Path | None:
    """Appendea esta corrida al JSONL de SU fuente y reescribe el
    rollup derivado de esa fuente. None si falló -- nunca propaga.

    No toca `telemetria/{fecha}.json` ni el JSONL de otra fuente: eso
    es lo que chocaba entre VPS y GHA."""
    try:
        ahora = ahora or _ahora()
        origen = resolver_fuente(fuente)
        m.timestamp = m.timestamp or ahora.isoformat(timespec="seconds")
        fecha = ahora.date().isoformat()
        path = ruta_events(dir_telemetria, fecha, origen)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = m.como_dict()
        payload["fuente"] = origen
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

        corridas = _corridas_de_jsonl(path)
        rollup = {
            "fuente": origen,
            "corridas": corridas,
            "sesion": resumir_sesion(corridas),
        }
        ruta_sesion(dir_telemetria, fecha, origen).write_text(
            json.dumps(rollup, ensure_ascii=False, indent=2)
        )
        return path
    except Exception as ex:
        log.warning("no se pudo guardar la telemetría paper: %s", type(ex).__name__)
        return None
