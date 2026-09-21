"""State Engine persistido -- "Fase 2" (pedido explícito 2026-08-11):
"el bot está funcionando como radar, no como un detector de entrada...
quiero que me avise ÚNICAMENTE cuando una oportunidad esté realmente
lista para entrar... NO quiero que Telegram reciba mensajes constantes
mientras una acción está en WATCHING".

Antes de este módulo, cada corrida recalculaba todo desde cero -- un
candidato "casi" (catalizador confirmado, pero sin patrón o sin volumen
todavía) desaparecía en silencio hasta la siguiente corrida completa, sin
memoria de cuándo empezó a vigilarse ni de qué pasó mientras tanto. Este
módulo agrega esa memoria: una lista persistida (mismo patrón JSON que
`tracker.py`/`heartbeat.py`, committeada por el workflow) donde cada
ticker vive en uno de cinco estados, con el historial COMPLETO de
transiciones y su timestamp -- Principio 9 (auditoría reconstruible)
aplicado también a "por qué este ticker entró a vigilancia y qué pasó
después", no solo a las alertas que sí se mandaron.

Seis estados:

  WATCHING    -- catalizador confirmado, en observación. Nunca genera
                 un mensaje de Telegram por sí solo (eso sería
                 exactamente el ruido que se pidió evitar).
  TRIGGERED   -- se volvió `accionable` (las 5 preguntas de
                 `evaluator.py` + el veto de riesgo/recompensa) --
                 termina en un mensaje de entrada inmediato. El
                 buscador NO la re-evalúa. Deja de ser el final del
                 ciclo cuando el paper trader registra un desenlace
                 terminal (revisión que rechaza, o fill ya cerrado):
                 entonces pasa a ARCHIVED. Sin esa transición, NTLA y
                 BEAM se quedaron `triggered` días después de haber
                 sido revisadas -- ver `marcar_archivada`.
  INVALIDATED -- el catalizador que lo puso en vigilancia ya no es
                 válido (`evaluator` cortó el análisis en la pregunta 1
                 al re-evaluar).
  MISSED      -- el patrón se formó pero ya no estamos a tiempo
                 (`early_opportunity` dice "tarde") -- la regla de "no
                 perseguir" aplicada al ciclo de vigilancia, no solo a
                 la corrida que lo descubrió.
  EXPIRED     -- lleva más de `cfg.minutos_maximos_en_watching` sin
                 resolver -- una candidata de hace dos horas ya no es
                 la misma oportunidad que la que se detectó.
  ARCHIVED    -- TRIGGERED cuyo desenlace paper ya existe. No se
                 borra: se agrega una transición y el paper trader
                 appendea un JSONL durable. El buscador no inventa
                 este estado -- solo lo acepta.

INVALIDATED/MISSED/EXPIRED/ARCHIVED son terminales de verdad. TRIGGERED
es terminal para el buscador (no se reabre ni se re-dispara) pero no
para el ciclo paper: puede pasar a ARCHIVED. El historial de
transiciones NUNCA se borra al cambiar de estado -- queda en el
archivo para reconstruir la sesión, ver `marcar_triggered`.

Snapshot congelado desde el descubrimiento (`desde_candidato_diario`):
catalizador y metadata NO se vuelven a pedir en cada re-chequeo (esos
datos no cambian minuto a minuto, y volver a pedir noticias para 20-50
tickers cada 5 minutos sería gastar llamadas de red sin necesidad) --
solo las velas intradía se piden frescas en cada ciclo. `run.py` es
quien orquesta cuándo se descubre (corrida completa, cada ~30 min) y
cuándo se re-chequea (corrida liviana, cada ~5 min -- el mínimo real que
garantiza GitHub Actions, ver README)."""

from __future__ import annotations

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter.catalysts.detector import Catalizador, dentro_de_ventana
from momentum_hunter.models import Metadata

log = logging.getLogger("momentum_hunter.watchlist")

PATH = Path(__file__).resolve().parent / "watchlist.json"

# Estado runtime del VPS -- FUERA del repo a propósito. Escribir PATH
# desde `--solo-watchlist` sucia el worktree y rompe `git pull --rebase`
# (medido 2026-09-18). GHA sigue siendo el único escritor del canónico.
STATE_PATH_DEFAULT = Path("/var/lib/momentum/watchlist_vps_state.json")
ENV_VPS_STATE = "MOMENTUM_WATCHLIST_VPS_STATE"
ENV_STATE_PATH = "MOMENTUM_WATCHLIST_STATE"

# Campos que el rechequeo VPS sí muta. Alta, catalizador y snapshot
# (nombre, float, score, gap…) se quedan en el canónico de GHA.
CAMPOS_OVERLAY = (
    "estado",
    "actualizado_en",
    "tarde_consecutivas",
    "market_event_ts",
    "data_received_ts",
    "evaluador_ts",
    "mensaje_generado_ts",
    "telegram_enviado_ts",
    "signal_latency_ms",
    "velas_desde_ruptura",
    "watchlist_escrito_ts",
    "ultima_entrada",
    "ultimo_stop",
    "ultimo_objetivo",
    "ultima_zona_entrada_baja",
    "ultimos_niveles_ts",
    "stop_tesis",
    "clima_mercado",
)

ESTADO_WATCHING = "watching"
ESTADO_TRIGGERED = "triggered"
ESTADO_INVALIDATED = "invalidated"
ESTADO_MISSED = "missed"
ESTADO_EXPIRED = "expired"
ESTADO_ARCHIVED = "archived"
# TRIGGERED sigue acá: el buscador no la re-vigila. ARCHIVED es el
# terminal post-paper. Ambos se purgan a los 7 días; el JSONL del
# paper trader es el registro que no se va con esa purga.
ESTADOS_TERMINALES = frozenset({
    ESTADO_TRIGGERED, ESTADO_INVALIDATED, ESTADO_MISSED, ESTADO_EXPIRED,
    ESTADO_ARCHIVED,
})


class EscrituraWatchlistCanonicaProhibida(RuntimeError):
    """`--solo-watchlist` en VPS intentó escribir el JSON canónico.

    Fallar fuerte es a propósito: un call site que se escape volvería a
    suciar el worktree y rompería `git pull --rebase`. Rollback:
    `MOMENTUM_WATCHLIST_VPS_STATE=0`.
    """


