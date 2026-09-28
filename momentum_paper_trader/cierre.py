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

CIERRE QUE NO SE CONCRETA (2026-09-25, carrera el 2026-09-28).
`DELETE /v2/positions/{symbol}` con las patas del bracket vivas responde
403: esas patas reservan la cantidad. El 25/9, CTAS, TWST, NBIS y DLB
recibieron ~40 rechazos entre 19:50 y 20:00 UTC, solo como WARNING.
Un 204 al cancelarlas solo acepta el pedido: pueden seguir
`pending_cancel` y dejar `qty_available` en 0 (MNST, 28/9, qty 17).
El DELETE espera a que cada pata de venta esté en un estado terminal
(~5 s). Si una se llena, no se vende: lo confirma el seguimiento. Si
no llegan a tiempo, no se vende y se avisa; el vigía reintenta.
`cancel_orders` está documentado en el DELETE de TODAS las posiciones,
no en el de un símbolo, y no se usa como prueba de que las patas
murieron. Un 404 de ese DELETE es "no hay posición", no un rechazo:
no autoriza la venta de respaldo. Esa venta, si el DELETE sí fue un
4xx distinto, relee `GET /v2/positions/{symbol}` y manda
min(qty, qty_available) solo si es long y > 0. La qty del listado
inicial no se reutiliza. Un timeout no se reintenta si ya hay una
venta viva. La orden de respaldo lleva `client_order_id`
`eod-{ticker}-{YYYYMMDD}`; si Alpaca dice que está repetido, esa
orden ya se había enviado. Aceptar no marca el trade `cerrada`: eso
lo confirma `seguimiento.py` contra el broker."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from momentum_hunter import sesion

from momentum_paper_trader import dedupe_avisos, estado, ia_decision, notify
from momentum_paper_trader.alpaca_client import (
    AlpacaPaperClient,
    orden_sigue_viva,
    orden_ya_terminada,
    ordenes_con_patas,
)
from momentum_paper_trader.config import PaperTraderConfig
from momentum_paper_trader.reconciliacion import cobertura, ventas_vivas

log = logging.getLogger("momentum_paper_trader.cierre")

_NY = ZoneInfo("America/New_York")

# Un 204 de DELETE /v2/orders/{id} solo acepta la cancelación. La pata
# puede quedar `pending_cancel` y, mientras tanto, la posición sigue con
# qty_available 0. 11 lecturas y 10 pausas de 0,5 s son ~5 s de espera.
_ESPERA_PATA_SEG = 0.5
_ESPERA_PATA_PASOS = 11

# Estos desenlaces no son un cierre fallido: no hay nada que vender, o
# la salida ya la hizo una pata. Avisar sería un ERROR en falso.
_SIN_AVISO = frozenset({"pata_llena", "sin_posicion"})


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


