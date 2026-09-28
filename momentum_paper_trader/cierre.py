"""Cierre diario -- no dejar ninguna posición abierta de un día para otro.

EL HUECO QUE ESTO TAPA (encontrado el 2026-08-21). Las órdenes bracket se
mandan con `time_in_force: "day"`, así que sus dos patas de salida
(take-profit y stop-loss) se cancelan solas al cerrar el mercado. Si la
compra se llenó a las 10 de la mañana y para el cierre no tocó ni el stop
ni el objetivo, la posición queda abierta durante la noche SIN STOP Y SIN
OBJETIVO -- desprotegida contra cualquier hueco de apertura del día
siguiente. `seguimiento.py` sabe detectar ese estado y avisarlo, pero
avisar no es arreglarlo.

ESTADO ACTUAL (2026-08-25): AGUANTAR ESTÁ DESACTIVADO. Se liquida todo
antes del cierre, sin excepción. La lógica de decisión con IA que
describe el párrafo siguiente sigue entera y probada, detrás del flag
`config.permitir_aguantar_overnight` -- no se borró, se apagó.

El motivo del cambio: sin historial de operaciones cerradas no hay forma
de juzgar si el criterio de la IA para aguantar es criterio o es
esperanza ("el catalizador sigue vivo" suena igual en los dos casos), y
el stop protector no acota el costo de equivocarse porque no cubre un
hueco de apertura. Se reactiva cuando haya ~50 operaciones con las que
medirlo.

LA DECISIÓN LA TOMA LA IA, posición por posición (usuario, 2026-08-21:
"el objetivo de crear la IA que tome las decisiones de inversión es para
eso"). La primera versión de este módulo liquidaba todo con una regla
fija; el usuario señaló, con razón, que una regla mecánica no distingue
"esto se rompió" de "esto va lento pero sigue vivo", que es justo lo que
la capa de IA existe para juzgar.

CONDICIÓN INNEGOCIABLE PARA AGUANTAR: si la IA decide mantener una
posición, se le coloca un STOP NUEVO que sobrevive a la noche
(`time_in_force: "gtc"`, ver `alpaca_client.colocar_stop_protector`).
Aguantar sin protección sería peor que cualquiera de las dos opciones, y
es exactamente el estado que este módulo nació para eliminar. Si el stop
protector no se puede colocar, se cierra -- no hay tercera vía.

AVISO HONESTO: un stop no protege contra un hueco de apertura. Si cierra
en $50 con stop en $48 y abre en $40, la venta sale cerca de $40. Reduce
el riesgo nocturno, no lo elimina. Por eso el prompt de la IA se lo dice
explícitamente y el default ante cualquier duda es cerrar.

CUÁNDO. `cfg.minutos_antes_del_cierre` antes del cierre (16:00 ET), o
sea 15:50 ET por defecto. El re-chequeo de watchlist corre cada pocos
minutos, así que suele caer al menos una corrida dentro de esa ventana.

La hora se calcula con la zona horaria real (`momentum_hunter.sesion`),
así que el cambio de horario se aplica solo. Lo que sigue sin saberse
son los feriados y las medias sesiones: en una media sesión (13:00 ET)
esta ventana no se abriría y las posiciones quedarían sin liquidar. El
ejecutor sí consulta el calendario real de Alpaca antes de ABRIR, pero
el cierre todavía no. Anotado, no resuelto.

IDEMPOTENTE por construcción: la segunda corrida dentro de la ventana ya
no encuentra posiciones y no hace nada. No hace falta estado persistido.

CIERRE QUE NO SE CONCRETA (2026-09-25). `DELETE /v2/positions/{symbol}`
sin cancelar antes las patas del bracket responde 403: esas patas
reservan la cantidad (el mismo motivo por el que la rama de aguantar
sí llama a `cancelar_ordenes_de`, y por el que el DELETE de TODAS las
posiciones manda `cancel_orders=true`). El cierre de una sola posición
no hacía ninguna de las dos cosas. El 25/9, CTAS, TWST, NBIS y DLB
recibieron ~40 rechazos entre 19:50 y 20:00 UTC, solo como WARNING.
No quedó `cierre_order_id`. Las patas day expiraron al cierre y
`seguimiento.py` las marcó `cerrada` sin fill. Ahora se cancela
primero, se reintenta el DELETE y, si Alpaca lo rechaza, se vende a
mercado la cantidad completa. Si ni eso queda aceptado, sale un
Telegram ERROR. Aceptar la orden no marca el trade `cerrada`: eso lo
confirma `seguimiento.py` contra `GET /v2/positions`."""