# Guardia de proceso: ON solo durante `revisar_watchlist` con el flag.
_prohibir_escritura_canonica = False


@dataclass(frozen=True)
class Transicion:
    estado: str
    timestamp: str   # ISO 8601 UTC -- también es "el timestamp de la transición" para latencia
    motivo: str
    # -- Latencia POR TRANSICIÓN (pedido explícito 2026-08-11, integración
    # de Telegram: "para cada transición registra timestamp de detección,
    # evaluación, transición, generación del mensaje, envío a Telegram").
    # Antes esto solo existía para TRIGGERED (ver los campos a nivel
    # `EntradaWatchlist` más abajo, que se conservan tal cual para no
    # romper esa medición ya probada); esto lo generaliza a las 5
    # transiciones sin duplicar la idea. Todo opcional y `None` por
    # defecto -- nunca se inventa un timestamp que no se midió de verdad
    # (ver `completar_latencia_transicion`).
    deteccion_ts: str | None = None      # reloj: cuándo llegaron los datos que originaron esta transición
    evaluacion_ts: str | None = None     # reloj: cuándo la lógica de decisión (evaluator o el TTL) resolvió esto
    mensaje_generado_ts: str | None = None
    telegram_enviado_ts: str | None = None
    latencia_desde_deteccion_ms: float | None = None    # None si esta transición no tiene un dato puntual de origen
    latencia_desde_evaluacion_ms: float | None = None
    latencia_desde_transicion_ms: float | None = None    # SIEMPRE calculable -- referencia común a las 5 transiciones


@dataclass
class EntradaWatchlist:
    ticker: str
    nombre: str | None
    estado: str
    creado_en: str        # ISO UTC -- cuándo entró a WATCHING por primera vez
    actualizado_en: str   # ISO UTC de la última transición
    transiciones: list[Transicion] = field(default_factory=list)
    # -- Snapshot congelado desde el descubrimiento, ver docstring del módulo --
    catalizador_tipo: str | None = None
    catalizador_titular: str | None = None
    catalizador_fuente: str | None = None
    catalizador_fecha: str | None = None
    catalizador_confirmado: bool = True
    catalizador_fuentes_adicionales: tuple[str, ...] = field(default_factory=tuple)
    shares_float: float | None = None
    short_pct_float: float | None = None
    es_large_cap: bool = False
    score_base: float = 0.0
    atr_diario: float | None = None
    # Congelado desde el descubrimiento -- el gap de apertura no cambia
    # durante el resto de la sesión, así el chequeo liviano no necesita
    # volver a pedir barras DIARIAS (solo intradía) para calcularlo.
    gap_pct_congelado: float | None = None
    # Corrección 2026-08-11 (revisión de PR): el veredicto "tarde" de
    # `early_opportunity` es una lectura del INSTANTE actual (extensión
    # desde VWAP/EMA9, velas desde la ruptura) -- ambas condiciones
    # pueden revertirse (un retroceso resetea la extensión, una vela por
    # debajo del nivel resetea el conteo). Antes, UNA sola lectura
    # "tarde" mandaba la candidata a MISSED (terminal), apagando la
    # vigilancia continua con un solo dato ruidoso. Este contador exige
    # ver "tarde" en `cfg.verificaciones_tarde_para_missed` chequeos
    # SEGUIDOS antes de comprometerse -- se resetea a 0 en cualquier
    # lectura que no sea "tarde" (ver `run._evaluar_no_disparada`).
    tarde_consecutivas: int = 0
    # -- Latencia (pedido explícito): solo se llenan al pasar a TRIGGERED --
    market_event_ts: str | None = None       # timestamp de la VELA que confirmó (dato, no reloj)
    data_received_ts: str | None = None      # reloj: cuándo llegaron esas velas
    evaluador_ts: str | None = None          # reloj: cuándo evaluator.evaluar() dijo accionable
    mensaje_generado_ts: str | None = None   # reloj: cuándo se armó el texto del mensaje
    telegram_enviado_ts: str | None = None   # reloj: cuándo enviar_telegram() devolvió
    signal_latency_ms: float | None = None   # telegram_enviado_ts - market_event_ts
    # Velas de 1 min que el cierre llevaba sobre el nivel de ruptura AL
    # disparar (`velas_desde_ruptura` de los factores de esa evaluación).
    # Solo se guarda: no cambia umbrales ni la regla de "tarde". Sirve para
    # medir la latencia completa ruptura -> orden (esto + velas desde el
    # disparo). None = no se midió; nunca se asume 0.
    velas_desde_ruptura: int | None = None
    # Reloj de la PRIMERA vez que esta entrada TRIGGERED se persistió
    # en disco. Distinto de `actualizado_en` (cambio de estado en
    # memoria): es el instante en que otra corrida -- u otro proceso
    # que solo lee este archivo -- pudo ver el disparo. Se llena en
    # `guardar`, nunca se inventa, y no se reescribe si ya existe
    # (la segunda persistencia, la de la latencia de Telegram, no
    # debe mover el origen de esta medición).
    watchlist_escrito_ts: str | None = None
    # -- Últimos niveles calculados (2026-08-11, integración de Telegram):
    # `/trade` es un comando de SOLO LECTURA, sin acceso a datos de mercado
    # en vivo -- "NO debe crear una nueva oportunidad... debe leer el
    # estado existente y mostrar la información YA calculada". Esto cachea
    # exactamente lo que la corrida normal (`run.py`) de todas formas ya
    # calculó en el chequeo más reciente (`report.niveles_entrada_salida`/
    # `zona_entrada`) -- ningún cálculo nuevo, solo persistencia (ver
    # `actualizar_niveles`).
    ultima_entrada: float | None = None
    ultimo_stop: float | None = None
    ultimo_objetivo: float | None = None
    ultima_zona_entrada_baja: float | None = None   # nivel de ruptura -- "la entrada que se esperaba"
    ultimos_niveles_ts: str | None = None
    # Congelado la PRIMERA vez que se calcularon niveles y nunca
    # reescrito (ver `actualizar_niveles`): el nivel que hacía válida la
    # idea cuando la tuvimos. `ultimo_stop` se recalcula en cada chequeo
    # y por eso persigue al precio; este no. Es contra este contra el que
    # se mide si la tesis se rompió (ver `run._evaluar_no_disparada`).
    stop_tesis: float | None = None
    # Cómo venía el mercado GENERAL en el chequeo más reciente
    # ("favorable"/"debil"/"desconocido", ver `mercado.py`). Viaja en la
    # entrada porque la watchlist es el único canal entre el buscador y
    # el ejecutor: así la capa de decisión con IA puede pesarlo sin
    # volver a pedir datos ni duplicar la lógica. `None` = todavía no se
    # midió (entradas creadas antes del 2026-08-21).
    clima_mercado: str | None = None


