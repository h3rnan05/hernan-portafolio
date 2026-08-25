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

CUÁNDO. `cfg.minutos_antes_del_cierre` antes de las 20:00 UTC (16:00 ET),
o sea 19:50 UTC por defecto. El re-chequeo de watchlist corre cada 5
minutos hasta las 20:00, así que siempre hay al menos una corrida dentro
de esa ventana. Misma convención de horario de verano que ya usan los
cron y `factors/intradia` -- con la misma limitación honesta: en horario
de invierno el mercado cierra a las 21:00 UTC y esta ventana quedaría una
hora antes de tiempo (cerraría a las 14:50 ET). Anotado, no resuelto:
requiere una fuente de calendario de mercado que este proyecto no tiene.

IDEMPOTENTE por construcción: la segunda corrida dentro de la ventana ya
no encuentra posiciones y no hace nada. No hace falta estado persistido."""

from __future__ import annotations

import logging
from datetime import datetime

from momentum_hunter.factors.intradia import HORA_CIERRE_UTC
from momentum_hunter.run import enviar_telegram

from momentum_paper_trader import ia_decision
from momentum_paper_trader.alpaca_client import AlpacaPaperClient
from momentum_paper_trader.config import PaperTraderConfig

log = logging.getLogger("momentum_paper_trader.cierre")


def _hora_utc(ahora: datetime) -> float:
    return ahora.hour + ahora.minute / 60.0


def en_ventana_de_cierre(ahora: datetime, cfg: PaperTraderConfig) -> bool:
    """¿Estamos en los últimos minutos de la sesión regular?

    La ventana va desde `minutos_antes_del_cierre` antes del cierre hasta
    el cierre mismo. Después de las 20:00 UTC ya no se intenta: el
    mercado está cerrado y una orden a mercado no se ejecutaría hasta el
    día siguiente -- justo lo contrario de lo que se busca."""
    inicio = HORA_CIERRE_UTC - cfg.minutos_antes_del_cierre / 60.0
    return inicio <= _hora_utc(ahora) < HORA_CIERRE_UTC


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
    """Un resumen de lo que se decidió, con el razonamiento de la IA en
    cada caso -- mismo principio que el mensaje de apertura: el usuario
    debe saber QUÉ hizo el bot y POR QUÉ, no solo el número."""
    lineas = ["🧪 [PAPER] CIERRE DEL DÍA", ""]
    total = 0.0
    hay_pl = False

    if cerradas:
        lineas.append("Cerradas:")
        for p, razon in cerradas:
            pl = _num(p.get("unrealized_pl"))
            cab = f"• {p.get('symbol', '?')}"
            if pl is not None:
                hay_pl = True
                total += pl
                cab += f": {'+' if pl >= 0 else '-'}${abs(pl):,.2f} aprox."
            lineas += [cab, f"  🤖 {razon}"]
        lineas.append("")

    if aguantadas:
        lineas.append("Se mantienen hasta mañana:")
        for p, razon, stop in aguantadas:
            pl = _num(p.get("unrealized_pl"))
            cab = f"• {p.get('symbol', '?')}"
            if pl is not None:
                cab += f": {'+' if pl >= 0 else '-'}${abs(pl):,.2f} abierto"
            lineas += [cab, f"  🤖 {razon}", f"  Stop de protección puesto en ${stop:,.2f}"]
        lineas += ["", "Aviso: un stop no protege contra un hueco de apertura. Si abre muy "
                   "por debajo, la venta se ejecuta al precio de apertura."]
        lineas.append("")

    if hay_pl:
        lineas += [f"Resultado realizado hoy: {'+' if total >= 0 else '-'}${abs(total):,.2f} aprox.", ""]
    lineas.append("Cuenta de práctica -- ningún dinero real se movió.")
    return "\n".join(lineas)


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

        try:
            client.cerrar_posicion(ticker)
        except Exception as ex:
            log.warning("%s: falló el cierre de la posición: %s", ticker, ex)
            continue
        cerradas.append((p, decision.razonamiento))
        log.info("%s: cerrada al final del día", ticker)

    if cerradas or aguantadas:
        enviar_telegram(_mensaje(cerradas, aguantadas))
    return [p for p, _ in cerradas]