from __future__ import annotations

import logging
from datetime import datetime

from momentum_hunter import sesion

from momentum_paper_trader import dedupe_avisos, estado, ia_decision, notify
from momentum_paper_trader.alpaca_client import AlpacaPaperClient
from momentum_paper_trader.config import PaperTraderConfig

log = logging.getLogger("momentum_paper_trader.cierre")


def en_ventana_de_cierre(ahora: datetime, cfg: PaperTraderConfig) -> bool:
    """¿Estamos en los últimos minutos de la sesión regular?

    La ventana va desde `minutos_antes_del_cierre` antes del cierre hasta
    el cierre mismo. Pasado el cierre ya no se intenta: el mercado está
    cerrado y una orden a mercado no se ejecutaría hasta el día
    siguiente -- justo lo contrario de lo que se busca.

    2026-08-27: pasa a calcularse con la zona horaria real
    (`momentum_hunter.sesion`) en vez de la constante de verano que este
    módulo usaba antes. Esa constante hacía que en horario de invierno
    la ventana cayera una hora antes de tiempo -- se liquidaba a las
    14:50 ET, con más de una hora de sesión por delante. Era una
    limitación anotada y no resuelta en el docstring de arriba; ya está
    resuelta."""
    faltan = sesion.minutos_hasta_el_cierre(ahora)
    return 0 < faltan <= cfg.minutos_antes_del_cierre


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _contexto_posicion(p: dict, clima: str | None = None) -> str:
    """Lo que la IA necesita para decidir sobre ESTA posición -- todo del
    payload que Alpaca ya devuelve, ningún dato nuevo que pedir."""
    pl = _num(p.get("unrealized_pl"))
    plpc = _num(p.get("unrealized_plpc"))
    entrada = _num(p.get("avg_entry_price"))
    actual = _num(p.get("current_price"))
    lineas = [
        f"Ticker: {p.get('symbol', '?')}",
        f"Cantidad: {int(_num(p.get('qty')) or 0)} acciones",
        f"Precio de entrada: ${entrada:,.2f}" if entrada else "Precio de entrada: desconocido",
        f"Precio actual: ${actual:,.2f}" if actual else "Precio actual: desconocido",
    ]
    if pl is not None:
        pct = f" ({plpc * 100:+.2f}%)" if plpc is not None else ""
        lineas.append(f"Resultado abierto: {'+' if pl >= 0 else '-'}${abs(pl):,.2f}{pct}")
    if clima:
        lineas.append(f"Clima del mercado general hoy: {clima}")
    lineas.append("Faltan minutos para el cierre del mercado.")
    return "\n".join(lineas)


def _stop_protector(p: dict, cfg: PaperTraderConfig) -> float | None:
    """Dónde poner el stop que sobrevive la noche.

    No se inventa un nivel: se usa el precio actual menos el mismo
    porcentaje de colchón que `cfg.colchon_stop_nocturno`. Es
    deliberadamente simple y explícito -- el stop original del bracket ya
    no existe a esta hora (murió con la sesión), y reconstruirlo desde el
    ATR exigiría volver a pedir datos de mercado que este módulo no
    tiene."""
    actual = _num(p.get("current_price")) or _num(p.get("avg_entry_price"))
    if actual is None or actual <= 0:
        return None
    return round(actual * (1 - cfg.colchon_stop_nocturno), 2)


def _mensaje(cerradas: list[tuple[dict, str]], aguantadas: list[tuple[dict, str, float]]) -> str:
    """Solo las posiciones CERRADAS -- aguantar overnight no es un trade
    completado y no se avisa (anti-spam). `aguantadas` se recibe para no
    romper callers/tests; no entra al texto."""
    del aguantadas
    return notify.formatear_cierre_dia(cerradas)