def catalizador_de(e: EntradaWatchlist) -> Catalizador | None:
    """Reconstruye el `Catalizador` congelado -- None si la entrada se
    creó sin uno (no debería pasar en la práctica: solo se entra a
    WATCHING con catalizador confirmado, pero nunca se asume)."""
    if e.catalizador_tipo is None:
        return None
    return Catalizador(
        tipo=e.catalizador_tipo, titular=e.catalizador_titular or "",
        fuente=e.catalizador_fuente or "", fecha=e.catalizador_fecha,
        confirmado=e.catalizador_confirmado,
        fuentes_adicionales=tuple(e.catalizador_fuentes_adicionales),
    )


def meta_de(e: EntradaWatchlist) -> Metadata:
    """Metadata mínima reconstruida -- solo los campos que
    `evaluator._hay_desequilibrio` realmente lee; el resto no hace
    falta para re-evaluar."""
    return Metadata(ticker=e.ticker, shares_float=e.shares_float, short_pct_float=e.short_pct_float)


def catalizador_vigente(e: EntradaWatchlist, dias_ventana: int, ahora: datetime) -> bool:
    """¿El catalizador congelado sigue dentro de la misma ventana que ya
    exige `catalysts.detector.dentro_de_ventana` al descubrirlo? Un
    catalizador reconstruido siempre tiene `confirmado=True` (se congela
    tal cual se confirmó, nunca cambia) -- por eso INVALIDATED no puede
    salir de re-evaluar el catalizador con `evaluator.evaluar` (ese
    branch nunca se activaría dos veces sobre el mismo dato). Esta es la
    señal real y medible de que el catalizador ya envejeció, reutilizando
    la misma regla de días en vez de inventar una nueva."""
    return dentro_de_ventana(e.catalizador_fecha, ahora.date(), dias_ventana)


def _ahora_iso(ahora: datetime) -> str:
    return ahora.isoformat(timespec="seconds")


def _transicionar(
    e: EntradaWatchlist, estado: str, motivo: str, ahora: datetime,
    deteccion_ts: str | None = None, evaluacion_ts: str | None = None,
) -> None:
    ts = _ahora_iso(ahora)
    e.estado = estado
    e.actualizado_en = ts
    e.transiciones.append(Transicion(
        estado=estado, timestamp=ts, motivo=motivo,
        deteccion_ts=deteccion_ts, evaluacion_ts=evaluacion_ts,
    ))


def completar_latencia_transicion(
    e: EntradaWatchlist, mensaje_generado_ts: str, telegram_enviado_ts: str,
) -> None:
    """Completa la ÚLTIMA transición registrada con los dos timestamps que
    solo se conocen DESPUÉS del intento real de envío (armar el texto,
    mandarlo a Telegram) -- generaliza `registrar_latencia` (que solo
    cubría TRIGGERED) a las 5 transiciones. `Transicion` es inmutable, así
    que se reemplaza por una copia (`dataclasses.replace`).

    Tres métricas, cada una `None` si su punto de partida no existe --
    nunca se inventa un timestamp que no se midió:
      - `latencia_desde_deteccion_ms`: "¿cuánto tardó desde que llegaron
        los datos que mostraron la señal?" -- `None` para transiciones sin
        un dato puntual de origen (EXPIRED es un TTL de reloj, no un dato
        de mercado).
      - `latencia_desde_evaluacion_ms`: "¿cuánto tardó desde que la
        acción se volvió accionable/se resolvió el veredicto?".
      - `latencia_desde_transicion_ms`: desde el instante en que el
        State Engine cambió de estado -- SIEMPRE calculable, la métrica
        de referencia común a las 5 transiciones."""
    if not e.transiciones:
        return
    ultima = e.transiciones[-1]

    def _delta_ms(inicio_iso: str | None) -> float | None:
        if inicio_iso is None:
            return None
        try:
            inicio = datetime.fromisoformat(inicio_iso)
            fin = datetime.fromisoformat(telegram_enviado_ts)
        except ValueError:
            return None
        return round((fin - inicio).total_seconds() * 1000.0, 1)

    e.transiciones[-1] = replace(
        ultima,
        mensaje_generado_ts=mensaje_generado_ts,
        telegram_enviado_ts=telegram_enviado_ts,
        latencia_desde_deteccion_ms=_delta_ms(ultima.deteccion_ts),
        latencia_desde_evaluacion_ms=_delta_ms(ultima.evaluacion_ts),
        latencia_desde_transicion_ms=_delta_ms(ultima.timestamp),
    )


