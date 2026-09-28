"""Sombra de `trade_updates` -- solo escucha, nunca decide.

POR QUÉ. El cierre y el seguimiento se enteran de un fill cuando el
próximo tick hace `GET /v2/orders` (en el cierre, `_esperar_patas`
en `cierre.py`). Eso llega tarde y no deja un registro de cada
transición. Este proceso abre el websocket de la cuenta PAPER, pide
`trade_updates` y appendea cada evento a un JSONL. Un archivo compacto
guarda el último estado por orden. Nada de eso lo lee el ejecutor, el
cierre ni la IA: el polling REST sigue siendo la única autoridad.

El host está fijo en `wss://paper-api.alpaca.markets/stream`. Cualquier
otra url --incluida la de la cuenta real-- aborta el arranque antes de
abrir el socket. No hay variable de entorno ni argumento para cambiarla.

FRAMES. La documentación de Alpaca dice que este stream paper manda
JSON en frames binarios (el de datos de mercado manda texto). Se
decodifican como UTF-8. Un frame que no sea JSON objeto no se convierte
en un evento: se descarta y se anota que era ilegible. No hablamos
MessagePack: no pedimos ese codec, y un binario que no sea UTF-8 JSON
no se interpreta a medias.

UN DATO QUE FALTA NO ES CERO. `qty`, `filled_qty` y `price` quedan
`null` si el frame no los trae. Un cero real que Alpaca sí mandó se
conserva como `"0"`.

USO
  python -m momentum_paper_trader.trade_updates_sombra

Credenciales: `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_API_SECRET`, las
mismas del resto del paper trader. Sin ellas no se conecta. El log
nunca escribe la clave, el secreto ni el cuerpo de un frame.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("momentum_paper_trader.trade_updates_sombra")

# Fijo. No sale de una variable de entorno: un error de config no puede
# apuntar este proceso a la cuenta real. `afirmar_url_paper` vuelve a
# comprobar el host por si alguien edita la constante.
URL_STREAM_PAPER = "wss://paper-api.alpaca.markets/stream"
HOST_PAPER = "paper-api.alpaca.markets"

RUTA_JSONL_DEFAULT = Path("/var/lib/momentum/trade_updates.jsonl")
RUTA_ESTADO_DEFAULT = Path("/var/lib/momentum/trade_updates_estado.json")

VERSION_ESTADO = 1
MAX_FALLOS_AUTH = 3
ESPERA_AUTH_S = 10.0
# Cada silencio de esta longitud escribe un heartbeat. No es un evento
# de orden: es la prueba de que el socket seguía abierto.
HEARTBEAT_S = 60.0
# Sin ningún frame (ni de control) durante este lapso: un hueco. Una
# cuenta quieta lo va a marcar; el hueco dice "no llegó nada", no "pasó
# un fill". Se abre una vez y se cierra con el siguiente frame.
HUECO_S = 300.0
BACKOFF_BASE_S = 1.0
BACKOFF_TOPE_S = 30.0

_STATUS_AUTH_CONOCIDOS = frozenset({"authorized", "unauthorized"})
_CAMPOS_ESTADO = (
    "order_id", "client_order_id", "symbol", "event", "status",
    "qty", "filled_qty", "event_qty", "price", "timestamp", "recibido_en",
    "execution_id",
)
# Por debajo de esto no se redacta: un secreto real de Alpaca es largo,
# y un id corto no debe desaparecer del log porque coincidió con un
# fragmento.
_LARGO_MINIMO_SECRETO = 12


class UrlNoEsPaper(Exception):
    """El arranque se niega. El mensaje es fijo: la url recibida puede
    traer usuario o clave y no se repite."""


class ConexionCerrada(Exception):
    pass


class ParadaPedida(Exception):
    pass


class EstadoIlegible(Exception):
    """El archivo por orden existe pero no se puede leer. No se
    reescribe: pisarlo con vacío haría desaparecer el último estado."""


class YaCorre(Exception):
    """Otro proceso tiene el candado. Dos listeners pisarían el JSONL."""


def afirmar_url_paper(url: str) -> str:
    """Devuelve la url paper canónica o lanza `UrlNoEsPaper`.

    Tiene que ser exactamente `URL_STREAM_PAPER` y, además, parsear como
    `wss` contra `paper-api.alpaca.markets` con path `/stream` y sin
    usuario, clave, query ni fragmento. Las dos condiciones van juntas:
    cambiar la constante hacia otro host también falla."""
    if not isinstance(url, str):
        raise UrlNoEsPaper("se rechazó el stream: no es el host paper")
    partes = urlsplit(url)
    canonico = (
        url == URL_STREAM_PAPER
        and partes.scheme == "wss"
        and partes.hostname == HOST_PAPER
        and partes.path == "/stream"
        and partes.username is None
        and partes.password is None
        and partes.query == ""
        and partes.fragment == ""
    )
    if not canonico:
        raise UrlNoEsPaper("se rechazó el stream: no es el host paper")
    return URL_STREAM_PAPER


def espera_de_reintento(intento: int, *, base: float = BACKOFF_BASE_S, tope: float = BACKOFF_TOPE_S) -> float:
    """Segundos antes de volver a conectar. `intento` 1 espera `base`;
    después se duplica hasta `tope`. No baja de `base`: un cero aquí
    sería un bucle apretado contra Alpaca."""
    if intento < 1:
        return base
    return min(tope, base * (2 ** (intento - 1)))


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def _texto(v) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    return s if s else None


def _cantidad(v) -> str | None:
    """String que vino en el frame, o None si el campo no está.

    Un `0` presente se conserva (`"0"`). `None`, `""` y un bool no son
    una cantidad: no se traducen a cero. Un float solo entra si es
    finito; Alpaca manda estos campos como string y el float es un
    accidente del JSON."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return format(Decimal(str(v)), "f")
    return None


