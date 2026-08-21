"""Orquestador -- lee el State Engine de `momentum_hunter` (nunca lo
modifica, nunca duplica su lógica de decisión) y, para cada entrada
TRIGGERED que todavía no tenga una revisión registrada, le pide su
criterio a la IA (`ia_decision.decidir`, ver ese módulo para los
guardarraíles) y coloca UNA orden paper si -- y solo si -- la IA dice que
sí. Nunca re-evalúa si la señal PASA el filtro mecánico -- esa decisión ya
la tomó `momentum_hunter/run.py`; acá se decide si, dado que ya pasó ese
filtro, vale la pena arriesgar dinero (simulado) en ella. Los niveles
siempre son los que el pipeline YA calculó (`EntradaWatchlist.
ultima_entrada/ultimo_stop/ultimo_objetivo`, ver `momentum_hunter/
watchlist.py`) -- ni el código ni la IA inventan un precio nuevo."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from momentum_hunter import watchlist
from momentum_hunter.run import enviar_telegram

from momentum_paper_trader import estado, ia_decision
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


def _mensaje_confirmacion(orden: OrdenBracket, decision: ia_decision.DecisionIA) -> str:
    """Etiquetado [PAPER] bien visible en la primera línea -- nunca debe
    poder confundirse con una alerta real de momentum_hunter. Incluye el
    razonamiento de la IA (pedido explícito del usuario: "cada que haga
    un trade, que me avise qué hizo") -- no solo los niveles mecánicos."""
    riesgo = (orden.precio_entrada - orden.stop) * orden.cantidad
    return (
        f"🧪 [PAPER] ORDEN COLOCADA -- {orden.ticker}\n\n"
        f"Cantidad: {orden.cantidad}\n"
        f"Entrada (limit): ${orden.precio_entrada:,.2f}\n"
        f"Stop: ${orden.stop:,.2f}\n"
        f"Objetivo: ${orden.objetivo:,.2f}\n"
        f"Riesgo: ${riesgo:,.2f}\n\n"
        f"🤖 Por qué entró (confianza {decision.confianza}/10):\n{decision.razonamiento}\n\n"
        f"Cuenta de práctica -- ningún dinero real se movió."
    )


def ejecutar(
    client: AlpacaPaperClient, cfg: PaperTraderConfig = PaperTraderConfig(), dry_run: bool = False,
) -> list[estado.RevisionIA]:
    """Devuelve las revisiones NUEVAS que terminaron en una orden colocada
    en esta corrida (no las que la IA rechazó -- esas no colocan nada que
    reportar, aunque igual quedan registradas para no volver a
    preguntarse). En dry-run, calcula y loguea qué haría, pero nunca llama
    a Alpaca, nunca llama a la IA, y nunca persiste nada (mismo principio
    que `momentum_hunter.run`)."""
    cfg.validar()
    entradas = watchlist.cargar()
    revisiones_previas = estado.cargar()
    nuevas: list[estado.RevisionIA] = []

    for e in entradas:
        if e.estado != watchlist.ESTADO_TRIGGERED:
            continue
        if estado.ya_revisada(revisiones_previas, e.ticker, e.creado_en):
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
                "[dry-run] %s: pediría criterio a la IA y, de aprobar, colocaría %d acciones "
                "@ $%.2f (stop $%.2f, objetivo $%.2f)",
                e.ticker, cantidad, e.ultima_entrada, e.ultimo_stop, e.ultimo_objetivo)
            continue

        decision = ia_decision.decidir(e)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")

        if not decision.entrar:
            log.info(
                "%s: la IA no entra (confianza %d/10) -- %s",
                e.ticker, decision.confianza, decision.razonamiento)
            revisiones_previas.append(estado.RevisionIA(
                ticker=e.ticker, creado_en=e.creado_en, entro=False,
                confianza=decision.confianza, razonamiento=decision.razonamiento,
                timestamp=timestamp,
            ))
            estado.guardar(revisiones_previas)
            continue

        try:
            orden = client.colocar_orden_bracket(
                e.ticker, cantidad, e.ultima_entrada, e.ultimo_stop, e.ultimo_objetivo)
        except Exception as ex:
            log.warning("%s: la IA aprobó pero falló colocar la orden paper: %s", e.ticker, ex)
            # No se registra como revisada -- un fallo de RED/API de
            # Alpaca no es un "no" de la IA, así que la próxima corrida
            # debe poder reintentarlo con la misma entrada TRIGGERED.
            continue

        registro = estado.RevisionIA(
            ticker=e.ticker, creado_en=e.creado_en, entro=True,
            confianza=decision.confianza, razonamiento=decision.razonamiento,
            timestamp=timestamp, order_id=orden.order_id, cantidad=orden.cantidad,
            precio_entrada=orden.precio_entrada, stop=orden.stop, objetivo=orden.objetivo,
        )
        revisiones_previas.append(registro)
        nuevas.append(registro)
        estado.guardar(revisiones_previas)
        enviar_telegram(_mensaje_confirmacion(orden, decision))
        log.info("%s: orden paper colocada (%s)", e.ticker, orden.order_id)

    return nuevas