def _anotar_orden_de_cierre_en_revisiones(ids_cierre: dict[str, str]) -> None:
    """Anota en la revisión de cada trade la orden de liquidación de fin de
    día, para que `seguimiento.py` confirme su llenado real.

    POR QUÉ. La liquidación de fin de día vende con una orden aparte y las
    dos patas del bracket quedan canceladas. `seguimiento.py` ve entonces
    la entrada llena y las patas muertas sin ninguna de salida llenada, y
    concluye "posición sin salidas": un ERROR por Telegram y un trade sin
    P&L. Pero acá el cierre fue A PROPÓSITO, no un descuido. Anotando la
    orden de liquidación, `seguimiento.py` confirma el llenado real antes
    de dar el trade por "cerrada": una liquidación ACEPTADA no es una
    liquidación LLENADA. Si esa orden muere sin llenarse (símbolo halted,
    sin liquidez al cierre, rechazo), la posición sigue abierta y
    desprotegida, y el ERROR de "posición sin salidas" tiene que salir
    igual -- por eso acá NO se marca terminal ni se inventa un P&L: solo
    se anota qué orden hay que vigilar.

    Empareja con la revisión viva del ticker: la de `creado_en` más
    reciente entre las no terminales con orden real (una revisión rancia
    sin resolver no debe robarse el cierre de hoy). Mejor esfuerzo: nunca
    tumba el cierre."""
    if not ids_cierre:
        return
    try:
        revisiones = estado.cargar()
    except Exception as ex:
        log.warning("cierre diario: no se pudieron cargar revisiones para registrar el cierre (%s)", type(ex).__name__)
        return
    viva_por_ticker: dict[str, estado.RevisionIA] = {}
    for r in revisiones:
        if not (r.entro and r.order_id and r.resultado not in estado.RESULTADOS_TERMINALES):
            continue
        prev = viva_por_ticker.get(r.ticker)
        if prev is None or (r.creado_en or "") > (prev.creado_en or ""):
            viva_por_ticker[r.ticker] = r
    cambio = False
    for ticker, order_id in ids_cierre.items():
        r = viva_por_ticker.get(ticker)
        if r is None:
            continue
        r.cierre_order_id = order_id
        cambio = True
    if cambio:
        try:
            estado.guardar(revisiones)
        except Exception as ex:
            log.warning("cierre diario: no se pudo guardar el cierre en revisiones (%s)", type(ex).__name__)


# Dos DELETE: el primero justo después de cancelar, el segundo por si
# alguna pata seguía viva y el 403 era exactamente eso. Más intentos no
# cambian el diagnóstico; el respaldo es la venta a mercado.
_INTENTOS_DELETE = 2

_DETALLE_FALLO = {
    "rechazo": (
        "Alpaca rechazó el cierre. Se cancelaron las órdenes abiertas del símbolo, "
        "se reintentó el DELETE y la venta a mercado de la cantidad completa tampoco "
        "quedó aceptada. La posición puede seguir abierta y sin stop."
    ),
    "sin_confirmar": (
        "No se pudo confirmar la liquidación. No se mandó una venta a mercado de "
        "respaldo porque el intento no fue un rechazo y la orden pudo haber llegado. "
        "Revisar en Alpaca; no se da el trade por cerrado."
    ),
    "sin_qty": (
        "Alpaca rechazó el cierre y la posición no trae una cantidad utilizable. "
        "No se inventó una venta. La posición puede seguir abierta."
    ),
}


