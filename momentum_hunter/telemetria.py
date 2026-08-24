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
final de la corrida se persiste en `telemetria/{fecha}.json`, un archivo
por día con una entrada por corrida (mismo patrón que `audit.py`, mismo
formato tolerante a corrupción que el resto del repo).

NUNCA CAMBIA EL COMPORTAMIENTO. Registrar es un efecto secundario puro:
si la telemetría falla, se traga su propio error y el pipeline sigue.
Sería absurdo que el módulo que existe para vigilar fallos causara uno."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("momentum_hunter.telemetria")

DIR_TELEMETRIA = Path(__file__).resolve().parent / "telemetria"

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


def registrar_corrida(
    m: Metricas, dir_telemetria: Path = DIR_TELEMETRIA, ahora: datetime | None = None,
) -> Path | None:
    """Añade esta corrida al archivo del día. Devuelve la ruta escrita, o
    None si algo falló -- y en ese caso NO propaga la excepción: la
    telemetría jamás debe tumbar una corrida (ver docstring del módulo)."""
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
                log.warning("telemetría del día ilegible, se empieza de nuevo: %s", path.name)

        data["corridas"].append(m.como_dict())
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return path
    except Exception as ex:
        log.warning("no se pudo guardar la telemetría: %s", ex)
        return None


def cargar_dias(
    desde: str, hasta: str, dir_telemetria: Path = DIR_TELEMETRIA,
) -> list[dict]:
    """Todas las corridas entre dos fechas (ISO, ambas inclusive), en
    orden. Un archivo ilegible se omite en vez de tumbar el reporte."""
    corridas: list[dict] = []
    if not dir_telemetria.exists():
        return corridas
    for path in sorted(dir_telemetria.glob("*.json")):
        if not (desde <= path.stem <= hasta):
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("telemetría ilegible, se omite: %s", path.name)
            continue
        for c in data.get("corridas", []):
            if isinstance(c, dict):
                c.setdefault("dia", path.stem)
                corridas.append(c)
    return corridas