def desde_candidato_diario(
    c, ahora: datetime, deteccion_ts: str | None = None, evaluacion_ts: str | None = None,
) -> EntradaWatchlist:
    """Nueva entrada en WATCHING a partir de un `CandidatoDiario` de la
    etapa 1 (con catalizador ya confirmado -- `run.py` solo llama esto
    para candidatos que ya pasaron ese filtro).

    `deteccion_ts`/`evaluacion_ts` (2026-08-11, integración de Telegram):
    los mismos relojes que ya captura `run.main()` para la etapa 2
    (llegada de las velas intradía / veredicto del evaluador) -- quedan
    en la transición inicial WATCHING para que el mensaje de vigilancia
    también tenga latencia medida, no solo TRIGGERED."""
    cat = c.catalizador
    ts = _ahora_iso(ahora)
    return EntradaWatchlist(
        ticker=c.ticker, nombre=c.nombre, estado=ESTADO_WATCHING,
        creado_en=ts, actualizado_en=ts,
        transiciones=[Transicion(
            estado=ESTADO_WATCHING, timestamp=ts,
            motivo="Catalizador confirmado -- en observación.",
            deteccion_ts=deteccion_ts, evaluacion_ts=evaluacion_ts,
        )],
        catalizador_tipo=cat.tipo if cat else None,
        catalizador_titular=cat.titular if cat else None,
        catalizador_fuente=cat.fuente if cat else None,
        catalizador_fecha=cat.fecha if cat else None,
        catalizador_confirmado=cat.confirmado if cat else True,
        catalizador_fuentes_adicionales=cat.fuentes_adicionales if cat else (),
        shares_float=c.meta.shares_float, short_pct_float=c.meta.short_pct_float,
        es_large_cap=c.es_large_cap, score_base=c.puntuacion.score_total,
        atr_diario=c.factores.atr,
    )


_CAMPOS_ENTRADA = {f.name for f in fields(EntradaWatchlist)}


def parsear(data: object) -> list[EntradaWatchlist]:
    """El parseo puro (dict ya cargado -> entradas) -- separado de
    `cargar()` (2026-08-11, integración de Telegram) para que otros
    procesos SIN filesystem local (el servicio de Telegram en Render, que
    lee este mismo archivo vía la API de contenidos de GitHub) puedan
    reutilizar exactamente esta misma tolerancia a corrupción en vez de
    reimplementarla -- "no dupliques la lógica de estados en Telegram".
    Una entrada individual corrupta se descarta sola, sin perder las
    demás -- por eso TODO el parseo de una entrada (incluidas sus
    transiciones) vive dentro del mismo try/except, no solo la
    construcción final de `EntradaWatchlist`."""
    if not isinstance(data, dict):
        log.warning("watchlist con formato inesperado (no es un objeto); se reinicia vacía")
        return []
    entradas = []
    for d in data.get("entradas", []):
        try:
            d = dict(d)
            # Un campo que esta versión no conoce (lo escribió una versión
            # más nueva) se ignora en vez de descartar la entrada entera.
            desconocidos = sorted(set(d) - _CAMPOS_ENTRADA)
            for campo in desconocidos:
                d.pop(campo)
            if desconocidos:
                log.info("entrada %s: campos desconocidos ignorados: %s", d.get("ticker"), desconocidos)
            transiciones = [Transicion(**t) for t in d.pop("transiciones", [])]
            if "catalizador_fuentes_adicionales" in d:
                # JSON no tiene tuplas -- se recarga como lista; se restaura
                # el tipo declarado para que `catalizador_de` y cualquier
                # comparación por igualdad se comporten igual que en la
                # entrada recién creada (nunca persistida/recargada).
                d["catalizador_fuentes_adicionales"] = tuple(d["catalizador_fuentes_adicionales"])
            entradas.append(EntradaWatchlist(transiciones=transiciones, **d))
        except (TypeError, ValueError, AttributeError) as e:
            log.warning("entrada de watchlist corrupta (%s); se descarta", e)
    return entradas


def cargar(
    path: Path = PATH, apply_vps_state: bool = False,
) -> list[EntradaWatchlist]:
    """Un archivo corrupto no debe tumbar la corrida -- se ignora y se
    reinicia vacía (mismo principio que `heartbeat.cargar_estado`).

    `apply_vps_state=True` aplica el overlay VPS encima del canónico.
    GHA no lo usa: el state file no se consume fuera del host VPS (v1)."""
    if not path.exists():
        entradas: list[EntradaWatchlist] = []
    else:
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("watchlist corrupta (%s); se reinicia vacía", e)
            data = None
        entradas = [] if data is None else parsear(data)
    if apply_vps_state:
        return aplicar_overlay(entradas)
    return entradas


def cargar_con_overlay(path: Path = PATH) -> list[EntradaWatchlist]:
    """Canónico de solo lectura + overlay VPS. Misma verdad operativa
    que `--solo-watchlist` en el host: el paper trader del VPS tiene que
    ver el TRIGGERED local, no la foto de GHA."""
    return aplicar_overlay(cargar(path, apply_vps_state=False))


def _es_path_canonico(path: Path) -> bool:
    try:
        return Path(path).resolve() == PATH.resolve()
    except OSError:
        return Path(path) == PATH


def _sellar_watchlist_escrito_ts(
    entradas: list[EntradaWatchlist], ahora: datetime | None,
) -> str:
    escrito = _ahora_iso(ahora or datetime.now(UTC))
    for e in entradas:
        if e.estado == ESTADO_TRIGGERED and e.watchlist_escrito_ts is None:
            e.watchlist_escrito_ts = escrito
    return escrito