def _status_http(ex: BaseException) -> int | None:
    resp = getattr(ex, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def _qty_para_vender(p: dict) -> str | None:
    """El `qty` del broker, como string, o None si no se puede usar.

    No convierte un ausente en 0: vender cero no aplana nada, y vender
    una cantidad inventada puede dejar un resto o abrir un corto."""
    q = p.get("qty")
    if isinstance(q, bool) or q is None:
        return None
    if isinstance(q, str):
        texto = q.strip()
        if not texto:
            return None
        try:
            n = float(texto)
        except ValueError:
            return None
        if not n > 0:
            return None
        return texto
    try:
        n = float(q)
    except (TypeError, ValueError):
        return None
    if not n > 0:
        return None
    if n == int(n):
        return str(int(n))
    return str(n)


def _cancelar(client: AlpacaPaperClient, ticker: str, ordenes: list) -> None:
    try:
        client.cancelar_ordenes_de(ticker, ordenes)
    except Exception as ex:
        log.warning(
            "%s: no se pudieron cancelar las órdenes antes del cierre (%s)",
            ticker, type(ex).__name__,
        )


def _liquidar(
    client: AlpacaPaperClient, ticker: str, qty: str | None, abiertas: list,
) -> tuple[dict | None, str | None]:
    """(orden con id, código de fallo). El código es None si hay id.

    Cancelar primero es obligatorio: con las patas del bracket vivas el
    DELETE ve cantidad disponible 0 y Alpaca responde 403. La pata de
    stop suele estar `held` y solo aparece en `legs`; `cancelar_ordenes_de`
    la incluye. El DELETE igual lleva `cancel_orders=true`: es Alpaca
    quien cancela, antes de liquidar, las órdenes que retienen la
    cantidad -- también la `held` que este listado no haya visto. Si el
    rechazo se repite, la venta a mercado usa la qty del broker. Un
    error que no es 4xx no dispara esa venta -- el DELETE pudo haber
    llegado igual y una segunda orden abriría un corto."""
    _cancelar(client, ticker, abiertas)
    ultimo_rechazo = False
    for intento in range(1, _INTENTOS_DELETE + 1):
        try:
            resp = client.cerrar_posicion(ticker, cancel_orders=True)
        except Exception as ex:
            status = _status_http(ex)
            # Solo el código HTTP y el tipo: el str de la excepción
            # trae la URL, y este log termina en journald.
            log.warning(
                "%s: DELETE de la posición no aceptado (intento %d, %s)",
                ticker, intento,
                f"HTTP {status}" if status is not None else type(ex).__name__,
            )
            ultimo_rechazo = status is not None and 400 <= status < 500
            if intento < _INTENTOS_DELETE:
                try:
                    frescas = client.ordenes_abiertas()
                except Exception as ex_leer:
                    log.warning(
                        "%s: no se pudieron releer las órdenes abiertas (%s)",
                        ticker, type(ex_leer).__name__,
                    )
                    frescas = []
                if not isinstance(frescas, list):
                    frescas = []
                _cancelar(client, ticker, frescas)
            continue
        if isinstance(resp, dict) and resp.get("id"):
            return resp, None
        log.warning("%s: el DELETE respondió sin id de orden; no se manda otra venta", ticker)
        return None, "sin_confirmar"
    if not ultimo_rechazo:
        return None, "sin_confirmar"
    if qty is None:
        log.warning("%s: DELETE rechazado y no hay qty utilizable; no se inventa una venta", ticker)
        return None, "sin_qty"
    try:
        resp = client.vender_a_mercado(ticker, qty)
    except Exception as ex:
        log.warning("%s: la venta a mercado de respaldo falló (%s)", ticker, type(ex).__name__)
        return None, "rechazo"
    if isinstance(resp, dict) and resp.get("id"):
        return resp, None
    log.warning("%s: la venta a mercado de respaldo no trajo id", ticker)
    return None, "rechazo"


def _avisar_cierre_fallido(ticker: str, codigo: str, ahora: datetime) -> None:
    """Telegram ERROR, una vez por símbolo y por día de sesión. El
    WARNING del journal no bastó el 2026-09-25: nadie vio los 403."""
    detalle = _DETALLE_FALLO.get(codigo, _DETALLE_FALLO["sin_confirmar"])
    texto = notify.formatear_error(
        tipo="cierre de fin de día rechazado",
        ticker=ticker,
        detalle=detalle,
    )
    marca = dedupe_avisos.clave("cierre_fallido", ticker, ahora)
    if dedupe_avisos.ya_avisada(marca):
        log.info("%s: el cierre fallido ya se avisó en esta sesión", ticker)
        return
    notify.enviar(texto)
    dedupe_avisos.marcar(marca, ahora)


def cerrar_si_toca(
    client: AlpacaPaperClient, cfg: PaperTraderConfig, ahora: datetime,
    clima: str | None = None,
) -> list[dict]:
    """Decide posición por posición si cerrarla o aguantarla, con la IA.
    Devuelve las que se CERRARON (vacío si no tocaba, no había, o se
    aguantaron todas).

    Nunca lanza: un fallo acá no debe tumbar la corrida. Cualquier
    posición cuya decisión o cuya protección falle se CIERRA -- ver el
    docstring del módulo sobre por qué el default va en esa dirección."""
    if not cfg.cerrar_antes_del_cierre or not en_ventana_de_cierre(ahora, cfg):
        return []

    try:
        posiciones = client.posiciones()
    except Exception as ex:
        log.warning("no se pudieron leer las posiciones para el cierre diario: %s", ex)
        return []
    if not posiciones:
        log.info("cierre diario: no hay posiciones abiertas")
        return []

    try:
        abiertas = client.ordenes_abiertas()
    except Exception as ex:
        log.warning("no se pudieron leer las órdenes abiertas: %s", ex)
        abiertas = []

    cerradas: list[tuple[dict, str]] = []
    aguantadas: list[tuple[dict, str, float]] = []
    ids_cierre: dict[str, str] = {}   # ticker -> id de la orden de liquidación

    for p in posiciones:
        ticker = p.get("symbol", "?")
        if not cfg.permitir_aguantar_overnight:
            # Aguantar está desactivado (ver `config.permitir_aguantar_
            # overnight`): no se le pregunta a la IA algo cuya respuesta
            # no se puede acatar. Se ahorra la llamada y se liquida.
            decision = ia_decision.DecisionCierre(
                cerrar=True, confianza=10,
                razonamiento=("Cierre obligatorio de fin de día: aguantar hasta mañana está "
                              "desactivado hasta tener historial suficiente para evaluarlo."))
        else:
            decision = ia_decision.decidir_cierre(_contexto_posicion(p, clima))

        if not decision.cerrar:
            stop = _stop_protector(p, cfg)
            cantidad = int(_num(p.get("qty")) or 0)
            if stop is not None and cantidad > 0:
                try:
                    # Las patas del bracket siguen vivas: hay que
                    # cancelarlas antes o el stop nuevo rebota por
                    # cantidad insuficiente.
                    client.cancelar_ordenes_de(ticker, abiertas)
                    client.colocar_stop_protector(ticker, cantidad, stop)
                    aguantadas.append((p, decision.razonamiento, stop))
                    log.info("%s: se aguanta hasta mañana con stop en $%.2f", ticker, stop)
                    continue
                except Exception as ex:
                    log.warning(
                        "%s: la IA quería aguantar pero falló el stop protector (%s) -- se cierra",
                        ticker, ex)
            else:
                log.warning("%s: no se pudo calcular un stop protector -- se cierra", ticker)

        orden, fallo = _liquidar(client, str(ticker), _qty_para_vender(p), abiertas)
        if not orden:
            _avisar_cierre_fallido(str(ticker), fallo or "sin_confirmar", ahora)
            continue
        cerradas.append((p, decision.razonamiento))
        # El id -- del DELETE o de la venta a mercado de respaldo -- es
        # lo que `seguimiento.py` vigila. Aceptar no es llenar, y llenar
        # no es "la posición ya no está": eso se confirma contra el broker.
        ids_cierre[str(ticker)] = str(orden["id"])
        log.info("%s: liquidación enviada al final del día", ticker)

    # Anotar la orden de liquidación en las revisiones ANTES de avisar:
    # aunque el Telegram falle, `seguimiento.py` ya sabrá qué orden vigilar
    # para confirmar el fill (y volver a alertar si no se llena).
    _anotar_orden_de_cierre_en_revisiones(ids_cierre)

    if cerradas:
        texto = _mensaje(cerradas, aguantadas)
        if texto:
            notify.enviar(texto)
    return [p for p, _ in cerradas]
