"""La cuenta de Alpaca manda sobre lo que dice `revisiones.json`.

El 2026-09-25 el cierre de fin de día recibió 403, no guardó orden de
liquidación, y `seguimiento.py` marcó CTAS, TWST, NBIS y DLB como
`cerrada` con `pnl` vacío. El archivo las sacó de seguimiento. El lunes
seguían abiertas en el broker, sin stop, ocupando 4 de los 5 cupos.

Esto corre en cada pasada de `run.py` (cada tick del vigía: incluye el
arranque de la sesión y el momento posterior al cierre). Compara
`GET /v2/positions` con las revisiones vivas y con las órdenes de esos
símbolos (`status=all`, padre filled incluido).
Una posición del broker que nadie sigue, o que no tiene un stop de
venta abierto, dispara un Telegram ERROR. Una venta a mercado viva
(el cierre en curso) no es un stop, pero tampoco es "desprotegida":
hay una salida pendiente y no se alarma el cierre que está funcionando.

El stop de un bracket ya lleno no sale en `status=open`. Ese listado
trae solo el take-profit (`new`) y con `legs` vacío. El stop queda
`held` bajo la compra ya `filled`, o como fila propia de `status=all`
(MNST, 2026-09-28: padre `265e093f`, stop `f2d920f1` a $41.62, límite
`bb5baab2` a $42.36). El chequeo pide
`status=all&nested=true&symbols=<posiciones>` y cuenta una venta
`stop` / `stop_limit` / `trailing_stop` solo si su status es `held`,
`new`, `accepted` o `pending_new`. `pending_cancel`, un status
desconocido o ausente no es un stop.

No coloca órdenes ni cambia umbrales. El caso conocido de "posición
sin stop" por un take-profit parcial (FCEL, 2026-10-02: Alpaca cancela
el stop del OCO en cuanto una pata se ejecuta, aunque sea en parte) lo
corrige `reproteccion.py` en el mismo tick, justo antes de esta revisión;
lo que llegue hasta acá sin stop es lo que nadie supo reponer. Si no se pueden leer las
posiciones, no alerta y no inventa un "todo bien". Si no se pueden
leer las órdenes, sí alerta lo que no depende de ellas (una posición
sin revisión) y no afirma que falte el stop: un dato ausente no es
evidencia de que no haya protección."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from momentum_paper_trader import dedupe_avisos, estado, notify
from momentum_paper_trader.alpaca_client import (
    AlpacaPaperClient,
    orden_sigue_viva,
    ordenes_con_patas,
)

log = logging.getLogger("momentum_paper_trader.reconciliacion")

_TIPOS_STOP = frozenset({"stop", "stop_limit", "trailing_stop"})


@dataclass(frozen=True)
class Problema:
    ticker: str
    sin_seguimiento: bool
    sin_stop: bool


def _ticker_seguido(revisiones: list[estado.RevisionIA]) -> set[str]:
    """Revisión viva: hubo orden y el ciclo paper no terminó. Una
    `cerrada` / `objetivo` / `stop` ya no sigue la posición -- si el
    broker todavía la tiene, es justo el caso que hay que gritar."""
    return {
        r.ticker
        for r in revisiones
        if r.entro and r.order_id and r.resultado not in estado.RESULTADOS_TERMINALES
    }


def ventas_vivas(ordenes: list[dict] | None) -> list[dict] | None:
    """Ventas (fila o pata) que todavía trabajan.

    None si no hay listado: no es una lista vacía. Solo entra lo que
    `orden_sigue_viva` acepta (`held`, `new`, `accepted`, `pending_new`).
    `pending_cancel`, un status desconocido o ausente, y una pata ya
    `canceled` quedan fuera: una lista negra de "muertas" los contaría
    como protección."""
    if ordenes is None:
        return None
    salida: list[dict] = []
    for o in ordenes_con_patas(ordenes):
        simbolo = o.get("_symbol")
        if not isinstance(simbolo, str) or not simbolo:
            continue
        if not orden_sigue_viva(o):
            continue
        if str(o.get("side") or "").lower() != "sell":
            continue
        salida.append(o)
    return salida


def cobertura(ordenes: list[dict] | None) -> tuple[set[str], set[str]] | None:
    """(símbolos con stop de venta, símbolos con venta a mercado).
    None si no hay listado: no se puede afirmar que falte el stop.

    Mira la fila y sus `legs` vía `ventas_vivas`. El padre de un bracket
    lleno es una compra `filled`: no protege, pero sus patas sí pueden.
    Cuenta un `stop` / `stop_limit` / `trailing_stop` de venta, o una
    venta a mercado en curso, solo con status `held`, `new`, `accepted`
    o `pending_new`. El take-profit es un `limit` y no entra. Un stop
    en `pending_cancel` tampoco."""
    vivas = ventas_vivas(ordenes)
    if vivas is None:
        return None
    stops: set[str] = set()
    mercados: set[str] = set()
    for o in vivas:
        simbolo = o.get("_symbol")
        tipo = str(o.get("type") or "").lower()
        if tipo in _TIPOS_STOP:
            stops.add(simbolo)
        elif tipo == "market":
            mercados.add(simbolo)
    return stops, mercados


def detectar(
    posiciones: list[dict],
    ordenes: list[dict] | None,
    revisiones: list[estado.RevisionIA],
) -> list[Problema]:
    """Puro: no toca red ni disco. Una posición listada por el broker
    cuenta aunque su `qty` sea ilegible (estar en el listado es el dato;
    la cantidad ausente no la borra)."""
    seguidos = _ticker_seguido(revisiones)
    cobertura_de = cobertura(ordenes)
    problemas: list[Problema] = []
    vistos: set[str] = set()
    for p in posiciones:
        if not isinstance(p, dict):
            continue
        simbolo = p.get("symbol")
        if not isinstance(simbolo, str) or not simbolo or simbolo in vistos:
            continue
        vistos.add(simbolo)
        sin_seguimiento = simbolo not in seguidos
        if cobertura_de is None:
            sin_stop = False
        else:
            stops, mercados = cobertura_de
            sin_stop = simbolo not in stops and simbolo not in mercados
        if sin_seguimiento or sin_stop:
            problemas.append(Problema(simbolo, sin_seguimiento, sin_stop))
    return problemas


def _frase(p: Problema) -> str:
    partes: list[str] = []
    if p.sin_seguimiento:
        partes.append("no hay una revisión viva que la siga")
    if p.sin_stop:
        partes.append("no tiene stop de venta abierto")
    return f"{p.ticker}: " + " y ".join(partes) + "."


def revisar(client: AlpacaPaperClient, ahora: datetime | None = None) -> list[str]:
    """Avisa los problemas nuevos de esta pasada. Devuelve esos tickers.
    Nunca lanza: un fallo acá no puede tumbar el tick ni el cierre."""
    try:
        return _revisar(client, ahora or datetime.now(UTC))
    except Exception as ex:
        log.warning("reconciliación de posiciones no completada (%s)", type(ex).__name__)
        return []


def _revisar(client: AlpacaPaperClient, ahora: datetime) -> list[str]:
    try:
        posiciones = client.posiciones()
    except Exception as ex:
        log.warning("reconciliación: no se pudieron leer las posiciones (%s)", type(ex).__name__)
        return []
    if not isinstance(posiciones, list):
        # Un cuerpo ilegible no es "cero posiciones".
        log.warning("reconciliación: el listado de posiciones no es una lista; no se alerta")
        return []

    simbolos: list[str] = []
    vistos_simbolos: set[str] = set()
    for p in posiciones:
        if not isinstance(p, dict):
            continue
        simbolo = p.get("symbol")
        if isinstance(simbolo, str) and simbolo and simbolo not in vistos_simbolos:
            vistos_simbolos.add(simbolo)
            simbolos.append(simbolo)

    ordenes: list[dict] | None
    if not simbolos:
        ordenes = []
    else:
        try:
            # status=open no trae al padre filled, y el stop held vive
            # en sus legs. Hace falta status=all de estos símbolos.
            crudas = client.ordenes_de_simbolos(simbolos)
        except Exception as ex:
            log.warning(
                "reconciliación: no se pudieron leer las órdenes (%s); no se afirma que falte el stop",
                type(ex).__name__,
            )
            ordenes = None
        else:
            ordenes = crudas if isinstance(crudas, list) else None
            if ordenes is None:
                log.warning(
                    "reconciliación: las órdenes de los símbolos en posición no son una lista; "
                    "no se afirma que falte el stop"
                )

    try:
        revisiones = estado.cargar()
    except Exception as ex:
        log.warning("reconciliación: no se pudieron leer las revisiones (%s)", type(ex).__name__)
        return []

    problemas = detectar(posiciones, ordenes, revisiones)
    nuevos = [
        p for p in problemas
        if not dedupe_avisos.ya_avisada(dedupe_avisos.clave("broker", p.ticker, ahora))
    ]
    if not nuevos:
        return []

    texto = notify.formatear_error(
        tipo="posición del broker sin seguimiento o sin stop",
        detalle=" ".join(_frase(p) for p in nuevos)
        + " Sigue ocupando cupo y puede quedar desprotegida.",
    )
    notify.enviar(texto)
    for p in nuevos:
        dedupe_avisos.marcar(dedupe_avisos.clave("broker", p.ticker, ahora), ahora)
    log.error("reconciliación: %s", ", ".join(_frase(p) for p in nuevos))
    return [p.ticker for p in nuevos]