def decodificar_frame(frame: bytes | str | None) -> dict | None:
    """JSON objeto dentro de un frame binario o de texto.

    El paper manda binario; el texto se acepta igual para no tirar un
    evento si el opcode cambia. Bytes que no son UTF-8 (MessagePack u
    otra cosa) y un JSON que no sea objeto devuelven None: no hay
    evento que inventar."""
    if isinstance(frame, bytes):
        try:
            texto = frame.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(frame, str):
        texto = frame
    else:
        return None
    if not texto.strip():
        return None
    try:
        msg = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(msg, dict):
        return None
    return msg


def evento_de_mensaje(msg: dict, recibido_en: str) -> dict | None:
    """Una línea de sombra, o None si el mensaje no es un trade_update
    con `event`. Sin `event` no se inventa el tipo.

    `qty` es el tamaño de la orden (`order.qty`). `event_qty` es lo que
    este evento llenó o canceló (`data.qty`); en un parcial no son lo
    mismo y no se copia uno en el hueco del otro. `price` es el precio
    del evento, no el promedio: si no vino, queda null."""
    if not isinstance(msg, dict) or msg.get("stream") != "trade_updates":
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    event = data.get("event")
    if not isinstance(event, str) or not event.strip():
        return None
    order = data.get("order")
    if not isinstance(order, dict):
        order = {}
    return {
        "tipo": "trade_update",
        "recibido_en": recibido_en,
        "event": event.strip(),
        "order_id": _texto(order.get("id")),
        "client_order_id": _texto(order.get("client_order_id")),
        "symbol": _texto(order.get("symbol")),
        "qty": _cantidad(order.get("qty")),
        "filled_qty": _cantidad(order.get("filled_qty")),
        "event_qty": _cantidad(data.get("qty")),
        "price": _cantidad(data.get("price")),
        "timestamp": _texto(data.get("timestamp")),
        "status": _texto(order.get("status")),
        "execution_id": _texto(data.get("execution_id")),
    }


def mensaje_auth(api_key: str, api_secret: str) -> dict:
    """La documentación del stream pide `action: auth` con la clave y
    el secreto en la raíz (no el `authenticate` anidado de alpaca-py).
    Si el paper rechaza este formato, el proceso se detiene: no prueba
    el otro a ciegas."""
    return {"action": "auth", "key": api_key, "secret": api_secret}


def mensaje_listen() -> dict:
    return {"action": "listen", "data": {"streams": ["trade_updates"]}}


def estado_auth(msg) -> str:
    """Un token corto. Nunca el texto que mandó el servidor: podría
    hacernos eco de la clave."""
    if msg is None:
        return "sin_respuesta"
    if not isinstance(msg, dict):
        return "ilegible"
    data = msg.get("data")
    if not isinstance(data, dict):
        return "sin_status"
    status = data.get("status")
    if not isinstance(status, str) or not status.strip():
        return "sin_status"
    s = status.strip().lower()
    if s in _STATUS_AUTH_CONOCIDOS:
        return s
    return "otro"