# Dos DELETE: el primero cuando las patas ya están terminales, el segundo
# por si el rechazo era una pata que todavía no habíamos visto. Más
# intentos no cambian el diagnóstico; el respaldo es la venta a mercado,
# y solo con una cantidad releída.
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
    "patas_pendientes": (
        "Se pidió cancelar las patas de salida y no llegaron a un estado terminal "
        "a tiempo. No se mandó el DELETE ni una venta a mercado: aceptar la "
        "cancelación no es lo mismo que haberla soltado. El próximo ciclo del vigía reintenta."
    ),
    "lectura": (
        "No se pudo releer la posición en Alpaca antes de vender. No se vendió: "
        "un fallo de lectura no es una posición vacía ni una cantidad en cero."
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


def _numero(v) -> float | None:
    """None si el campo no es un número finito. Nunca sustituye un ausente por 0."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        texto = v.strip()
        if not texto:
            return None
        try:
            n = float(texto)
        except ValueError:
            return None
    else:
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


def _texto_qty(n: float) -> str | None:
    if not n > 0:
        return None
    if n == int(n):
        return str(int(n))
    texto = f"{n:.9f}".rstrip("0").rstrip(".")
    return texto or None


def _id_orden_eod(ticker: str, ahora: datetime) -> str:
    """El mismo id en cada reintento de esta sesión, para que Alpaca
    rechace el duplicado en vez de vender dos veces."""
    limpio = "".join(c if c.isalnum() else "-" for c in ticker)
    if ahora.tzinfo is None:
        fecha = ahora.date()
    else:
        fecha = ahora.astimezone(_NY).date()
    return f"eod-{limpio}-{fecha:%Y%m%d}"[:48]


def _cancelar(client: AlpacaPaperClient, ticker: str, ordenes: list) -> None:
    try:
        client.cancelar_ordenes_de(ticker, ordenes)
    except Exception as ex:
        log.warning(
            "%s: no se pudieron cancelar las órdenes antes del cierre (%s)",
            ticker, type(ex).__name__,
        )


def _patas_de_salida(ticker: str, ordenes: list) -> list[str]:
    """Ids de ventas que todavía no están terminales.

    El padre `filled` no entra: que la compra se haya llenado no es
    motivo para no cerrar. Una pata de venta sí puede llenarse mientras
    la cancelamos, y esa es la que no hay que duplicar."""
    ids: list[str] = []
    vistos: set[str] = set()
    if not isinstance(ordenes, list):
        return ids
    for o in ordenes_con_patas(ordenes):
        if o.get("_symbol") != ticker:
            continue
        if str(o.get("side") or "").lower() != "sell":
            continue
        if orden_ya_terminada(o):
            continue
        oid = o.get("id")
        if not oid:
            continue
        texto = str(oid)
        if texto in vistos:
            continue
        vistos.add(texto)
        ids.append(texto)
    return ids


def _esperar_patas(client: AlpacaPaperClient, ticker: str, ids: list[str]) -> str:
    """'listas', 'filled', 'pendiente' o 'ilegible'.

    'listas' = cada pata está en un terminal que no es filled (canceled,
    expired, rejected, ...): ya no retienen cantidad. 'filled' = alguna
    se ejecutó; vender encima puede abrir un corto o vender de más.
    'pendiente' = se acabó la espera y alguna sigue viva. 'ilegible' =
    no se pudo leer un status; un hueco no es un terminal."""
    if not ids:
        return "listas"
    for paso in range(_ESPERA_PATA_PASOS):
        llena = False
        listas = True
        for oid in ids:
            try:
                datos = client.estado_orden(oid)
            except Exception as ex:
                log.warning(
                    "%s: no se pudo leer la pata %s (%s)",
                    ticker, oid, type(ex).__name__,
                )
                return "ilegible"
            if not isinstance(datos, dict):
                log.warning("%s: la pata %s no vino como objeto; no se vende", ticker, oid)
                return "ilegible"
            status = datos.get("status")
            if not isinstance(status, str) or not status.strip():
                listas = False
                continue
            s = status.strip().lower()
            if s == "filled":
                llena = True
            elif not orden_ya_terminada({"status": s}):
                listas = False
        if llena:
            log.info("%s: una pata se llenó mientras se cancelaba; no se vende", ticker)
            return "filled"
        if listas:
            return "listas"
        if paso + 1 < _ESPERA_PATA_PASOS:
            time.sleep(_ESPERA_PATA_SEG)
    log.warning("%s: una pata no llegó a estado terminal a tiempo; no se vende", ticker)
    return "pendiente"


def _leer_ordenes(client: AlpacaPaperClient, ticker: str) -> list | None:
    """None si la lectura falló. Una lista vacía es "no hay órdenes",
    que no es lo mismo: fallar y seguir podría vender dos veces."""
    try:
        frescas = client.ordenes_de_simbolos([ticker])
    except Exception as ex:
        log.warning(
            "%s: no se pudieron releer las órdenes del símbolo (%s)",
            ticker, type(ex).__name__,
        )
        return None
    if not isinstance(frescas, list):
        return None
    return frescas


def _venta_que_bloquea_reintento(
    ticker: str, ordenes: list,
) -> tuple[str, dict | None]:
    """Sobre un listado ya leído, antes de repetir un DELETE.

    La venta viva es la de `reconciliacion.ventas_vivas` /
    `cobertura`: la misma lista blanca (`orden_sigue_viva`: held, new,
    accepted, pending_new). No se copia esa lista acá.

    'libre': no queda una venta de este símbolo sin terminar.
    'ya_enviada': hay una venta a mercado (o con nuestro id eod) en esa
    lista blanca. Esa es la liquidación; no se manda otra.
    'ocupada': hay otra venta viva (el stop, por ejemplo).
    'ilegible': una venta no está ni viva ni terminal (`pending_cancel`,
    status ausente o desconocido). Duda: no se reintenta y no se vende.
    """
    for o in ordenes_con_patas(ordenes):
        if not isinstance(o, dict) or o.get("_symbol") != ticker:
            continue
        if str(o.get("side") or "").lower() != "sell":
            continue
        if orden_ya_terminada(o) or orden_sigue_viva(o):
            continue
        log.warning(
            "%s: una venta no está ni viva ni terminal; no se reintenta el cierre",
            ticker,
        )
        return "ilegible", None
    vivas = ventas_vivas(ordenes)
    cubre = cobertura(ordenes)
    if vivas is None or cubre is None:
        return "ilegible", None
    de_este = [o for o in vivas if o.get("_symbol") == ticker]
    for o in de_este:
        tipo = str(o.get("type") or "").lower()
        coid = o.get("client_order_id")
        es_eod = isinstance(coid, str) and coid.startswith("eod-")
        if (tipo == "market" or es_eod) and o.get("id"):
            return "ya_enviada", o
    _stops, mercados = cubre
    if ticker in mercados or de_este:
        return "ocupada", None
    return "libre", None


def _leer_qty_vendible(client: AlpacaPaperClient, ticker: str) -> tuple[str, str | None]:
    """('ausente'|'fallo'|'no'|'vender', qty).

    La qty es min(qty, qty_available) de una lectura de AHORA. Un campo
    ausente no se trata como cero: cero sería "no hay nada que vender"
    inventado, y vender el qty viejo con el disponible en cero abre un
    corto o vende de más."""
    try:
        p = client.posicion(ticker)
    except Exception as ex:
        log.warning(
            "%s: no se pudo releer la posición antes de vender (%s)",
            ticker, type(ex).__name__,
        )
        return "fallo", None
    if p is None:
        return "ausente", None
    if not isinstance(p, dict):
        log.warning("%s: la posición releída no es un objeto; no se vende", ticker)
        return "fallo", None
    lado = p.get("side")
    if not isinstance(lado, str) or lado.strip().lower() != "long":
        log.warning("%s: la posición releída no es long; no se vende", ticker)
        return "no", None
    if "qty" not in p or "qty_available" not in p:
        log.warning("%s: la posición releída no trae qty y qty_available; no se vende", ticker)
        return "no", None
    qty = _numero(p.get("qty"))
    disp = _numero(p.get("qty_available"))
    if qty is None or disp is None:
        log.warning("%s: qty o qty_available ilegible; no se vende", ticker)
        return "no", None
    texto = _texto_qty(min(qty, disp))
    if texto is None:
        log.warning("%s: no hay cantidad disponible para vender", ticker)
        return "no", None
    return "vender", texto


def _vender_respaldo(
    client: AlpacaPaperClient, ticker: str, ahora: datetime,
) -> tuple[dict | None, str | None]:
    estado_pos, qty = _leer_qty_vendible(client, ticker)
    if estado_pos == "ausente":
        log.info("%s: la posición ya no está; no se vende a mercado", ticker)
        return None, "sin_posicion"
    if estado_pos == "fallo":
        return None, "lectura"
    if estado_pos != "vender" or qty is None:
        return None, "sin_qty"
    try:
        resp = client.vender_a_mercado(
            ticker, qty, client_order_id=_id_orden_eod(ticker, ahora),
        )
    except Exception as ex:
        log.warning("%s: la venta a mercado de respaldo falló (%s)", ticker, type(ex).__name__)
        return None, "rechazo"
    if isinstance(resp, dict) and resp.get("id"):
        return resp, None
    log.warning("%s: la venta a mercado de respaldo no trajo id", ticker)
    return None, "rechazo"


def _liquidar(
    client: AlpacaPaperClient, ticker: str, abiertas: list, ahora: datetime,
) -> tuple[dict | None, str | None]:
    """(orden con id, código de fallo). El código es None si hay id.

    Cancelar primero es obligatorio: con las patas del bracket vivas el
    DELETE ve cantidad disponible 0 y Alpaca responde 403. La pata de
    stop suele estar `held` bajo el padre ya `filled`, que `status=open`
    no devuelve; el listado tiene que ser el de `ordenes_de_simbolos`.
    Después de cancelar se espera el status: un 204 no suelta la
    cantidad. `cancel_orders` en el DELETE de un símbolo no está
    documentado y no se usa como red.

    Un 404 del DELETE es "ya no hay posición", no un 4xx que autorice
    vender. La venta de respaldo, si hace falta, usa una lectura fresca
    y min(qty, qty_available). Un timeout o un 5xx no la disparan, y
    antes de reintentar se mira si ya quedó una venta viva: el DELETE
    pudo haber llegado igual."""
    ordenes = abiertas if isinstance(abiertas, list) else []
    ultimo_rechazo = False
    vio_404 = False
    for intento in range(1, _INTENTOS_DELETE + 1):
        patas = _patas_de_salida(ticker, ordenes)
        _cancelar(client, ticker, ordenes)
        espera = _esperar_patas(client, ticker, patas)
        if espera == "filled":
            return None, "pata_llena"
        if espera == "pendiente":
            # La espera se agotó sin un terminal. Aunque la posición
            # tenga qty vendible, vender acá es la carrera: se aborta
            # y se avisa. El ciclo siguiente reintenta.
            return None, "patas_pendientes"
        if espera != "listas":
            # Status ilegible o cualquier otra duda: tampoco se vende.
            return None, "sin_confirmar"
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
            # 404 no es un rechazo: la posición ya no está (la pata se
            # llenó, o un DELETE que había hecho timeout sí llegó).
            if status == 404:
                vio_404 = True
                ultimo_rechazo = False
            else:
                ultimo_rechazo = status is not None and 400 <= status < 500
            if intento < _INTENTOS_DELETE:
                # Una sola lectura: la que dice "ya hay una venta" es la
                # misma que se cancelaría en el intento siguiente. Leer
                # dos veces abre un hueco en el que la orden aparece
                # entre medias y el reintento la cancela.
                frescas = _leer_ordenes(client, ticker)
                if status is None or status >= 500:
                    if frescas is None:
                        return None, "sin_confirmar"
                    decision, previa = _venta_que_bloquea_reintento(ticker, frescas)
                    if (
                        decision == "ya_enviada"
                        and isinstance(previa, dict)
                        and previa.get("id")
                    ):
                        log.info("%s: no se reintenta el cierre; ya hay una venta viva", ticker)
                        return previa, None
                    if decision != "libre":
                        return None, "sin_confirmar"
                    ordenes = frescas
                else:
                    ordenes = frescas if frescas is not None else []
            continue
        if isinstance(resp, dict) and resp.get("id"):
            return resp, None
        log.warning("%s: el DELETE respondió sin id de orden; no se manda otra venta", ticker)
        return None, "sin_confirmar"
    if vio_404 and not ultimo_rechazo:
        estado_pos, _qty = _leer_qty_vendible(client, ticker)
        if estado_pos == "ausente":
            log.info("%s: DELETE 404 y la posición no está; no se vende", ticker)
            return None, "sin_posicion"
        if estado_pos == "fallo":
            return None, "lectura"
        log.warning(
            "%s: DELETE 404 pero la lectura no confirma que esté plana; no se vende",
            ticker,
        )
        return None, "sin_confirmar"
    if not ultimo_rechazo:
        return None, "sin_confirmar"
    return _vender_respaldo(client, ticker, ahora)


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

    simbolos = [
        p.get("symbol") for p in posiciones
        if isinstance(p, dict) and isinstance(p.get("symbol"), str) and p.get("symbol")
    ]
    try:
        # El mismo listado que la reconciliación: el stop held no está
        # en status=open, está bajo el padre filled.
        abiertas = client.ordenes_de_simbolos(simbolos)
    except Exception as ex:
        log.warning("no se pudieron leer las órdenes de las posiciones: %s", type(ex).__name__)
        abiertas = []
    if not isinstance(abiertas, list):
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

        orden, fallo = _liquidar(client, str(ticker), abiertas, ahora)
        if not orden:
            if fallo not in _SIN_AVISO:
                _avisar_cierre_fallido(str(ticker), fallo or "sin_confirmar", ahora)
            else:
                log.info("%s: sin venta de respaldo (%s)", ticker, fallo)
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
