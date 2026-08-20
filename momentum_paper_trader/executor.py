"""Orquestador -- lee el State Engine de `momentum_hunter` (nunca lo
modifica, nunca duplica su lógica de decisión) y coloca UNA orden paper
por cada entrada TRIGGERED que todavía no tenga una registrada. Nunca
re-evalúa si la señal es buena -- esa decisión ya la tomó
`momentum_hunter/run.py`; acá solo se ejecuta lo que ya se decidió,
con los niveles que el pipeline YA calculó (`EntradaWatchlist.
ultima_entrada/ultimo_stop/ultimo_objetivo`, ver
`momentum_hunter/watchlist.py`) -- ningún precio nuevo se inventa."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from momentum_hunter import watchlist
from momentum_hunter.run import enviar_telegram

from momentum_paper_trader import estado
from momentum_paper_trader.alpaca_client import AlpacaPaperClient, OrdenBracket
from momentum_paper_trader.config import PaperTraderConfig

log = logging.getLogger("momentum_paper_trader.executor")


def _tamano_posicion(entrada: float, stop: float, cfg: PaperTraderConfig) -> int:
    """Acciones = riesgo en dólares ÷ distancia al stop -- nunca un
    número de acciones fijo, para que el riesgo real de cada trade sea
    siempre el mismo sin importar qué tan ajustado esté el stop.
    Redondea hacia ABAJO (nunca arriesgar más de lo pedido); 0 si el
    riesgo no alcanza para ni una acción entera, o si el stop no está
    por debajo de la entrada (dato inconsistente, se omite)."""
    riesgo_por_accion = entrada - stop
    if riesgo_por_accion <= 0:
        return 0
    cantidad = int(cfg.riesgo_dolares_por_operacion // riesgo_por_accion)
    return cantidad if cantidad >= cfg.minimo_acciones else 0


def _mensaje_confirmacion(orden: OrdenBracket) -> str:
    """Etiquetado [PAPER] bien visible en la primera línea -- nunca debe
    poder confundirse con una alerta real de momentum_hunter."""
    riesgo = (orden.precio_entrada - orden.stop) * orden.cantidad
    return (
        f"🧪 [PAPER] ORDEN COLOCADA -- {orden.ticker}\n\n"
        f"Cantidad: {orden.cantidad}\n"
        f"Entrada (limit): ${orden.precio_entrada:,.2f}\n"
        f"Stop: ${orden.stop:,.2f}\n"
        f"Objetivo: ${orden.objetivo:,.2f}\n"
        f"Riesgo: ${riesgo:,.2f}\n\n"
        f"Cuenta de práctica -- ningún dinero real se movió."
    )


def ejecutar(
    client: AlpacaPaperClient, cfg: PaperTraderConfig = PaperTraderConfig(), dry_run: bool = False,
) -> list[estado.OrdenRegistrada]:
    """Devuelve las órdenes NUEVAS colocadas en esta corrida. En
    dry-run, calcula y loguea qué haría, pero nunca llama a Alpaca ni
    persiste nada (mismo principio que `momentum_hunter.run`)."""
    cfg.validar()
    entradas = watchlist.cargar()
    ordenes_previas = estado.cargar()
    nuevas: list[estado.OrdenRegistrada] = []

    for e in entradas:
        if e.estado != watchlist.ESTADO_TRIGGERED:
            continue
        if estado.ya_procesada(ordenes_previas, e.ticker, e.creado_en):
            continue
        if e.ultima_entrada is None or e.ultimo_stop is None or e.ultimo_objetivo is None:
            log.warning(
                "%s: TRIGGERED sin niveles cacheados -- se omite (no se inventa un precio)", e.ticker)
            continue

        cantidad = _tamano_posicion(e.ultima_entrada, e.ultimo_stop, cfg)
        if cantidad == 0:
            log.info(
                "%s: riesgo de $%.2f no alcanza para 1 acción con este stop -- se omite",
                e.ticker, cfg.riesgo_dolares_por_operacion)
            continue

        if dry_run:
            log.info(
                "[dry-run] %s: colocaría %d acciones @ $%.2f (stop $%.2f, objetivo $%.2f)",
                e.ticker, cantidad, e.ultima_entrada, e.ultimo_stop, e.ultimo_objetivo)
            continue

        try:
            orden = client.colocar_orden_bracket(
                e.ticker, cantidad, e.ultima_entrada, e.ultimo_stop, e.ultimo_objetivo)
        except Exception as ex:
            log.warning("%s: falló colocar la orden paper: %s", e.ticker, ex)
            continue

        registro = estado.OrdenRegistrada(
            ticker=e.ticker, creado_en=e.creado_en, order_id=orden.order_id,
            cantidad=orden.cantidad, precio_entrada=orden.precio_entrada,
            stop=orden.stop, objetivo=orden.objetivo,
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        ordenes_previas.append(registro)
        nuevas.append(registro)
        enviar_telegram(_mensaje_confirmacion(orden))
        log.info("%s: orden paper colocada (%s)", e.ticker, orden.order_id)

    if nuevas:
        estado.guardar(ordenes_previas)
    return nuevas
