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

Cinco estados, tal como se pidieron:

  WATCHING    -- catalizador confirmado, en observación. Nunca genera
                 un mensaje de Telegram por sí solo (eso sería
                 exactamente el ruido que se pidió evitar).
  TRIGGERED   -- se volvió `accionable` (las 5 preguntas de
                 `evaluator.py` + el veto de riesgo/recompensa) --
                 termina en un mensaje de entrada inmediato.
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

TRIGGERED/INVALIDATED/MISSED/EXPIRED son terminales: una vez ahí, el
ticker sale de la vigilancia activa (pero su historial de transiciones
NUNCA se borra -- queda en el archivo para poder reconstruir la sesión
completa después, incluyendo la latencia de la señal, ver
`marcar_triggered`).

Snapshot congelado desde el descubrimiento (`desde_candidato_diario`):
catalizador y metadata NO se vuelven a pedir en cada re-chequeo (esos
datos no cambian minuto a minuto, y volver a pedir noticias para 20-50
tickers cada 5 minutos sería gastar llamadas de red sin necesidad) --
solo las velas intradía se piden frescas en cada ciclo. `run.py` es
quien orquesta cuándo se descubre (corrida completa, cada ~30 min) y
cuándo se re-chequea (corrida liviana, cada ~5 min -- el mínimo real que
garantiza GitHub Actions, ver README)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from momentum_hunter.catalysts.detector import Catalizador, dentro_de_ventana
from momentum_hunter.models import Metadata

log = logging.getLogger("momentum_hunter.watchlist")

PATH = Path(__file__).resolve().parent / "watchlist.json"

ESTADO_WATCHING = "watching"
ESTADO_TRIGGERED = "triggered"
ESTADO_INVALIDATED = "invalidated"
ESTADO_MISSED = "missed"
ESTADO_EXPIRED = "expired"
ESTADOS_TERMINALES = frozenset({ESTADO_TRIGGERED, ESTADO_INVALIDATED, ESTADO_MISSED, ESTADO_EXPIRED})


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


def cargar(path: Path = PATH) -> list[EntradaWatchlist]:
    """Un archivo corrupto no debe tumbar la corrida -- se ignora y se
    reinicia vacía (mismo principio que `heartbeat.cargar_estado`)."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("watchlist corrupta (%s); se reinicia vacía", e)
        return []
    return parsear(data)


def guardar(entradas: list[EntradaWatchlist], path: Path = PATH) -> None:
    data = {"entradas": [asdict(e) for e in entradas]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


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
    evaluador_ts: str, ahora: datetime,
) -> None:
    """Transición a TRIGGERED -- guarda los primeros tres timestamps de
    latencia (los dos que faltan, `mensaje_generado_ts`/
    `telegram_enviado_ts`, los llena `registrar_latencia` después de que
    el mensaje de verdad se arma y se manda, ver `run.py`)."""
    _transicionar(e, ESTADO_TRIGGERED, "Todas las condiciones se cumplieron.", ahora)
    e.market_event_ts = market_event_ts
    e.data_received_ts = data_received_ts
    e.evaluador_ts = evaluador_ts


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