def guardar(
    entradas: list[EntradaWatchlist], path: Path = PATH, ahora: datetime | None = None,
) -> None:
    """Persiste la watchlist. Como efecto secundario de instrumentación,
    sella `watchlist_escrito_ts` en cada TRIGGERED que todavía no lo
    tiene -- el dato solo existe en el momento de escribir, y no hay
    otro sitio honesto donde tomarlo. No cambia estados ni niveles.

    Con la guardia VPS activa, escribir el PATH canónico explota: un
    call site escapado no puede suciar el worktree en silencio."""
    if _prohibir_escritura_canonica and _es_path_canonico(path):
        raise EscrituraWatchlistCanonicaProhibida(
            f"VPS --solo-watchlist no puede escribir {PATH}; "
            "usar guardar_vps_state (flag MOMENTUM_WATCHLIST_VPS_STATE)"
        )
    _sellar_watchlist_escrito_ts(entradas, ahora)
    data = {"entradas": [asdict(e) for e in entradas]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def vps_state_habilitado() -> bool:
    """Feature flag. Default OFF: GHA y tests siguen escribiendo PATH.
    El wrapper VPS exporta `MOMENTUM_WATCHLIST_VPS_STATE=1`. Rollback:
    `=0` restaura `guardar` → PATH."""
    return os.environ.get(ENV_VPS_STATE, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def state_path() -> Path:
    raw = os.environ.get(ENV_STATE_PATH, "").strip()
    return Path(raw) if raw else STATE_PATH_DEFAULT


def activar_prohibicion_canonica() -> None:
    global _prohibir_escritura_canonica
    _prohibir_escritura_canonica = True


def desactivar_prohibicion_canonica() -> None:
    global _prohibir_escritura_canonica
    _prohibir_escritura_canonica = False


@contextmanager
def prohibir_escritura_canonica():
    """Durante `--solo-watchlist` con flag ON: `guardar(PATH)` explota."""
    activar_prohibicion_canonica()
    try:
        yield
    finally:
        desactivar_prohibicion_canonica()


def _parse_ts(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _escribir_json_atomico(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o640)
    except OSError:
        pass


def _transicion_a_overlay(t: Transicion) -> dict:
    d = {"a": t.estado, "motivo": t.motivo, "en": t.timestamp}
    for campo in (
        "deteccion_ts", "evaluacion_ts", "mensaje_generado_ts",
        "telegram_enviado_ts", "latencia_desde_deteccion_ms",
        "latencia_desde_evaluacion_ms", "latencia_desde_transicion_ms",
    ):
        val = getattr(t, campo)
        if val is not None:
            d[campo] = val
    return d


_CAMPOS_TRANSICION = {f.name for f in fields(Transicion)}


def _transicion_desde_overlay(d: object) -> Transicion | None:
    if not isinstance(d, dict):
        return None
    estado = d.get("a") or d.get("estado")
    ts = d.get("en") or d.get("timestamp")
    if not estado or not ts:
        return None
    kwargs = {
        "estado": estado,
        "timestamp": ts,
        "motivo": d.get("motivo") or "",
    }
    for campo in _CAMPOS_TRANSICION:
        if campo in kwargs or campo not in d:
            continue
        kwargs[campo] = d[campo]
    try:
        return Transicion(**kwargs)
    except TypeError:
        return None


def _clave_transicion(t: Transicion) -> tuple[str, str, str]:
    return (t.estado, t.timestamp, t.motivo)


def _transicion_mas_completa(nueva: Transicion, vieja: Transicion) -> bool:
    """La overlay puede completar latencia de Telegram sobre la misma
    transición: mismo (estado, ts, motivo), más campos medidos."""
    if _clave_transicion(nueva) != _clave_transicion(vieja):
        return False
    return (
        (nueva.telegram_enviado_ts is not None and vieja.telegram_enviado_ts is None)
        or (nueva.mensaje_generado_ts is not None and vieja.mensaje_generado_ts is None)
        or (nueva.latencia_desde_transicion_ms is not None
            and vieja.latencia_desde_transicion_ms is None)
    )


def _append_transiciones(e: EntradaWatchlist, overlay: dict) -> None:
    por_clave = {_clave_transicion(t): i for i, t in enumerate(e.transiciones)}
    for raw in overlay.get("transiciones_append") or []:
        t = _transicion_desde_overlay(raw)
        if t is None:
            continue
        idx = por_clave.get(_clave_transicion(t))
        if idx is None:
            e.transiciones.append(t)
            por_clave[_clave_transicion(t)] = len(e.transiciones) - 1
        elif _transicion_mas_completa(t, e.transiciones[idx]):
            e.transiciones[idx] = t


def _entrada_a_overlay(e: EntradaWatchlist, overlay_ts: str) -> dict:
    d = {campo: getattr(e, campo) for campo in CAMPOS_OVERLAY}
    d["overlay_ts"] = overlay_ts
    d["transiciones_append"] = [_transicion_a_overlay(t) for t in e.transiciones]
    return d


def cargar_vps_state(path: Path | None = None) -> dict:
    """State ilegible → overlay vacío. Un campo ausente no es evidencia."""
    path = path or state_path()
    if not path.exists():
        return {"schema": 1, "entries": {}}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("state VPS corrupto (%s); se ignora el overlay", type(e).__name__)
        return {"schema": 1, "entries": {}}
    if not isinstance(data, dict):
        log.warning("state VPS con formato inesperado; se ignora el overlay")
        return {"schema": 1, "entries": {}}
    if data.get("schema") not in (None, 1):
        log.warning("state VPS schema=%s desconocido; se ignora el overlay", data.get("schema"))
        return {"schema": 1, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {"schema": 1, "entries": {}}
    return data


def lock_path(path: Path | None = None) -> Path:
    """Candado de ESCRITURA de la watchlist en el VPS, al lado del state
    file. Lo toman solo las escrituras (overlay, canónico fusionado,
    materialización): milisegundos. Nunca se sostiene durante un escaneo
    ni un rechequeo, así que el rechequeo jamás espera al escaneo."""
    base = path or state_path()
    return base.with_name(base.name + ".lock")


@contextmanager
def _candado(path: Path | None = None):
    ruta = lock_path(path)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fh = ruta.open("a")
    except OSError as e:
        # Sin candado (p. ej. /var/lib no escribible en una prueba local):
        # se escribe igual. Un candado imposible no puede tumbar la corrida.
        log.warning("sin candado de watchlist (%s): se escribe sin él", type(e).__name__)
        yield
        return
    with fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def guardar_vps_state(
    entradas: list[EntradaWatchlist],
    path: Path | None = None,
    ahora: datetime | None = None,
) -> None:
    """Persiste SOLO el overlay. Nunca escribe PATH.

    `overlay_ts` existe porque `actualizar_niveles` y `tarde_consecutivas`
    no tocan `actualizado_en`: sin un reloj de escritura, el merge por
    empate devolvería siempre GHA y se perderían esos mutadores.

    Bajo candado (ver `lock_path`): el escaneo del VPS escribe el
    canónico fusionado desde otro proceso y ninguno debe ver una
    escritura a medias del otro."""
    path = path or state_path()
    if _es_path_canonico(path):
        raise EscrituraWatchlistCanonicaProhibida(
            f"guardar_vps_state se negó a escribir el PATH canónico ({PATH})"
        )
    escrito = _sellar_watchlist_escrito_ts(entradas, ahora)
    data = {
        "schema": 1,
        "updated_at": escrito,
        "source": "vps-solo-watchlist",
        "entries": {
            e.ticker: _entrada_a_overlay(e, escrito) for e in entradas
        },
    }
    with _candado(path):
        _escribir_json_atomico(path, data)
    log.info("watchlist VPS state escrito en %s (%d ticker(s))", path, len(entradas))


def guardar_canonico_fusionado(
    entradas: list[EntradaWatchlist],
    path: Path = PATH,
    state: Path | None = None,
    ahora: datetime | None = None,
) -> list[EntradaWatchlist]:
    """Escritura del ESCANEO en el VPS: el canónico, con el overlay
    aplicado en el instante de escribir.

    POR QUÉ. El escaneo dura ~9 min y arrancó con una foto. Mientras
    tanto el rechequeo (cada 5 min) pudo disparar, invalidar o refrescar
    niveles en el overlay. Escribir la foto tal cual pisaría eso. Se
    vuelve a aplicar el overlay justo antes de escribir, con las mismas
    reglas de fusión de siempre (`_fusionar_overlay`), bajo el candado,
    y se devuelve lo que quedó escrito. Los tickers NUEVOS del escaneo
    no están en el overlay y pasan tal cual: por eso el escaneo escribe
    el canónico y no el overlay (el overlay guarda solo campos de estado,
    no una entrada completa)."""
    with _candado(state):
        fusionadas = aplicar_overlay(entradas, state)
        # Con el path por defecto se llama a `guardar` sin path: así un
        # `guardar` redirigido (pruebas, wrappers) sigue mandando.
        if path == PATH:
            guardar(fusionadas, ahora=ahora)
        else:
            guardar(fusionadas, path, ahora)
    log.info("watchlist canónica escrita con overlay aplicado (%d entrada(s))", len(fusionadas))
    return fusionadas


def materializar_overlay(path: Path = PATH, state: Path | None = None) -> int:
    """Vuelca canónico+overlay al canónico, bajo candado, para que el
    VPS pueda commitear `watchlist.json` como dueño. Idempotente: si el
    overlay no cambia nada, el archivo queda igual. Devuelve cuántas
    entradas se escribieron."""
    with _candado(state):
        entradas = aplicar_overlay(cargar(path, apply_vps_state=False), state)
        if path == PATH:
            guardar(entradas)
        else:
            guardar(entradas, path)
    return len(entradas)


def _overlay_mas_nuevo(canon: EntradaWatchlist, overlay: dict) -> bool:
    ts_canon = _parse_ts(canon.actualizado_en)
    if ts_canon is None:
        return False
    candidatos = []
    for key in ("actualizado_en", "overlay_ts", "ultimos_niveles_ts"):
        t = _parse_ts(overlay.get(key) if isinstance(overlay.get(key), str) else None)
        if t is not None:
            candidatos.append(t)
    if not candidatos:
        return False
    return max(candidatos) > ts_canon


def _aplicar_campos_overlay(
    e: EntradaWatchlist, overlay: dict, *, incluir_estado: bool,
) -> None:
    for campo in CAMPOS_OVERLAY:
        if campo not in overlay:
            continue
        if campo == "estado" and not incluir_estado:
            continue
        valor = overlay[campo]
        if valor is None:
            continue
        setattr(e, campo, valor)


def _overlay_decidio_antes(canon: EntradaWatchlist, overlay: dict) -> bool:
    """Los dos lados llegaron a un estado terminal distinto (p. ej. el
    rechequeo disparó TRIGGERED y el escaneo, que arrancó antes con datos
    más viejos, marcó MISSED nueve minutos después). Gana la decisión que
    ocurrió PRIMERO: es la que ya actuó (el paper pudo colocar una orden
    sobre ese TRIGGERED). Empate o fecha ilegible: canónico, como siempre."""
    ts_canon = _parse_ts(canon.actualizado_en)
    ts_overlay = _parse_ts(overlay.get("actualizado_en") if isinstance(overlay.get("actualizado_en"), str) else None)
    if ts_canon is None or ts_overlay is None:
        return False
    return ts_overlay < ts_canon


def _fusionar_overlay(canon: EntradaWatchlist, overlay: dict) -> EntradaWatchlist:
    """Reglas de conflicto (v1): catalizador/alta=canónico; canónico
    terminal que cambia de estado=canónico, salvo que el overlay también
    sea terminal y haya decidido ANTES (`_overlay_decidio_antes`);
    watching+overlay más nuevo=VPS; empate=canónico."""
    overlay_estado = overlay.get("estado")
    if canon.estado in ESTADOS_TERMINALES:
        if overlay_estado is not None and overlay_estado != canon.estado:
            if overlay_estado in ESTADOS_TERMINALES and _overlay_decidio_antes(canon, overlay):
                _aplicar_campos_overlay(canon, overlay, incluir_estado=True)
                _append_transiciones(canon, overlay)
            return canon
        if not _overlay_mas_nuevo(canon, overlay):
            return canon
        _aplicar_campos_overlay(canon, overlay, incluir_estado=False)
        _append_transiciones(canon, overlay)
        return canon
    if not _overlay_mas_nuevo(canon, overlay):
        return canon
    _aplicar_campos_overlay(canon, overlay, incluir_estado=True)
    _append_transiciones(canon, overlay)
    return canon


def aplicar_overlay(
    entradas: list[EntradaWatchlist],
    path: Path | None = None,
) -> list[EntradaWatchlist]:
    """Aplica el state file ticker a ticker. Ticker en overlay ausente
    del canónico se descarta: GHA es dueño del universo, no se resucitan
    fantasmas."""
    data = cargar_vps_state(path)
    entries = data.get("entries") or {}
    if not entries:
        return entradas
    resultado = []
    for e in entradas:
        overlay = entries.get(e.ticker)
        if overlay is None:
            overlay = entries.get(e.ticker.upper())
        if not isinstance(overlay, dict):
            resultado.append(e)
            continue
        resultado.append(_fusionar_overlay(e, overlay))
    return resultado


def activas(entradas: list[EntradaWatchlist]) -> list[EntradaWatchlist]:
    return [e for e in entradas if e.estado == ESTADO_WATCHING]


# Cuánto tiempo después del disparo se le siguen refrescando los niveles
# a una TRIGGERED. Una sesión regular dura 6,5 h (13:30-20:00 UTC); 8
# cubre la sesión entera con margen. Pasado eso, la ventana de esa señal
# ya se cerró: refrescar sus niveles solo gastaría pedidos de velas por
# un ticker que nadie va a operar (las TRIGGERED se conservan 7 días,
# ver `RETENCION_DIAS_TERMINALES`, y sin este tope se refrescarían todas,
# cada 5 minutos, durante una semana).
HORAS_REFRESCO_NIVELES = 8


def con_niveles_que_refrescar(
    entradas: list[EntradaWatchlist], ahora: datetime,
    horas: float = HORAS_REFRESCO_NIVELES,
) -> list[EntradaWatchlist]:
    """TRIGGERED recientes cuyos niveles conviene mantener al día -- NO
    para re-evaluarlas (TRIGGERED es terminal y no se reabre), solo para
    que `ultima_entrada/ultimo_stop/ultimo_objetivo` no se queden
    congelados en el instante del disparo.

    Bug real encontrado el 2026-08-24: `activas()` devuelve solo
    WATCHING, así que en cuanto una señal pasaba a TRIGGERED sus niveles
    dejaban de actualizarse para siempre. Cualquier consumidor de esos
    niveles (el comando `/trade`, o un sistema externo que lea este
    archivo) recibía a partir de ahí el precio congelado del instante
    del disparo, cada vez más viejo, sin forma de distinguirlo de uno
    fresco salvo por `ultimos_niveles_ts` -- y un consumidor prudente
    que compare ese timestamp contra un tope de frescura descartaba la
    señal para siempre después del primer intento fallido.

    Refrescarlos no reabre la decisión ni cambia el estado -- TRIGGERED
    sigue siendo terminal. Solo mantiene al día el precio que este
    módulo ya venía cacheando, que es exactamente para lo que se agregó
    ese caché (ver los campos `ultima_entrada`/`ultimo_stop`/
    `ultimo_objetivo`).

    `actualizado_en` es el momento de la transición a TRIGGERED, y
    `actualizar_niveles` no lo toca -- así la ventana no se auto-renueva
    con cada refresco."""
    limite = timedelta(hours=horas)
    recientes = []
    for e in entradas:
        if e.estado != ESTADO_TRIGGERED:
            continue
        try:
            disparada = datetime.fromisoformat(e.actualizado_en)
        except (TypeError, ValueError):
            # Sin fecha legible no se refresca: acá el lado seguro es no
            # gastar pedidos de velas (a diferencia de `purgar_antiguas`,
            # donde el lado seguro era no borrar). No debería pasar.
            continue
        if ahora - disparada <= limite:
            recientes.append(e)
    return recientes


def agregar_nuevas(
    entradas: list[EntradaWatchlist], candidatos, ahora: datetime,
    deteccion_ts: str | None = None, evaluacion_ts: str | None = None,
) -> list[EntradaWatchlist]:
    """Agrega a WATCHING los `CandidatoDiario` (o cualquier objeto con
    `.ticker`) que todavía no estén en la watchlist ACTIVA -- un ticker
    ya en WATCHING no se reinicia (conservaría `creado_en` incorrecto);
    uno que ya está en un estado terminal de HOY tampoco se re-agrega en
    la misma sesión (evita re-vigilar algo que ya se resolvió dos veces
    en la misma corrida/día). Un estado terminal de un día ANTERIOR sí
    puede volver a vigilarse -- el bloqueo es por sesión, no para
    siempre (bug real, 2026-08-11: antes de este fix, un ticker que
    terminaba EXPIRED/MISSED/INVALIDATED/TRIGGERED una sola vez quedaba
    bloqueado en `watchlist.json` para el resto de la vida del bot, ya
    que ese archivo se committea y nunca se limpiaba solo -- ver
    `purgar_antiguas`).

    EXPIRED es la ÚNICA excepción al bloqueo por sesión (corrección
    2026-08-11, revisión de PR, quinta vuelta): a diferencia de
    TRIGGERED/MISSED/INVALIDATED (una decisión real sobre la candidata),
    EXPIRED solo dice "este intento de vigilancia se venció" (TTL de
    `minutos_maximos_en_watching`, 120 min por defecto) -- el
    catalizador puede seguir vigente por días (`dias_ventana_
    catalizador`). Bloquearla el resto del día apagaba la vigilancia de
    5 minutos justo para las candidatas que el escaneo completo sigue
    re-descubriendo como válidas, dejándolas solo con el chequeo de 30
    minutos; re-descubrirla reinicia `creado_en` (nuevo intento, nuevo TTL)."""
    hoy = ahora.date()

    def _bloquea(e: EntradaWatchlist) -> bool:
        if e.estado == ESTADO_WATCHING:
            return True
        if e.estado == ESTADO_EXPIRED:
            return False
        try:
            return datetime.fromisoformat(e.actualizado_en).date() == hoy
        except ValueError:
            return True   # fecha ilegible -- más seguro no re-agregar de más

    ya_conocidos = {e.ticker for e in entradas if _bloquea(e)}
    nuevas = [
        desde_candidato_diario(c, ahora, deteccion_ts, evaluacion_ts) for c in candidatos
        if c.ticker not in ya_conocidos
    ]
    return entradas + nuevas


RETENCION_DIAS_TERMINALES = 7


def purgar_antiguas(
    entradas: list[EntradaWatchlist], ahora: datetime, dias: int = RETENCION_DIAS_TERMINALES,
) -> list[EntradaWatchlist]:
    """Descarta entradas en estado TERMINAL (nunca las WATCHING activas)
    con más de `dias` días desde su última transición -- sin esto,
    `watchlist.json` crece sin límite para siempre (se committea en cada
    corrida, nunca se sobreescribe desde cero). El historial de largo
    plazo para aprendizaje/auditoría sigue viviendo en
    `tracker.py`/`audit.py`; este archivo es solo el estado operativo
    reciente, no el registro permanente."""
    limite = timedelta(days=dias)
    resultado = []
    for e in entradas:
        if e.estado not in ESTADOS_TERMINALES:
            resultado.append(e)
            continue
        try:
            actualizado = datetime.fromisoformat(e.actualizado_en)
        except ValueError:
            resultado.append(e)   # fecha ilegible -- más seguro no purgar de más
            continue
        if ahora - actualizado <= limite:
            resultado.append(e)
    return resultado


def marcar_invalidated(
    e: EntradaWatchlist, motivo: str, ahora: datetime,
    deteccion_ts: str | None = None, evaluacion_ts: str | None = None,
) -> None:
    _transicionar(e, ESTADO_INVALIDATED, motivo, ahora, deteccion_ts, evaluacion_ts)


def marcar_missed(
    e: EntradaWatchlist, motivo: str, ahora: datetime,
    deteccion_ts: str | None = None, evaluacion_ts: str | None = None,
) -> None:
    _transicionar(e, ESTADO_MISSED, motivo, ahora, deteccion_ts, evaluacion_ts)


def marcar_triggered(
    e: EntradaWatchlist, market_event_ts: str, data_received_ts: str,
    evaluador_ts: str, ahora: datetime, velas_desde_ruptura: int | None = None,
) -> None:
    """Transición a TRIGGERED -- guarda los primeros tres timestamps de
    latencia (los dos que faltan, `mensaje_generado_ts`/
    `telegram_enviado_ts`, los llena `registrar_latencia` después de que
    el mensaje de verdad se arma y se manda, ver `run.py`)."""
    _transicionar(e, ESTADO_TRIGGERED, "Todas las condiciones se cumplieron.", ahora)
    e.market_event_ts = market_event_ts
    e.data_received_ts = data_received_ts
    e.evaluador_ts = evaluador_ts
    e.velas_desde_ruptura = velas_desde_ruptura


def marcar_archivada(
    e: EntradaWatchlist, motivo: str, ahora: datetime,
) -> bool:
    """TRIGGERED → ARCHIVED. Solo esa dirección, y solo si todavía está
    en TRIGGERED: archivar WATCHING/MISSED/EXPIRED/INVALIDATED escondería
    un síntoma distinto al que se diagnosticó (entradas paper ya
    revisadas que nunca salían de TRIGGERED).

    No inventa niveles ni toca umbrales. Devuelve True si transicionó.
    El registro durable (ticker, estados, timestamps, revisión paper,
    causa raíz) lo escribe el paper trader en JSONL -- esta función
    solo agrega la transición, que `purgar_antiguas` sí puede llevarse
    a los 7 días."""
    if e.estado != ESTADO_TRIGGERED:
        return False
    _transicionar(e, ESTADO_ARCHIVED, motivo, ahora)
    return True


def actualizar_niveles(
    e: EntradaWatchlist, entrada: float | None, stop: float | None, objetivo: float | None,
    zona_entrada_baja: float | None, ahora: datetime,
) -> None:
    """Cachea los niveles que el pipeline YA calculó en este chequeo --
    ver docstring de los campos en `EntradaWatchlist`. Ningún cálculo
    nuevo se hace acá, solo persistencia de lo que `report.py` ya
    resolvió con datos frescos."""
    e.ultima_entrada = entrada
    e.ultimo_stop = stop
    e.ultimo_objetivo = objetivo
    e.ultima_zona_entrada_baja = zona_entrada_baja
    e.ultimos_niveles_ts = _ahora_iso(ahora)
    # El stop de la TESIS se congela la primera vez y no se vuelve a
    # tocar -- mismo principio que `gap_pct_congelado`/`atr_diario`/el
    # catalizador: son el retrato del momento en que nació la idea.
    # `ultimo_stop` se recalcula en cada chequeo con datos frescos, así
    # que persigue al precio hacia abajo; compararlo con el precio actual
    # para detectar una tesis rota nunca dispararía (encontrado por una
    # prueba al implementar esa detección, 2026-08-21). El congelado es
    # el único que responde "¿el precio perdió el nivel que hacía válida
    # esta idea CUANDO la tuvimos?".
    if e.stop_tesis is None and stop is not None:
        e.stop_tesis = stop


def registrar_latencia(e: EntradaWatchlist, mensaje_generado_ts: str, telegram_enviado_ts: str) -> None:
    """`signal_latency_ms` = del timestamp de la VELA que confirmó la
    entrada (dato de mercado, no reloj de la máquina) al momento en que
    Telegram confirmó el envío -- la medida honesta de "cuánto tardó en
    llegarte la señal desde que el mercado hizo el movimiento", no solo
    cuánto tardó el cómputo."""
    e.mensaje_generado_ts = mensaje_generado_ts
    e.telegram_enviado_ts = telegram_enviado_ts
    if e.market_event_ts is None:
        return
    try:
        inicio = datetime.fromisoformat(e.market_event_ts)
        fin = datetime.fromisoformat(telegram_enviado_ts)
    except ValueError:
        return
    e.signal_latency_ms = round((fin - inicio).total_seconds() * 1000.0, 1)


def expirar_vencidas(
    entradas: list[EntradaWatchlist], minutos_maximos: int, ahora: datetime,
) -> list[EntradaWatchlist]:
    """TTL de WATCHING -- una candidata que lleva más de
    `minutos_maximos` sin confirmar nada expira sola, muta `entradas`
    in-place (mismo patrón que las demás transiciones).

    Devuelve las entradas recién expiradas EN ESTA LLAMADA (2026-08-11,
    integración de Telegram: antes esta función no devolvía nada -- ahora
    el caller necesita saber exactamente cuáles para mandar el mensaje
    EXPIRED una sola vez, sin volver a evaluar el resto de `entradas`).
    EXPIRED es un TTL de reloj, no un dato de mercado puntual -- por eso
    su transición no lleva `deteccion_ts`/`evaluacion_ts` (quedan `None`,
    ver docstring de `Transicion`)."""
    limite = timedelta(minutes=minutos_maximos)
    expiradas: list[EntradaWatchlist] = []
    for e in entradas:
        if e.estado != ESTADO_WATCHING:
            continue
        try:
            creado = datetime.fromisoformat(e.creado_en)
        except ValueError:
            continue
        if ahora - creado > limite:
            _transicionar(
                e, ESTADO_EXPIRED,
                f"Llevaba más de {minutos_maximos} minutos en observación sin confirmar nada.",
                ahora,
            )
            expiradas.append(e)
    return expiradas