def autorizado(msg) -> bool:
    return estado_auth(msg) == "authorized"


def escuchando(msg) -> bool:
    if not isinstance(msg, dict) or msg.get("stream") != "listening":
        return False
    data = msg.get("data")
    if not isinstance(data, dict):
        return False
    streams = data.get("streams")
    if not isinstance(streams, list):
        return False
    return "trade_updates" in streams


def es_error_de_stream(msg) -> bool:
    return isinstance(msg, dict) and msg.get("action") == "error"


def _secretos_utiles(secretos: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(s for s in secretos if isinstance(s, str) and len(s) >= _LARGO_MINIMO_SECRETO)


def _redactar(valor, secretos: tuple[str, ...]):
    utiles = _secretos_utiles(secretos)
    if isinstance(valor, str):
        out = valor
        for s in utiles:
            if s in out:
                out = out.replace(s, "***")
        return out
    if isinstance(valor, dict):
        return {k: _redactar(v, secretos) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_redactar(v, secretos) for v in valor]
    return valor


def fusionar(previo: dict | None, nuevo: dict) -> dict:
    """Último estado de una orden. Un campo null en el evento nuevo no
    borra el valor anterior: un `canceled` suele no traer `price`, y
    pisarlo sería perder el último precio visto. Un valor presente,
    incluido `"0"`, sí reemplaza."""
    base: dict = {}
    if isinstance(previo, dict):
        for k in _CAMPOS_ESTADO:
            if previo.get(k) is not None:
                base[k] = previo[k]
    for k in _CAMPOS_ESTADO:
        if nuevo.get(k) is not None:
            base[k] = nuevo[k]
    return base


def _cargar_estado(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise EstadoIlegible from ex
    if not isinstance(data, dict) or data.get("version") != VERSION_ESTADO:
        raise EstadoIlegible
    ordenes = data.get("ordenes")
    if not isinstance(ordenes, dict):
        raise EstadoIlegible
    limpio: dict = {}
    for oid, fila in ordenes.items():
        if not isinstance(oid, str) or not isinstance(fila, dict):
            raise EstadoIlegible
        limpio[oid] = fila
    return limpio


class Escritor:
    """JSONL append-only más el estado por orden. El candado es del
    proceso entero: dos sombras no pueden compartir el archivo."""

    def __init__(self, jsonl: Path, estado: Path, ordenes: dict, lock, secretos: tuple[str, ...]) -> None:
        self._jsonl = jsonl
        self._estado = estado
        self._ordenes = ordenes
        self._lock = lock
        self._secretos = secretos
        self.al_anotar = None

    @classmethod
    def abrir(cls, jsonl: Path, estado: Path, secretos: tuple[str, ...] = ()) -> Escritor:
        jsonl.parent.mkdir(parents=True, exist_ok=True)
        estado.parent.mkdir(parents=True, exist_ok=True)
        lock_path = jsonl.with_name(jsonl.name + ".lock")
        lock = open(lock_path, "a+")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise YaCorre
        nuevo = not estado.exists()
        try:
            ordenes = _cargar_estado(estado)
        except Exception:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
            raise
        escritor = cls(jsonl, estado, ordenes, lock, secretos)
        if nuevo:
            # El archivo no existía: un libro vacío con versión es "aún
            # no vimos órdenes", no un estado corrupto que se haya
            # leído como cero.
            escritor._guardar_estado()
        return escritor

    def anotar(self, registro: dict) -> None:
        limpio = _redactar(registro, self._secretos)
        self._append(limpio)
        if limpio.get("tipo") == "trade_update":
            oid = limpio.get("order_id")
            if isinstance(oid, str) and oid:
                self._ordenes[oid] = fusionar(self._ordenes.get(oid), limpio)
                self._guardar_estado()
        if self.al_anotar is not None:
            self.al_anotar(limpio)

    def _append(self, registro: dict) -> None:
        linea = json.dumps(registro, ensure_ascii=False, separators=(",", ":"))
        with self._jsonl.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _guardar_estado(self) -> None:
        payload = {"version": VERSION_ESTADO, "ordenes": self._ordenes}
        tmp = self._estado.with_name(self._estado.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._estado)

    def cerrar(self) -> None:
        if self._lock is None:
            return
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock.close()
            self._lock = None


class Vigilancia:
    """Heartbeat mientras el socket calla, y un hueco si el callar se
    alarga. El hueco se anota una sola vez hasta que vuelve un frame."""

    def __init__(self, heartbeat_s: float = HEARTBEAT_S, hueco_s: float = HUECO_S) -> None:
        self.heartbeat_s = heartbeat_s
        self.hueco_s = hueco_s
        self.ultimo: datetime | None = None
        self.ultimo_heartbeat: datetime | None = None
        self.hueco_abierto = False

    def silencio_s(self, ahora: datetime) -> float | None:
        if self.ultimo is None:
            return None
        return round((ahora - self.ultimo).total_seconds(), 3)

    def recibido(self, ahora: datetime) -> list[dict]:
        regs: list[dict] = []
        if self.hueco_abierto and self.ultimo is not None:
            regs.append({
                "tipo": "hueco_cerrado",
                "recibido_en": _iso(ahora),
                "silencio_s": round((ahora - self.ultimo).total_seconds(), 3),
            })
        self.ultimo = ahora
        self.ultimo_heartbeat = ahora
        self.hueco_abierto = False
        return regs

    def revisar(self, ahora: datetime) -> list[dict]:
        if self.ultimo is None:
            self.ultimo = ahora
            self.ultimo_heartbeat = ahora
            return []
        silencio = (ahora - self.ultimo).total_seconds()
        out: list[dict] = []
        if (
            self.ultimo_heartbeat is None
            or (ahora - self.ultimo_heartbeat).total_seconds() >= self.heartbeat_s
        ):
            out.append({
                "tipo": "heartbeat",
                "recibido_en": _iso(ahora),
                "silencio_s": round(silencio, 3),
                "conectado": True,
            })
            self.ultimo_heartbeat = ahora
        if silencio >= self.hueco_s and not self.hueco_abierto:
            out.append({
                "tipo": "hueco",
                "motivo": "sin_frames",
                "recibido_en": _iso(ahora),
                "silencio_s": round(silencio, 3),
            })
            self.hueco_abierto = True
        return out


def _log_marca(reg: dict) -> None:
    tipo = reg.get("tipo")
    silencio = reg.get("silencio_s")
    if tipo == "heartbeat":
        log.info("heartbeat trade_updates silencio_s=%s", silencio)
    elif tipo == "hueco":
        log.warning(
            "hueco trade_updates motivo=%s silencio_s=%s",
            reg.get("motivo"), silencio,
        )
    elif tipo == "hueco_cerrado":
        log.info("hueco cerrado silencio_s=%s", silencio)


def _largo(frame) -> int:
    try:
        return len(frame)
    except TypeError:
        return -1


class _SocketWebsockets:
    def __init__(self, cm, ws) -> None:
        self._cm = cm
        self._ws = ws

    async def enviar(self, texto: str) -> None:
        await self._ws.send(texto)

    async def recibir(self):
        try:
            return await self._ws.recv()
        except Exception as ex:
            # No se encadena la causa: el mensaje de la librería a veces
            # cita el payload, y el payload de auth lleva el secreto.
            if type(ex).__name__.startswith("ConnectionClosed"):
                raise ConexionCerrada from None
            raise

    async def cerrar(self) -> None:
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception as ex:
            if type(ex).__name__.startswith("ConnectionClosed"):
                return
            log.info("al cerrar el stream (%s)", type(ex).__name__)


async def conectar_paper(url: str) -> _SocketWebsockets:
    afirmar_url_paper(url)
    try:
        import websockets
    except ImportError as ex:
        raise RuntimeError("falta el paquete websockets") from ex
    cm = websockets.connect(
        url,
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        max_queue=1024,
        compression=None,
    )
    ws = await cm.__aenter__()
    return _SocketWebsockets(cm, ws)


async def _recibir(sock, timeout: float, parada: asyncio.Event):
    """El frame, None si venció el silencio, o `ParadaPedida`.

    Se espera el frame y la parada a la vez: un SIGTERM no debe quedarse
    bloqueado hasta el próximo heartbeat."""
    recv_task = asyncio.create_task(sock.recibir())
    stop_task = asyncio.create_task(parada.wait())
    try:
        done, _pending = await asyncio.wait(
            {recv_task, stop_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if recv_task in done:
            return recv_task.result()
        if stop_task in done:
            raise ParadaPedida
        return None
    finally:
        for t in (recv_task, stop_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(recv_task, stop_task, return_exceptions=True)


async def _cerrar(sock) -> None:
    if sock is None:
        return
    try:
        await sock.cerrar()
    except Exception as ex:
        log.info("al cerrar el socket (%s)", type(ex).__name__)


def _anotar(escritor: Escritor, registro: dict) -> str | None:
    """None si se escribió. `"disco"` si no se pudo: seguir escuchando
    sin poder anotarlo sería una sombra que miente."""
    try:
        escritor.anotar(registro)
    except OSError as ex:
        log.error("no se pudo escribir la sombra (%s)", type(ex).__name__)
        return "disco"
    _log_marca(registro)
    return None


async def _consumir(sock, escritor, vigilancia, parada, reloj, espera_recepcion: float) -> str:
    while not parada.is_set():
        try:
            frame = await _recibir(sock, espera_recepcion, parada)
        except ParadaPedida:
            return "parada"
        except ConexionCerrada:
            return "caida"
        except Exception as ex:
            log.warning("recv falló (%s)", type(ex).__name__)
            return "caida"
        ahora = reloj()
        if frame is None:
            for reg in vigilancia.revisar(ahora):
                if _anotar(escritor, reg) == "disco":
                    return "disco"
            if parada.is_set():
                return "parada"
            continue
        msg = decodificar_frame(frame)
        if msg is None:
            log.warning(
                "frame ilegible (%s, %s bytes); no se inventa un evento",
                type(frame).__name__, _largo(frame),
            )
            continue
        if es_error_de_stream(msg):
            # El texto de `error_message` no se guarda: no lo necesitamos
            # para reconectar y podría no ser inocuo.
            log.warning("el stream mandó action=error; se reconecta")
            if _anotar(escritor, {"tipo": "error_stream", "recibido_en": _iso(ahora)}) == "disco":
                return "disco"
            return "caida"
        if msg.get("stream") == "trade_updates":
            for reg in vigilancia.recibido(ahora):
                if _anotar(escritor, reg) == "disco":
                    return "disco"
            ev = evento_de_mensaje(msg, _iso(ahora))
            if ev is None:
                log.warning("trade_updates sin evento usable; no se inventa")
                continue
            if _anotar(escritor, ev) == "disco":
                return "disco"
            if parada.is_set():
                return "parada"
            continue
        if msg.get("stream") in ("listening", "authorization"):
            for reg in vigilancia.recibido(ahora):
                if _anotar(escritor, reg) == "disco":
                    return "disco"
            continue
        log.warning("frame con stream no reconocido; no se inventa un evento")
        for reg in vigilancia.recibido(ahora):
            if _anotar(escritor, reg) == "disco":
                return "disco"
    return "parada"


async def correr(
    *,
    url: str = URL_STREAM_PAPER,
    jsonl: Path | None = None,
    estado: Path | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    conector=None,
    dormir=None,
    parada: asyncio.Event | None = None,
    vigilancia: Vigilancia | None = None,
    ahora=None,
    espera_recepcion: float = HEARTBEAT_S,
    espera_auth: float = ESPERA_AUTH_S,
    max_fallos_auth: int = MAX_FALLOS_AUTH,
    al_anotar=None,
    instalar_senales: bool = False,
) -> int:
    """Escucha hasta que pidan parar. 0 si la parada fue limpia, 1 si
    no se puede seguir (credenciales, auth, disco, estado ilegible)."""
    afirmar_url_paper(url)
    if not api_key or not api_secret:
        log.error("sin credenciales de Alpaca paper; no se conecta")
        return 1
    if parada is None:
        parada = asyncio.Event()
    if instalar_senales:
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, parada.set)
            loop.add_signal_handler(signal.SIGINT, parada.set)
        except NotImplementedError:
            pass
    if conector is None:
        conector = conectar_paper
    if dormir is None:
        dormir = asyncio.sleep
    if vigilancia is None:
        vigilancia = Vigilancia()
    if ahora is None:
        def ahora():
            return datetime.now(UTC)
    jsonl = jsonl or RUTA_JSONL_DEFAULT
    estado = estado or RUTA_ESTADO_DEFAULT
    try:
        escritor = Escritor.abrir(jsonl, estado, secretos=(api_key, api_secret))
    except YaCorre:
        log.error("ya hay otro listener de trade_updates; no se arranca")
        return 1
    except EstadoIlegible:
        log.error("el estado por orden es ilegible; no se arranca ni se reescribe")
        return 1
    except OSError as ex:
        log.error("no se pudo abrir el log sombra (%s)", type(ex).__name__)
        return 1
    escritor.al_anotar = al_anotar

    fallos_auth = 0
    caidas = 0
    try:
        while not parada.is_set():
            sock = None
            codigo = "caida"
            try:
                sock = await conector(url)
                await sock.enviar(json.dumps(mensaje_auth(api_key, api_secret)))
                frame = await _recibir(sock, espera_auth, parada)
                msg = decodificar_frame(frame) if frame is not None else None
                if not autorizado(msg):
                    fallos_auth += 1
                    log.warning("auth rechazada (%s)", estado_auth(msg))
                    if _anotar(escritor, {
                        "tipo": "auth_rechazada",
                        "recibido_en": _iso(ahora()),
                        "status": estado_auth(msg),
                    }) == "disco":
                        return 1
                    if fallos_auth >= max_fallos_auth:
                        log.error("auth rechazada %d veces; no se sigue", fallos_auth)
                        return 1
                    codigo = "auth"
                else:
                    await sock.enviar(json.dumps(mensaje_listen()))
                    frame = await _recibir(sock, espera_auth, parada)
                    msg = decodificar_frame(frame) if frame is not None else None
                    if not escuchando(msg):
                        if estado_auth(msg) == "unauthorized":
                            fallos_auth += 1
                            log.warning("listen rechazado (%s)", estado_auth(msg))
                            if _anotar(escritor, {
                                "tipo": "auth_rechazada",
                                "recibido_en": _iso(ahora()),
                                "status": "unauthorized",
                            }) == "disco":
                                return 1
                            if fallos_auth >= max_fallos_auth:
                                return 1
                            codigo = "auth"
                        else:
                            log.warning("el listen no confirmó trade_updates")
                            codigo = "caida"
                    else:
                        fallos_auth = 0
                        caidas = 0
                        for reg in vigilancia.recibido(ahora()):
                            if _anotar(escritor, reg) == "disco":
                                return 1
                        log.info("escuchando trade_updates")
                        codigo = await _consumir(
                            sock, escritor, vigilancia, parada, ahora, espera_recepcion,
                        )
            except ParadaPedida:
                codigo = "parada"
            except ConexionCerrada:
                codigo = "caida"
            except Exception as ex:
                log.warning("stream caído (%s)", type(ex).__name__)
                codigo = "caida"
            finally:
                await _cerrar(sock)
            if codigo == "parada" or parada.is_set():
                return 0
            if codigo == "disco":
                return 1
            if codigo == "auth":
                await dormir(espera_de_reintento(fallos_auth))
                continue
            caidas += 1
            silencio = vigilancia.silencio_s(ahora())
            # El próximo frame cierra este hueco. Si no se marca, el
            # silencio de la caída se queda abierto en el JSONL.
            vigilancia.hueco_abierto = True
            if _anotar(escritor, {
                "tipo": "hueco",
                "motivo": "reconexion",
                "recibido_en": _iso(ahora()),
                "silencio_s": silencio,
            }) == "disco":
                return 1
            log.warning("reconexión en %.1f s (caída %d)", espera_de_reintento(caidas), caidas)
            await dormir(espera_de_reintento(caidas))
    finally:
        escritor.cerrar()
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Sombra de trade_updates de Alpaca paper. Solo escucha; no coloca órdenes.",
    )
    ap.add_argument(
        "--jsonl",
        default=os.environ.get("MOMENTUM_TRADE_UPDATES_JSONL", str(RUTA_JSONL_DEFAULT)),
        help="JSONL append-only (default /var/lib/momentum/trade_updates.jsonl)",
    )
    ap.add_argument(
        "--estado",
        default=os.environ.get("MOMENTUM_TRADE_UPDATES_ESTADO", str(RUTA_ESTADO_DEFAULT)),
        help="último estado por orden",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(correr(
            jsonl=Path(args.jsonl),
            estado=Path(args.estado),
            api_key=os.environ.get("ALPACA_PAPER_API_KEY"),
            api_secret=os.environ.get("ALPACA_PAPER_API_SECRET"),
            instalar_senales=True,
        ))
    except UrlNoEsPaper:
        log.error("no se arranca: el stream no es el host paper")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
