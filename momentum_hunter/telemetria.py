"""Telemetría del pipeline -- qué pasó en cada corrida y qué se rompió.

POR QUÉ (pedido 2026-08-24: "haz un log de todo para ver qué errores
tenemos... tenemos que hacer este sistema escalable"). El bot ya guarda
una auditoría por CANDIDATA (`audit.py`), que responde "¿por qué se
descartó ESTA?". Lo que no existía era la vista de arriba: cuántas
entraron, dónde murieron en masa, y sobre todo QUÉ FALLÓ -- porque el
pipeline está lleno de `except Exception: return []` deliberados (nunca
tumbar la corrida por un ticker), y esos fallos hasta hoy desaparecían
sin dejar rastro. Un sistema que no puede ver sus propios errores no se
puede mejorar; por eso esto es la pieza que lo hace escalable.

QUÉ MIDE, y por qué cada cosa:

  - El embudo POR BANDA (small-cap / large-cap). La pregunta abierta del
    2026-08-24 es si las small-caps mueren por falta de cobertura de
    noticias -- no se pudo comprobar entonces, y esta es la forma
    correcta de responderla: contar, cada día, cuántas acciones de cada
    banda tenían ALGUNA noticia y cuántas tenían una que además calificó
    como catalizador. Con eso la respuesta llega sola en unos días, con
    datos en vez de con una corazonada.

  - Los ERRORES por tipo y por origen. No para tumbar nada -- el
    principio de "un ticker que falla no arruina la corrida" se
    mantiene intacto -- sino para saber que existen. Un 40% de fallos de
    red en el proveedor de noticias explicaría cosas que hoy parecerían
    "no había catalizadores".

CÓMO. Un `Metricas` que se pasa por el pipeline y se va llenando; al
final de la corrida se appendea a `telemetria/{fecha}/{fuente}/events.jsonl`
(VPS y GHA no tocan el mismo archivo). El JSON monolítico
`telemetria/{fecha}.json` se sigue leyendo para el reporte semanal de
días viejos, pero ya no se reescribe: el read-modify-write compartido
era la misma clase de conflicto que tumba el persist de git.

NUNCA CAMBIA EL COMPORTAMIENTO. Registrar es un efecto secundario puro:
si la telemetría falla, se traga su propio error y el pipeline sigue.
Sería absurdo que el módulo que existe para vigilar fallos causara uno."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("momentum_hunter.telemetria")

DIR_TELEMETRIA = Path(__file__).resolve().parent / "telemetria"

FUENTES_VALIDAS = ("vps", "gha", "local")
_RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

BANDA_SMALL = "small"
BANDA_LARGE = "large"
BANDAS = (BANDA_SMALL, BANDA_LARGE)


@dataclass
class Metricas:
    """Contadores de UNA corrida. Todos empiezan en cero y solo suben --
    nada acá interpreta ni decide, solo cuenta."""
    timestamp: str = ""
    modo: str = "escaneo"          # "escaneo" | "watchlist"

    universo_total: int = 0        # símbolos que el universo ofrecía
    universo_escaneado: int = 0    # los que de verdad se pidieron (ventana rotativa)

    # Embudo por banda: {"small": n, "large": n}
    operables: Counter = field(default_factory=Counter)
    con_alguna_noticia: Counter = field(default_factory=Counter)
    con_catalizador: Counter = field(default_factory=Counter)
    evaluadas: Counter = field(default_factory=Counter)
    accionables: Counter = field(default_factory=Counter)

    # Supervivencia de las cuatro condiciones obligatorias de `accionable`
    # -- responde "¿cuál nos está matando?" sin abrir la auditoría.
    paso_patron: int = 0
    paso_temprano: int = 0
    paso_riesgo: int = 0
    paso_dinero: int = 0
    paso_umbral: int = 0

    # Errores: {"origen:TipoDeError": n}. Nunca guarda el mensaje
    # completo (puede traer una URL con credenciales) -- solo el tipo.
    errores: Counter = field(default_factory=Counter)

    # Score más alto que se vio -- para detectar de un vistazo si el
    # umbral está por encima de lo alcanzable, que es el error que ya
    # nos costó semanas (ver `config.score_minimo_alerta`).
    score_maximo: float = 0.0

    def registrar_error(self, origen: str, ex: BaseException) -> None:
        self.errores[f"{origen}:{type(ex).__name__}"] += 1

    def sumar(self, contador: Counter, banda: str | None, n: int = 1) -> None:
        """`banda` None se cuenta aparte como 'desconocida' en vez de
        perderse o de asignarse a una banda arbitraria."""
        contador[banda if banda in BANDAS else "desconocida"] += n

    def como_dict(self) -> dict:
        return {
            "timestamp": self.timestamp or _ahora().isoformat(timespec="seconds"),
            "modo": self.modo,
            "universo_total": self.universo_total,
            "universo_escaneado": self.universo_escaneado,
            "embudo": {
                "operables": dict(self.operables),
                "con_alguna_noticia": dict(self.con_alguna_noticia),
                "con_catalizador": dict(self.con_catalizador),
                "evaluadas": dict(self.evaluadas),
                "accionables": dict(self.accionables),
            },
            "condiciones": {
                "patron": self.paso_patron,
                "temprano": self.paso_temprano,
                "riesgo_definido": self.paso_riesgo,
                "dinero_entrando": self.paso_dinero,
                "sobre_umbral": self.paso_umbral,
            },
            "score_maximo": round(self.score_maximo, 1),
            "errores": dict(self.errores),
        }


def _ahora() -> datetime:
    return datetime.now(UTC)


def resolver_fuente(fuente: str | None = None) -> str:
    """Origen de esta escritura. Ver `momentum_paper_trader.telemetria`:
    el env manda; GITHUB_ACTIONS implica `gha`; si no, `local`."""
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


def _corridas_de_json_legacy(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("telemetría ilegible, se omite: %s", path.name)
        return []
    if not isinstance(data, dict) or not isinstance(data.get("corridas"), list):
        return []
    return [c for c in data["corridas"] if isinstance(c, dict)]


def _corridas_de_jsonl(path: Path) -> list[dict]:
    corridas: list[dict] = []
    try:
        texto = path.read_text()
    except OSError:
        log.warning("telemetría jsonl ilegible, se omite: %s", path.name)
        return corridas
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError:
            log.warning("línea jsonl ilegible, se omite: %s", path.name)
            continue
        if isinstance(obj, dict):
            corridas.append(obj)
    return corridas


def registrar_corrida(
    m: Metricas,
    dir_telemetria: Path = DIR_TELEMETRIA,
    ahora: datetime | None = None,
    fuente: str | None = None,
) -> Path | None:
    """Appendea esta corrida al JSONL de SU fuente. Devuelve la ruta, o
    None si algo falló -- y en ese caso NO propaga: la telemetría jamás
    debe tumbar una corrida (ver docstring del módulo)."""
    try:
        ahora = ahora or _ahora()
        origen = resolver_fuente(fuente)
        m.timestamp = m.timestamp or ahora.isoformat(timespec="seconds")
        fecha = ahora.date().isoformat()
        path = dir_telemetria / fecha / origen / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = m.como_dict()
        payload["fuente"] = origen
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path
    except Exception as ex:
        log.warning("no se pudo guardar la telemetría: %s", type(ex).__name__)
        return None


def cargar_dias(
    desde: str, hasta: str, dir_telemetria: Path = DIR_TELEMETRIA,
) -> list[dict]:
    """Todas las corridas entre dos fechas (ISO, ambas inclusive), en
    orden. Layout viejo (`{fecha}.json`) y partido
    (`{fecha}/{fuente}/events.jsonl`). Un archivo ilegible se omite
    en vez de tumbar el reporte."""
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
