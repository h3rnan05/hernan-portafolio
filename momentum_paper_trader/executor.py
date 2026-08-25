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
watchlist.py`) -- ni el código ni la IA inventan un precio nuevo.

Guardarraíles DETERMINISTAS de cartera (antes y por encima de cualquier
criterio de la IA -- los límites de riesgo nunca dependen de un LLM):
  - nunca dos apuestas vivas sobre el mismo ticker (posición abierta u
    orden pendiente en Alpaca = ticker comprometido),
  - nunca más de `cfg.maximo_posiciones_abiertas` jugadas simultáneas,
  - nunca una orden cuyo costo exceda el EFECTIVO real de la cuenta
    (`cash`, nunca `buying_power` -- el margen 4x de Alpaca no es
    capital nuestro y este sistema no opera apalancado),
  - nunca una posición que pase de `cfg.maximo_pct_efectivo_por_posicion`
    del tamaño total de la cuenta (sin esto una acción cara se come el
    capital entero y el límite de posiciones no significa nada),
  - nunca una orden con el mercado cerrado (quedaría encolada para la
    apertura siguiente, con un precio de hoy -- ver `_mercado_cerrado`),
  - nunca un símbolo que Alpaca no marque como operable,
  - cada orden lleva un `client_order_id` derivado de la señal, para que
    un reintento del workflow no pueda duplicarla (ver `_id_de_orden`),
  - si la cuenta, el reloj o la ficha del activo no se pueden leer, no
    se opera (fail-closed, mismo principio que toda esta capa)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from momentum_hunter import watchlist
from momentum_hunter.run import enviar_telegram

from momentum_paper_trader import estado, ia_decision
from momentum_paper_trader.alpaca_client import AlpacaPaperClient, OrdenBracket
from momentum_paper_trader.config import PaperTraderConfig

log = logging.getLogger("momentum_paper_trader.executor")


def _niveles_rancios(e, cfg: PaperTraderConfig, ahora: datetime) -> float | None:
    """Antigüedad en minutos de los niveles si superan el tope, o `None`
    si están frescos (o si no hay timestamp con qué juzgarlos).

    El precio de entrada se congela cuando momentum_hunter evalúa la
    señal, pero la orden se coloca después. Sin este chequeo, una señal
    que quedó TRIGGERED y sin revisar (porque una corrida del trader
    falló) generaría, días más tarde, una orden con el precio de
    entonces -- comprar a un precio que ya no existe.

    Sin `ultimos_niveles_ts` no se bloquea nada: es un campo que se
    empezó a guardar después, y su ausencia no es evidencia de que los
    niveles estén viejos (mismo criterio que el resto del repo: no
    inventar el dato que falta, en ninguna de las dos direcciones)."""
    if not e.ultimos_niveles_ts:
        return None
    try:
        calculados = datetime.fromisoformat(e.ultimos_niveles_ts)
    except (TypeError, ValueError):
        return None
    if calculados.tzinfo is None:
        calculados = calculados.replace(tzinfo=UTC)
    minutos = (ahora - calculados).total_seconds() / 60.0
    return minutos if minutos > cfg.minutos_maximos_niveles else None


def _mercado_cerrado(client: AlpacaPaperClient) -> str | None:
    """Motivo por el que NO se debe colocar una orden de entrada ahora, o
    `None` si el mercado está abierto y operable.

    POR QUÉ EXISTE (encontrado el 2026-08-24 revisando el camino
    completo). Los dos workflows que invocan este módulo corren con cron
    `13-20 * * 1-5`, o sea desde las 13:00 hasta las 20:55 UTC -- pero la
    sesión regular va de 13:30 a 20:00 UTC (en verano). Eso deja corridas
    antes de la apertura y casi una hora de corridas después del cierre.
    Una orden bracket colocada ahí no se rechaza: queda ENCOLADA para la
    apertura siguiente, y se ejecutaría al día siguiente con un precio
    límite calculado el día anterior -- exactamente lo que
    `minutos_maximos_niveles` existe para impedir, pero por un camino que
    ese chequeo no ve (los niveles estaban frescos cuando se colocó).

    Se pregunta a Alpaca (`GET /v2/clock`) en vez de comparar contra una
    hora hardcodeada: es la única fuente del proyecto que sabe de
    feriados, medias sesiones y horario de invierno (en invierno la
    sesión es 14:30-21:00 UTC y cualquier constante de verano se
    equivoca por una hora entera).

    FAIL-CLOSED: si el reloj no se puede leer, no se opera. Mismo
    principio que `_leer_cuenta` -- ante la duda, no colocar."""
    try:
        reloj = client.reloj_mercado()
    except Exception as ex:
        return f"no se pudo leer el reloj del mercado ({type(ex).__name__})"
    if not reloj.get("is_open"):
        return "el mercado está cerrado -- una orden ahora quedaría encolada para mañana"
    return None


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


def _detalle_de_rechazo(ex: Exception) -> str:
    """Motivo legible de un rechazo de Alpaca, para que el log diga QUÉ
    pasó y no solo que algo falló.

    Alpaca devuelve `{"code": 40310000, "message": "..."}` con un código
    por motivo -- efectivo insuficiente, wash trade, sub-penny,
    `client_order_id` repetido, límite de peticiones. Sin esto, los tres
    se ven igual en el log ("HTTP 403") y el reporte semanal no puede
    distinguir un problema de dinero de uno de formato.

    Nunca lanza y nunca incluye la URL de la petición: esa lleva las
    credenciales en algunos clientes, y este texto termina en el log
    público del workflow."""
    respuesta = getattr(ex, "response", None)
    if respuesta is None:
        return type(ex).__name__
    try:
        cuerpo = respuesta.json()
        codigo, mensaje = cuerpo.get("code"), cuerpo.get("message")
    except Exception:
        codigo = mensaje = None
    estado_http = getattr(respuesta, "status_code", "?")
    if mensaje:
        return f"HTTP {estado_http}, código {codigo}: {mensaje}"
    return f"HTTP {estado_http} ({type(ex).__name__})"


def _id_de_orden(e) -> str:
    """Identificador determinista de la orden, derivado de la IDENTIDAD
    de la señal (ticker + cuándo entró a la watchlist), no del reloj.

    Es lo que hace que un reintento sea seguro: si el workflow muere
    después de que Alpaca aceptó la orden pero antes de que se persista
    la revisión, la corrida siguiente reconstruye exactamente el mismo
    id y Alpaca rechaza el duplicado en vez de abrir una segunda
    posición. Un id con timestamp no serviría -- sería distinto en cada
    intento, que es precisamente lo que hay que evitar.

    `creado_en` identifica la entrada de forma única (es lo que ya usa
    `estado.ya_revisada`); se limpia de caracteres raros para que sea un
    id válido."""
    limpio = "".join(c if c.isalnum() else "-" for c in e.creado_en)
    return f"momentum-{e.ticker}-{limpio}"


def _activo_no_operable(client: AlpacaPaperClient, ticker: str) -> str | None:
    """Motivo por el que NO se debe operar este símbolo, o `None` si se
    puede.

    Alpaca no expone un campo de "halted" intradía, así que `tradable`
    es lo más cerca que se puede estar sin una fuente externa de halts.
    No cubre un halt que empezó hace cinco minutos -- limitación real,
    documentada, no resuelta.

    FAIL-CLOSED: si la ficha del activo no se puede leer, no se opera.
    Mismo criterio que el reloj y la cuenta."""
    try:
        activo = client.activo(ticker)
    except Exception as ex:
        return f"no se pudo verificar si el símbolo es operable ({type(ex).__name__})"
    if activo.get("tradable") is not True:
        return f"Alpaca marca el símbolo como no operable (status: {activo.get('status')})"
    return None


def _techo_de_acciones(cuenta: "_EstadoCuenta", cfg: PaperTraderConfig, entrada: float) -> int:
    """Cuántas acciones caben, tomando el MENOR de los dos techos de
    cartera: el de concentración y el del efectivo real.

    - Concentración: contra el EQUITY (tamaño total de la cuenta), no
      contra el efectivo que queda. Con el efectivo, cada posición nueva
      se dimensionaría contra un número más chico que la anterior (15%,
      luego 15% del 85% restante, luego 15% de eso...) y el tamaño de una
      jugada dependería de en qué orden llegó la señal, no de su mérito.
      Contra el equity la regla es una sola y estable.
    - Efectivo: `cash` real, NUNCA `buying_power` -- el margen de Alpaca
      no es capital nuestro y este sistema no opera apalancado.

    Se aplica dos veces en `ejecutar`: una antes de consultar a la IA
    (para no gastar la llamada en algo que no se podría operar) y otra
    después, sobre la cantidad que la IA pidió. Es idempotente, así que
    calcularlo dos veces no cambia nada."""
    if entrada <= 0:
        return 0
    por_concentracion = (cuenta.equity * cfg.maximo_pct_efectivo_por_posicion) // entrada
    por_efectivo = cuenta.efectivo // entrada
    return max(0, int(min(por_concentracion, por_efectivo)))


def _mensaje_confirmacion(orden: OrdenBracket, decision: ia_decision.DecisionIA) -> str:
    """Etiquetado [PAPER] bien visible en la primera línea -- nunca debe
    poder confundirse con una alerta real de momentum_hunter. Incluye el
    razonamiento de la IA (pedido explícito del usuario: "cada que haga
    un trade, que me avise qué hizo") -- no solo los niveles mecánicos."""
    riesgo = (orden.precio_entrada - orden.stop) * orden.cantidad
    linea_tamano = (
        f"Tamaño: {decision.fraccion:.0%} del normal (convicción parcial)\n"
        if decision.fraccion < 1.0 else "")
    return (
        f"🧪 [PAPER] ORDEN COLOCADA -- {orden.ticker}\n\n"
        f"Cantidad: {orden.cantidad}\n"
        f"Entrada (limit): ${orden.precio_entrada:,.2f}\n"
        f"Stop: ${orden.stop:,.2f}\n"
        f"Objetivo: ${orden.objetivo:,.2f}\n"
        f"Riesgo: ${riesgo:,.2f}\n"
        f"{linea_tamano}\n"
        f"🤖 Por qué entró (confianza {decision.confianza}/10):\n{decision.razonamiento}\n\n"
        f"Cuenta de práctica -- ningún dinero real se movió."
    )


class _EstadoCuenta:
    """Snapshot de la cuenta paper al inicio de la corrida, actualizado
    localmente a medida que se colocan órdenes -- para que dos señales en
    la misma corrida no gasten el mismo efectivo dos veces ni excedan el
    máximo de posiciones entre las dos."""

    def __init__(self, efectivo: float, equity: float, tickers_comprometidos: set[str]) -> None:
        self.efectivo = efectivo
        self.equity = equity
        self.tickers_comprometidos = tickers_comprometidos

    def contexto_para_ia(self) -> str:
        ocupadas = ", ".join(sorted(self.tickers_comprometidos)) or "ninguna"
        return (
            f"Efectivo disponible: ${self.efectivo:,.2f} (equity total: ${self.equity:,.2f})\n"
            f"Posiciones/órdenes ya comprometidas ({len(self.tickers_comprometidos)}): {ocupadas}"
        )

    def registrar_orden(self, ticker: str, costo: float) -> None:
        self.efectivo -= costo
        self.tickers_comprometidos.add(ticker)


def _leer_cuenta(client: AlpacaPaperClient) -> _EstadoCuenta | None:
    """None si la cuenta no se puede leer -- la corrida entonces NO opera
    (fail-closed): sin saber el efectivo real y qué ya está comprometido,
    colocar órdenes sería operar a ciegas."""
    try:
        cuenta = client.info_cuenta()
        posiciones = client.posiciones()
        abiertas = client.ordenes_abiertas()
    except Exception as ex:
        log.warning("no se pudo leer el estado de la cuenta paper -- no se opera: %s", ex)
        return None
    comprometidos = {p.get("symbol") for p in posiciones} | {o.get("symbol") for o in abiertas}
    comprometidos.discard(None)
    return _EstadoCuenta(
        efectivo=float(cuenta.get("cash") or 0.0),
        equity=float(cuenta.get("equity") or 0.0),
        tickers_comprometidos=comprometidos,
    )


def ejecutar(
    client: AlpacaPaperClient, cfg: PaperTraderConfig = PaperTraderConfig(), dry_run: bool = False,
    ahora: datetime | None = None,
) -> list[estado.RevisionIA]:
    """Devuelve las revisiones NUEVAS que terminaron en una orden colocada
    en esta corrida (no las que la IA rechazó -- esas no colocan nada que
    reportar, aunque igual quedan registradas para no volver a
    preguntarse). En dry-run, calcula y loguea qué haría, pero nunca llama
    a Alpaca, nunca llama a la IA, y nunca persiste nada (mismo principio
    que `momentum_hunter.run`)."""
    cfg.validar()
    ahora = ahora or datetime.now(UTC)
    entradas = watchlist.cargar()
    revisiones_previas = estado.cargar()
    nuevas: list[estado.RevisionIA] = []

    pendientes = [
        e for e in entradas
        if e.estado == watchlist.ESTADO_TRIGGERED
        and not estado.ya_revisada(revisiones_previas, e.ticker, e.creado_en)
    ]
    if not pendientes:
        return nuevas

    cuenta: _EstadoCuenta | None = None
    if not dry_run:
        # Antes que nada: ¿el mercado está abierto? Una orden colocada
        # fuera de sesión no falla, queda encolada para mañana (ver
        # `_mercado_cerrado`). No se registra ninguna revisión: la señal
        # sigue viva para la próxima corrida dentro de sesión.
        cerrado = _mercado_cerrado(client)
        if cerrado is not None:
            log.info("no se colocan órdenes en esta corrida: %s", cerrado)
            return nuevas
        cuenta = _leer_cuenta(client)
        if cuenta is None:
            return nuevas

    for e in pendientes:
        if e.ultima_entrada is None or e.ultimo_stop is None or e.ultimo_objetivo is None:
            log.warning(
                "%s: TRIGGERED sin niveles cacheados -- se omite (no se inventa un precio)", e.ticker)
            continue

        # No se registra como revisada: la señal puede seguir siendo
        # buena, lo que está viejo es el PRECIO. El siguiente re-chequeo
        # (cada 5 min) recalcula los niveles y la orden se coloca ahí.
        rancios = _niveles_rancios(e, cfg, ahora)
        if rancios is not None:
            log.info(
                "%s: los niveles tienen %.0f min (tope %.0f) -- se espera a que se recalculen "
                "en vez de operar un precio viejo", e.ticker, rancios, cfg.minutos_maximos_niveles)
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

        # -- Guardarraíles deterministas de cartera (ANTES de gastar una
        # llamada a la IA en una señal que igual no se podría operar) --
        assert cuenta is not None
        if e.ticker in cuenta.tickers_comprometidos:
            log.info("%s: ya hay una posición u orden viva con este ticker -- se omite", e.ticker)
            continue
        if len(cuenta.tickers_comprometidos) >= cfg.maximo_posiciones_abiertas:
            log.info(
                "%s: la cuenta ya está en el máximo de %d posiciones simultáneas -- se omite",
                e.ticker, cfg.maximo_posiciones_abiertas)
            continue
        # Techo de GRANULARIDAD (2026-08-25). Si el tope de
        # concentración no da para al menos `minimo_acciones_para_operar`
        # acciones, la señal no se puede dimensionar: la fracción más
        # chica que la IA puede pedir redondearía a cero y su decisión
        # dejaría de ser expresable (ver config). Se descarta ACÁ, antes
        # de gastar una llamada a la IA en algo que no se podría operar.
        techo = _techo_de_acciones(cuenta, cfg, e.ultima_entrada)
        if techo < cfg.minimo_acciones_para_operar:
            log.info(
                "%s: a $%.2f la cuenta solo da para %d acción(es) (mínimo %d) -- "
                "esta señal no se puede dimensionar, se omite",
                e.ticker, e.ultima_entrada, techo, cfg.minimo_acciones_para_operar)
            continue

        no_operable = _activo_no_operable(client, e.ticker)
        if no_operable is not None:
            # No se registra revisión: el símbolo puede volver a ser
            # operable en la corrida siguiente (un halt se levanta).
            log.info("%s: %s -- se omite", e.ticker, no_operable)
            continue

        decision = ia_decision.decidir(e, cuenta.contexto_para_ia())
        timestamp = ahora.isoformat(timespec="seconds")

        if getattr(decision, "fallo_tecnico", False):
            # NO se registra como revisada: no hubo decisión que
            # registrar. Un fallo de infraestructura no debe quemar la
            # señal del día -- mismo criterio que ya se aplica más abajo
            # cuando falla la orden en Alpaca. La próxima corrida (a 5
            # minutos) lo reintenta.
            log.warning(
                "%s: no se pudo obtener decisión de la IA -- se reintentará: %s",
                e.ticker, decision.razonamiento)
            continue

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

        # ORDEN DE LAS OPERACIONES (corregido el 2026-08-25 -- costó un
        # trade real). La fracción de la IA se aplica al tamaño POR
        # RIESGO, y recién después muerden los topes de cartera.
        #
        # Antes era al revés: los topes recortaban primero y la fracción
        # reducía un número que ya estaba en el mínimo. Con LLY el tope
        # dejó 1 acción, la IA pidió la mitad, 1 x 0,5 = 0, y no se operó
        # pese a un "sí" explícito con confianza 7. Se recortaba dos
        # veces por lo mismo, porque la IA ya razona sobre la
        # concentración (ve el estado de la cuenta en su contexto).
        #
        # La separación correcta: la IA decide la INTENCIÓN ("media
        # posición"), los topes imponen la REALIDAD (cuánto cabe). Cada
        # uno una vez.
        cantidad = int(cantidad * decision.fraccion)
        cantidad = min(cantidad, _techo_de_acciones(cuenta, cfg, e.ultima_entrada))
        if cantidad < cfg.minimo_acciones:
            log.info(
                "%s: la fracción %.0f%% pedida por la IA no alcanza para 1 acción -- no se opera",
                e.ticker, decision.fraccion * 100)
            revisiones_previas.append(estado.RevisionIA(
                ticker=e.ticker, creado_en=e.creado_en, entro=False,
                confianza=decision.confianza,
                razonamiento=(decision.razonamiento
                              + " (La fracción de posición pedida no alcanzó para 1 acción entera "
                              "-- no se operó.)"),
                timestamp=timestamp,
            ))
            estado.guardar(revisiones_previas)
            continue

        try:
            orden = client.colocar_orden_bracket(
                e.ticker, cantidad, e.ultima_entrada, e.ultimo_stop, e.ultimo_objetivo,
                client_order_id=_id_de_orden(e))
        except Exception as ex:
            log.warning(
                "%s: la IA aprobó pero falló colocar la orden paper: %s",
                e.ticker, _detalle_de_rechazo(ex))
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
        cuenta.registrar_orden(e.ticker, cantidad * e.ultima_entrada)
        enviar_telegram(_mensaje_confirmacion(orden, decision))
        log.info("%s: orden paper colocada (%s)", e.ticker, orden.order_id)

    return nuevas
