"""Lee el calendario de sesión que el lado paper deja en disco.

El hunter no abre conexiones y no habla con ningún host de trading: solo
lee un JSON. Quien lo escribe (GET del calendario y del reloj, una vez
al día) vive en el paper trader. Si el archivo no está o no cubre el
día, no se inventa un horario de sesión regular: para entrar, eso es
cerrado; para liquidar, el cierre conservador de las 13:00 en
America/New_York (la media sesión más temprana). Un campo ausente no
se convierte en cero.

La fecha que importa es la de Nueva York, no la UTC: a las 01:00 UTC
todavía es el día anterior en el este.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# Mismos márgenes que la ventana fija de verano (13:00–20:01 UTC contra
# una apertura 13:30 y un cierre 20:00): media hora antes de abrir, un
# minuto después de cerrar, para que el último tick del cierre diario
# alcance a correr.
MINUTOS_ANTES_APERTURA = 30
MINUTOS_DESPUES_CIERRE = 1

# Media sesión (13:00 ET). Si no sabemos cuándo cierra hoy, se liquida
# a esta hora: es lo más temprano que el mercado cierra en un día hábil.
CIERRE_CONSERVADOR = time(13, 0)

ENV_RUTA = "MOMENTUM_CALENDARIO_PATH"
RUTA_DEFAULT = Path("/var/lib/momentum/calendario_alpaca.json")

MOTIVO_OK = "ok"
MOTIVO_CERRADO = "cerrado"          # el rango cubre el día y no hay sesión
MOTIVO_SIN_ARCHIVO = "sin_archivo"
MOTIVO_NO_CUBRE = "no_cubre"        # el archivo existe pero el día cae fuera
MOTIVO_ILEGIBLE = "ilegible"

_DESCONOCIDOS = frozenset({MOTIVO_SIN_ARCHIVO, MOTIVO_NO_CUBRE, MOTIVO_ILEGIBLE})

# El archivo se lee en cada vela. Se guarda parseado por ruta y mtime:
# si el job lo reescribe, el mtime cambia y se vuelve a leer.
_cache_clave: tuple | None = None
_cache_doc: dict | None = None


@dataclass(frozen=True)
class Dia:
    fecha: date
    apertura: time
    cierre: time


@dataclass(frozen=True)
class Consulta:
    fecha_ny: date
    motivo: str
    dia: Dia | None
    fetched_at: str | None = None

    @property
    def conocido(self) -> bool:
        return self.motivo in (MOTIVO_OK, MOTIVO_CERRADO)

    @property
    def desconocido(self) -> bool:
        return self.motivo in _DESCONOCIDOS


def ruta() -> Path:
    bruto = os.environ.get(ENV_RUTA)
    if bruto:
        return Path(bruto)
    return RUTA_DEFAULT


def guardar(path: Path, documento: dict) -> None:
    """Escritura atómica. La usa el job del paper; el hunter no la llama."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_name(path.name + ".tmp")
    temporal.write_text(
        json.dumps(documento, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporal, path)


def consultar(ahora: datetime, path: Path | None = None) -> Consulta:
    momento = _aware(ahora)
    fecha = momento.astimezone(NY).date()
    destino = path if path is not None else ruta()
    doc, motivo_archivo = _cargar(destino)
    if doc is None:
        return Consulta(fecha, motivo_archivo, None, None)
    fetched = doc.get("fetched_at")
    fetched_at = fetched if isinstance(fetched, str) and fetched else None
    dias = doc.get("dias")
    if not isinstance(dias, dict):
        return Consulta(fecha, MOTIVO_ILEGIBLE, None, fetched_at)
    desde, hasta = _rango(doc, dias)
    if desde is None or hasta is None:
        return Consulta(fecha, MOTIVO_ILEGIBLE, None, fetched_at)
    if fecha < desde or fecha > hasta:
        return Consulta(fecha, MOTIVO_NO_CUBRE, None, fetched_at)
    fila = dias.get(fecha.isoformat())
    if fila is None:
        return Consulta(fecha, MOTIVO_CERRADO, None, fetched_at)
    if not isinstance(fila, dict):
        return Consulta(fecha, MOTIVO_ILEGIBLE, None, fetched_at)
    apertura = _parse_hora(fila.get("open"))
    cierre = _parse_hora(fila.get("close"))
    # Horario a medias no es un horario: no se completa con ceros.
    if apertura is None or cierre is None or cierre <= apertura:
        return Consulta(fecha, MOTIVO_ILEGIBLE, None, fetched_at)
    return Consulta(fecha, MOTIVO_OK, Dia(fecha, apertura, cierre), fetched_at)


def en_sesion(ahora: datetime) -> bool:
    """True solo con una sesión conocida y el reloj dentro de [apertura, cierre)."""
    c = consultar(ahora)
    if c.dia is None:
        return False
    ny = _aware(ahora).astimezone(NY)
    return c.dia.apertura <= ny.time() < c.dia.cierre


def minutos_hasta_el_cierre(ahora: datetime) -> float:
    """Minutos hasta el cierre de HOY, para decidir si se puede ENTRAR.

    Negativo si el día no tiene sesión (feriado, finde) o si no sabemos
    el calendario: en ese caso no se abre nada. Antes de la apertura de
    un día conocido sigue siendo positivo — quien pregunta "¿queda
    tiempo?" tiene que mirar también si la sesión ya empezó.
    """
    c = consultar(ahora)
    if c.dia is None:
        return -1.0
    return _minutos_hasta(ahora, c.dia.cierre)


def minutos_para_liquidar(ahora: datetime) -> float:
    """Minutos hasta el cierre que usa el cierre de fin de día.

    Día conocido: su cierre real (media sesión incluida). Día que se
    sabe cerrado: negativo, no hay nada que liquidar. Calendario
    ausente o que no cubre hoy: las 13:00 de Nueva York.
    """
    c = consultar(ahora)
    if c.desconocido:
        return _minutos_hasta(ahora, CIERRE_CONSERVADOR)
    if c.dia is None:
        return -1.0
    return _minutos_hasta(ahora, c.dia.cierre)


def en_ventana_operativa(ahora: datetime) -> bool:
    """Ventana en la que el proceso permanente de rechequeo trabaja.

    Día con sesión: desde media hora antes de abrir hasta un minuto
    después de cerrar. Feriado o finde cubierto por el archivo: no hay
    ventana. Sin archivo o sin el día de hoy: solo el tramo alrededor
    de las 13:00 NY, para que el cierre diario igual corra.
    """
    c = consultar(ahora)
    ny = _aware(ahora).astimezone(NY)
    if c.desconocido:
        inicio = _instante_ny(ahora, CIERRE_CONSERVADOR) - timedelta(minutes=MINUTOS_ANTES_APERTURA)
        fin = _instante_ny(ahora, CIERRE_CONSERVADOR) + timedelta(minutes=MINUTOS_DESPUES_CIERRE)
        return inicio <= ny < fin
    if c.dia is None:
        return False
    inicio = _instante_ny(ahora, c.dia.apertura) - timedelta(minutes=MINUTOS_ANTES_APERTURA)
    fin = _instante_ny(ahora, c.dia.cierre) + timedelta(minutes=MINUTOS_DESPUES_CIERRE)
    return inicio <= ny < fin


def inicio_proxima_ventana(ahora: datetime) -> datetime:
    """Próximo inicio de ventana estrictamente posterior a `ahora` (UTC).

    Mira unos días hacia adelante en el archivo. Si el archivo no dice
    nada, el próximo tramo es el de las 13:00 NY (mismo margen), que es
    cuando habría que liquidar sin calendario.
    """
    base = _aware(ahora).astimezone(NY)
    for delta in range(0, 12):
        dia = base.date() + timedelta(days=delta)
        muestra = datetime.combine(dia, time(12, 0), tzinfo=NY)
        c = consultar(muestra)
        if c.dia is not None:
            inicio = datetime.combine(dia, c.dia.apertura, tzinfo=NY) - timedelta(
                minutes=MINUTOS_ANTES_APERTURA)
        elif c.desconocido:
            inicio = datetime.combine(dia, CIERRE_CONSERVADOR, tzinfo=NY) - timedelta(
                minutes=MINUTOS_ANTES_APERTURA)
        else:
            continue
        if inicio > base:
            return inicio.astimezone(UTC)
    manana = (base + timedelta(days=1)).date()
    return datetime.combine(manana, time(13, 0), tzinfo=UTC)


def hora_cierre_utc(ahora: datetime) -> float | None:
    """Hora UTC (fracción) del cierre que aplica a la fecha de NY de `ahora`.

    None si ese día se sabe cerrado. Si el calendario no cubre el día,
    las 13:00 NY pasadas a UTC — el mismo cierre conservador del EOD.
    """
    c = consultar(ahora)
    if c.motivo == MOTIVO_CERRADO:
        return None
    hora = c.dia.cierre if c.dia is not None else CIERRE_CONSERVADOR
    utc = _instante_ny(ahora, hora).astimezone(UTC)
    return utc.hour + utc.minute / 60.0 + utc.second / 3600.0


def limites_ny(timestamp_iso: str) -> tuple[time, time] | None:
    """(apertura, cierre) en hora de Nueva York para la vela, o None.

    None si el día no tiene sesión o el calendario no lo cubre: esa vela
    no es premarket ni sesión regular. No se cae a 9:30–16:00 fijos,
    porque en una media sesión eso contaría como regulares velas que ya
    son después del cierre.
    """
    try:
        dt = datetime.fromisoformat(timestamp_iso)
    except (TypeError, ValueError):
        return None
    c = consultar(dt)
    if c.dia is None:
        return None
    return c.dia.apertura, c.dia.cierre


def _aware(ahora: datetime) -> datetime:
    if ahora.tzinfo is None:
        return ahora.replace(tzinfo=UTC)
    return ahora


def _instante_ny(ahora: datetime, hora: time) -> datetime:
    ny = _aware(ahora).astimezone(NY)
    return ny.replace(hour=hora.hour, minute=hora.minute, second=hora.second, microsecond=0)


def _minutos_hasta(ahora: datetime, hora: time) -> float:
    ahora_ny = _aware(ahora).astimezone(NY)
    return (_instante_ny(ahora, hora) - ahora_ny).total_seconds() / 60.0


def _cargar(path: Path) -> tuple[dict | None, str]:
    global _cache_clave, _cache_doc
    try:
        st = path.stat()
    except FileNotFoundError:
        return None, MOTIVO_SIN_ARCHIVO
    except OSError:
        return None, MOTIVO_ILEGIBLE
    clave = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    if clave == _cache_clave and _cache_doc is not None:
        return _cache_doc, MOTIVO_OK
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, MOTIVO_ILEGIBLE
    if not isinstance(doc, dict):
        return None, MOTIVO_ILEGIBLE
    _cache_clave, _cache_doc = clave, doc
    return doc, MOTIVO_OK


def _rango(doc: dict, dias: dict) -> tuple[date | None, date | None]:
    rango = doc.get("rango")
    if isinstance(rango, dict):
        desde = _parse_fecha(rango.get("desde"))
        hasta = _parse_fecha(rango.get("hasta"))
        if desde is not None and hasta is not None and desde <= hasta:
            return desde, hasta
    fechas = [f for f in (_parse_fecha(k) for k in dias) if f is not None]
    if not fechas:
        return None, None
    return min(fechas), max(fechas)


def _parse_fecha(v) -> date | None:
    if not isinstance(v, str) or len(v) < 10:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _parse_hora(v) -> time | None:
    if not isinstance(v, str):
        return None
    partes = v.strip().split(":")
    if len(partes) not in (2, 3):
        return None
    try:
        hora = int(partes[0])
        minuto = int(partes[1])
        segundo = int(partes[2]) if len(partes) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hora <= 23 and 0 <= minuto <= 59 and 0 <= segundo <= 59):
        return None
    return time(hora, minuto, segundo)
