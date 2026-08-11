"""Orchestrator del Momentum Opportunity Hunter -- pipeline en DOS
etapas (pivote 2026-07-26, Prompt 1: "quiero que piense como un trader
de momentum, no como un screener").

**Etapa 1 (gruesa, diaria)**: universo → barras diarias → filtros de
precio/cap/liquidez/catalizador → `scoring.puntuar` → recorta a los
`cfg.max_candidatos_intradia` mejores (`CandidatoDiario`). Es
prácticamente el pipeline de antes del pivote -- sigue siendo necesario
porque pedir datos intradía para miles de tickers no es viable con
ningún proveedor gratis (ver `data/provider.py`).

**Etapa 2 (fina, intradía)**: SOLO sobre esos candidatos, pide velas de
1 minuto, calcula factores intradía reales (VWAP, EMA9, RVOL inmediato),
detecta uno de los seis patrones de Ross Cameron (`classification.py`) y
corre el árbol de 5 preguntas (`evaluator.py`) que decide si la alerta
es accionable. El Market Radar (`radar.py`) recoge lo que quedó "cerca"
pero no accionable.

USO
  python -m momentum_hunter.run                      # universo completo NYSE+NASDAQ+AMEX
  python -m momentum_hunter.run --limit 500           # subconjunto (recomendado en CI)
  python -m momentum_hunter.run --universo watchlist.txt  # archivo propio, un ticker por línea
  python -m momentum_hunter.run --no-catalizadores    # sin noticias -- nunca hay alerta real así
  python -m momentum_hunter.run --dry-run             # calcula y muestra, no manda ni registra
  python -m momentum_hunter.run --actualizar-resultados  # no escanea: actualiza el tracker

VARIABLES DE ENTORNO
  MOMENTUM_TELEGRAM_BOT_TOKEN / MOMENTUM_TELEGRAM_CHAT_ID
      Chat dedicado a este bot. Si no están, cae a TELEGRAM_BOT_TOKEN /
      TELEGRAM_CHAT_ID -- nunca falla por falta de secrets, solo deja de
      enviar y sigue registrando en el tracker.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import requests

from momentum_hunter import (
    audit,
    classification,
    diario,
    evaluator,
    heartbeat,
    memoria,
    outcomes,
    radar,
    report,
    skeptic,
    stats,
    tracker,
    universe,
    vigilancia,
    watchlist,
)
from momentum_hunter.alerts import (
    CandidatoDiario,
    CandidatoIntradia,
    accionables_ordenados,
    candidatos_para_etapa_intradia,
    cuota_alertas,
)
from momentum_hunter.catalysts.detector import YahooNewsProvider, detectar_catalizador, minutos_desde_catalizador
from momentum_hunter.config import CONFIG, MomentumConfig
from momentum_hunter.data.provider import DataProvider, YahooProvider
from momentum_hunter.factors import intradia as fi
from momentum_hunter.factors import momentum as mom
from momentum_hunter.models import Barras
from momentum_hunter.scoring import puntuar

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("momentum_hunter.run")


def _volumen_promedio(b: Barras, ventana: int = 20) -> float | None:
    if len(b.volume) < ventana:
        return None
    return sum(b.volume[-ventana:]) / ventana


def enviar_telegram(texto: str) -> None:
    token = os.getenv("MOMENTUM_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("MOMENTUM_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.info("sin secrets de Telegram: no envío (solo registro en el tracker)")
        return
    for i in range(0, len(texto), 3900):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": texto[i:i + 3900]}, timeout=15,
            )
        except Exception as e:
            log.warning("envío a Telegram falló: %s", e)


def _banda_de_universo(b: Barras, cfg: MomentumConfig) -> str | None:
    """"small" o "large", o None si no califica para ninguna banda.
    Large-cap (2026-08-07) es COMPLEMENTARIA a small-cap, no la
    reemplaza: cualquier ticker con precio por ENCIMA de `precio_max`
    entra por ahí en vez de quedar descartado, con su propio piso de
    liquidez (empresas grandes trafican mucho más como línea de base) --
    ver docstring de `config.incluir_large_cap`."""
    if not b.close or b.close[-1] <= 0:
        return None
    precio = b.close[-1]
    vol_prom = _volumen_promedio(b)
    if cfg.precio_min <= precio <= cfg.precio_max:
        if vol_prom is not None and vol_prom >= cfg.volumen_promedio_min:
            return "small"
        return None
    if cfg.incluir_large_cap and precio > cfg.precio_max:
        if vol_prom is not None and vol_prom >= cfg.volumen_promedio_min_large_cap:
            return "large"
    return None


def construir_candidatos_diarios(
    tickers_validos: list[str], barras: dict[str, Barras], provider: DataProvider,
    cfg: MomentumConfig, con_catalizadores: bool, bandas: dict[str, str] | None = None,
) -> list[CandidatoDiario]:
    """Etapa 1 -- núcleo puro y testeable: recibe todo ya inyectado
    (barras, metadata, catalizadores), nunca llama red directamente. Un
    ticker que falle no debe tumbar la corrida completa.

    `bandas` (2026-08-07, modo large-cap): mapa ticker -> "small"/"large"
    de `_banda_de_universo`. Un ticker en la banda "large" se salta el
    techo `market_cap_max` (ese techo es justamente lo que separa las dos
    bandas) y queda marcado `es_large_cap=True` para que `evaluator.py`
    sepa qué pregunta 3 aplicar. Sin `bandas` (compatibilidad con
    llamadas existentes/pruebas), todo se trata como small-cap de
    siempre."""
    bandas = bandas or {}
    metadata = provider.metadata(tickers_validos)
    noticias = YahooNewsProvider() if con_catalizadores else None

    candidatos: list[CandidatoDiario] = []
    for t in tickers_validos:
        try:
            b = barras[t]
            meta = metadata.get(t)
            if meta is None:
                continue
            es_large_cap = bandas.get(t) == "large"
            if meta.es_etf or (cfg.excluir_spac and meta.es_spac) or (cfg.excluir_cef and meta.es_cef):
                continue
            if not es_large_cap and meta.market_cap is not None and meta.market_cap > cfg.market_cap_max:
                continue
            vol_prom = _volumen_promedio(b)
            if meta.es_adr and (vol_prom is None or vol_prom < cfg.liquidez_minima_adr):
                continue

            factores = mom.calcular(b)
            catalizador = None
            if noticias is not None:
                catalizador = detectar_catalizador(noticias.titulares(t), cfg)

            puntuacion = puntuar(t, b.close[-1], vol_prom, factores, catalizador, meta, cfg)
            candidatos.append(CandidatoDiario(
                ticker=t, nombre=meta.nombre, precio=b.close[-1], volumen_promedio=vol_prom,
                factores=factores, catalizador=catalizador, meta=meta, puntuacion=puntuacion,
                es_large_cap=es_large_cap,
            ))
        except Exception as e:
            log.warning("candidato diario %s falló: %s", t, e)
    return candidatos


def _cierre_anterior(barras_diarias: Barras, fecha_hoy: str) -> float | None:
    """Cierre de la sesión ANTERIOR a hoy -- si la última barra diaria ya
    es de hoy (Yahoo actualiza la barra del día en curso en vivo durante
    el mercado), el cierre de ayer es el penúltimo dato, no el último.
    Aproximación best-effort documentada, no una fuente de "cierre
    oficial de ayer" separada."""
    if not barras_diarias.fechas or not barras_diarias.close:
        return None
    ultima_fecha = datetime.fromtimestamp(int(barras_diarias.fechas[-1]), tz=UTC).date().isoformat()
    if ultima_fecha == fecha_hoy and len(barras_diarias.close) >= 2:
        return barras_diarias.close[-2]
    return barras_diarias.close[-1]


def _nivel_para_patron(patron: str | None, factores) -> float | None:
    """Nivel de referencia para medir "velas desde la ruptura" -- el que
    de verdad definió la entrada de cada patrón, nunca uno genérico."""
    if patron == "gap_and_go":
        return factores.maximo_premarket
    if patron == "opening_range_breakout":
        return factores.rango_apertura_max
    if factores.ema9 is not None:
        return factores.ema9
    return factores.vwap


def _construir_candidato_intradia(
    ticker: str, nombre: str | None, catalizador, meta, es_large_cap: bool,
    atr_diario: float | None, score_base: float, cierre_anterior: float | None,
    bi, cfg: MomentumConfig, gap_pct_fallback: float | None = None,
) -> CandidatoIntradia | None:
    """Núcleo compartido de la etapa 2 -- lo usan tanto
    `construir_candidatos_intradia` (descubrimiento, con `cierre_anterior`
    real de las barras diarias) como `revisar_watchlist` (chequeo
    liviano, "Fase 2": sin barras diarias frescas). None si no hay velas
    de hoy todavía.

    Sin `cierre_anterior` (siempre el caso en `revisar_watchlist`), se
    deriva directamente de las velas intradía de `bi` (`periodo="5d"` ya
    trae la sesión anterior completa) antes de calcular el gap --
    corrección 2026-08-11 (revisión de PR): antes de esto, una candidata
    descubierta ANTES de la apertura regular (sin `gap_pct_congelado`
    todavía, porque no hay vela regular con la que congelarlo en el
    momento del descubrimiento) se quedaba sin gap para siempre en el
    chequeo liviano, justo en la ventana de apertura que el patrón "gap
    and go" necesita. `gap_pct_fallback` (el gap ya congelado) sigue
    siendo el último recurso si ni siquiera `bi` alcanza."""
    hoy = fi.barras_de_hoy(bi)
    if not hoy.timestamps:
        return None
    if cierre_anterior is None:
        cierre_anterior = fi.cierre_sesion_anterior(bi)
    factores = fi.calcular(bi, cierre_anterior)
    if factores.gap_pct is None and gap_pct_fallback is not None:
        factores = replace(factores, gap_pct=gap_pct_fallback)

    patron_preliminar = classification.detectar_patron(hoy, factores)
    nivel_ruptura = _nivel_para_patron(patron_preliminar, factores)
    if nivel_ruptura is not None:
        velas = fi.velas_desde_ruptura(hoy, nivel_ruptura)
        factores = replace(factores, velas_desde_ruptura=velas)

    minutos = minutos_desde_catalizador(catalizador)
    niveles = report.niveles_entrada_salida(factores, atr_diario)
    entrada = factores.precio_actual if factores.precio_actual is not None else 0.0

    resultado_eval = evaluator.evaluar(
        catalizador, minutos, factores, hoy, meta,
        entrada, niveles["stop"], niveles["objetivo"], score_base, cfg,
        es_large_cap=es_large_cap,
    )
    return CandidatoIntradia(
        ticker=ticker, nombre=nombre, catalizador=catalizador,
        minutos_desde_catalizador=minutos, factores=factores, bi_hoy=hoy,
        meta=meta, atr_diario=atr_diario, resultado=resultado_eval,
        es_large_cap=es_large_cap,
    )


def construir_candidatos_intradia(
    shortlist: list[CandidatoDiario], barras_diarias: dict[str, Barras],
    provider: DataProvider, cfg: MomentumConfig, on_datos_recibidos: Callable[[], None] | None = None,
) -> list[CandidatoIntradia]:
    """Etapa 2 -- SOLO sobre `shortlist` (ya recortada por
    `alerts.candidatos_para_etapa_intradia`). Un ticker que falle no
    tumba la corrida completa.

    `on_datos_recibidos` (2026-08-11, corrección de revisión de PR):
    callback opcional invocado justo DESPUÉS de que `provider.
    barras_intradia` devuelve -- para tickers en la banda large-cap con
    `max_candidatos_intradia` alto, este pedido secuencial puede tardar
    minutos (ver README). `main()` lo usa para capturar el reloj real de
    "cuándo llegaron los datos" (`dato_recibido_ts`) en vez de un reloj
    tomado ANTES de pedirlos, que atribuía todo el tiempo de descarga al
    paso equivocado en la latencia registrada."""
    tickers = [c.ticker for c in shortlist]
    barras_intradia = provider.barras_intradia(tickers, cfg.intervalo_intradia, cfg.periodo_intradia)
    if on_datos_recibidos is not None:
        on_datos_recibidos()

    resultado: list[CandidatoIntradia] = []
    for c in shortlist:
        try:
            bi = barras_intradia.get(c.ticker)
            if bi is None:
                continue
            hoy = fi.barras_de_hoy(bi)
            if not hoy.timestamps:
                continue
            cierre_ant = _cierre_anterior(barras_diarias[c.ticker], hoy.timestamps[-1][:10])
            candidato = _construir_candidato_intradia(
                c.ticker, c.nombre, c.catalizador, c.meta, c.es_large_cap,
                c.factores.atr, c.puntuacion.score_total, cierre_ant, bi, cfg,
            )
            if candidato is not None:
                resultado.append(candidato)
        except Exception as e:
            log.warning("candidato intradía %s falló: %s", c.ticker, e)
    return resultado


# Refinamiento "Head Trader" (2026-07-27), punto 10 -- la última
# pregunta: "si este fuera mi propio dinero y solo pudiera hacer una
# operación hoy, ¿realmente abriría esta posición?". Regla fija: con más
# de MAX_DUDAS_PARA_SI_CLARO dudas acumuladas (advertencias no fatales
# del debate), la respuesta ya no es un "sí claro" -- y sin un sí claro
# no hay alerta. Cada duda individual está en la auditoría, así que la
# decisión es rastreable.
MAX_DUDAS_PARA_SI_CLARO = 2


def seleccionar_y_auditar(
    candidatos: list[CandidatoIntradia], cfg: MomentumConfig,
    historial=None, hora_utc: float | None = None, n_universo: int = 0,
) -> tuple[list, dict[str, str], list[dict]]:
    """El tramo final del pipeline (pedido 2026-07-27, "sistema en el que
    confiaría mi patrimonio"): competencia relativa (Principio 4) +
    debate del abogado del diablo (Principios 1/2/11) + memoria
    contextual (Principios 3/12) + la última pregunta ("¿realmente
    abriría esta posición?") + auditoría completa de CADA candidato
    (Principios 6/7/9). Devuelve (oportunidades a alertar, vetadas
    {ticker: motivo}, snapshots de auditoría).

    Puro y testeable: `historial` y `hora_utc` son inyectables; solo se
    leen del mundo real cuando no se pasan. `n_universo` es cuántas
    acciones escaneó la etapa 1 -- para el ranking absoluto del mensaje
    ("la #1 del día entre N escaneadas")."""
    historial = tracker.cargar() if historial is None else historial
    ahora = datetime.now(UTC)
    hora_utc = hora_utc if hora_utc is not None else ahora.hour + ahora.minute / 60.0

    cuota = cuota_alertas(cfg)
    ordenados = accionables_ordenados(candidatos)
    elegidas: list[tuple[CandidatoIntradia, object]] = []
    vetadas: dict[str, str] = {}
    decisiones: dict[str, tuple[str, list[str], list[str]]] = {}

    for c in ordenados:
        if len(elegidas) >= cuota:
            mejor = elegidas[0][0].ticker
            decisiones[c.ticker] = (
                audit.DECISION_PERDIO_COMPETENCIA,
                [f"Calificó, pero {mejor} quedó por encima en la competencia relativa de esta corrida."],
                ["Que su configuración supere a la mejor del día, o que la mejor se invalide."],
            )
            continue

        niveles = report.niveles_entrada_salida(c.factores, c.atr_diario)
        ctx_patron = memoria.contexto_patron(historial, c.resultado.patron)
        ctx_catalizador = memoria.contexto_catalizador(
            historial, c.catalizador.tipo if c.catalizador else None)
        avisos_memoria = tuple(memoria.advertencias_contextuales([ctx_patron, ctx_catalizador]))
        objeciones = skeptic.refutar(
            c.factores, c.minutos_desde_catalizador, niveles["stop"], hora_utc, avisos_memoria)
        fatales = [o for o in objeciones if o.fatal]
        if fatales:
            vetadas[c.ticker] = fatales[0].texto
            decisiones[c.ticker] = (
                audit.DECISION_VETADA,
                [o.texto for o in fatales],
                [o.que_cambiaria for o in fatales],
            )
            continue

        advertencias = [o.texto for o in objeciones if not o.fatal]

        # La última pregunta (punto 10): sobrevivió el debate, pero si
        # quedó cargada de dudas, la respuesta no es un "sí claro".
        if len(advertencias) > MAX_DUDAS_PARA_SI_CLARO:
            motivo = (f"Sobrevivió los filtros, pero acumuló {len(advertencias)} dudas -- "
                      "eso ya no es un sí claro, y sin un sí claro no arriesgo dinero.")
            vetadas[c.ticker] = motivo
            decisiones[c.ticker] = (
                audit.DECISION_SIN_CONVICCION,
                [motivo, *advertencias],
                ["Que la misma configuración aparezca con menos dudas acumuladas a la vez."],
            )
            continue

        _, confianza_texto = memoria.confianza(ctx_patron, len(advertencias))
        oportunidad = report.construir_oportunidad(
            c, cfg.velas_maximas_desde_patron,
            probabilidad_historica=memoria.frase_probabilidad(ctx_patron),
            advertencias=advertencias, n_evaluados=len(candidatos),
            rank=len(elegidas) + 1, n_universo=n_universo,
            confianza_texto=confianza_texto,
            calidad_historica=memoria.linea_calidad(ctx_patron),
            hora_utc=hora_utc, cfg=cfg,
        )
        elegidas.append((c, oportunidad))
        decisiones[c.ticker] = (
            audit.DECISION_ALERTADA,
            ["Sobrevivió las 5 preguntas del evaluador, el debate del abogado del diablo "
             "y la última pregunta."]
            + advertencias,
            [],
        )

    snapshots = []
    for c in candidatos:
        if c.ticker in decisiones:
            decision, motivos, cambios = decisiones[c.ticker]
        else:
            decision = audit.DECISION_DESCARTADA
            motivos = list(c.resultado.penalizaciones) or ["No fue accionable."]
            cambios = evaluator.explicar_rechazo(c.resultado, cfg)
        snapshots.append(audit.snapshot_candidato(c, decision, motivos, cambios))

    return [o for _, o in elegidas], vetadas, snapshots


def _cargar_tickers(args: argparse.Namespace) -> list[str]:
    if args.universo:
        ticks = universe.desde_archivo(args.universo)
    else:
        ticks = universe.tickers(refrescar=args.refrescar_universo)
    if args.limit:
        ticks = ticks[: args.limit]
    return ticks


def _modo_actualizar_resultados(cfg: MomentumConfig) -> None:
    alertas = tracker.cargar()
    if not alertas:
        log.info("no hay alertas registradas todavía")
        return
    outcomes.actualizar_resultados(alertas, YahooProvider(), cfg)
    # Punto 9 ("Head Trader"): cada alerta recién resuelta genera su
    # página de aprendizaje -- una sola vez (diario_escrito).
    rutas = diario.escribir_nuevas(alertas)
    tracker.guardar(alertas)
    if rutas:
        log.info("diario: %d página(s) de aprendizaje nueva(s)", len(rutas))
    for h in cfg.horizontes_seguimiento:
        e = stats.calcular_estadisticas(alertas, h)
        log.info(
            "horizonte %dd: n=%d win_rate=%s retorno_prom=%s drawdown=%s expectancy=%s sharpe=%s",
            h, e.n, e.win_rate, e.retorno_promedio, e.drawdown_maximo, e.expectancy, e.sharpe,
        )


def _actualizar_watchlist(
    shortlist: list[CandidatoDiario], candidatos_intradia: list[CandidatoIntradia],
    elegidas_tickers: set[str], cfg: MomentumConfig, dry_run: bool, ahora: datetime | None = None,
    dato_recibido_ts: str | None = None,
) -> tuple[list, dict[str, object]]:
    """State Engine (2026-08-11, "Fase 2"): toda candidata con
    catalizador confirmado que llega a la etapa 2 entra a vigilancia
    persistida. Las que se alertan AHORA MISMO se marcan TRIGGERED de
    una vez (el historial de transiciones queda completo incluso para
    las inmediatas, no solo para las que confirma el chequeo liviano de
    `revisar_watchlist`); el resto queda en WATCHING, INVALIDATED o
    MISSED según lo que ya decidió `evaluator.evaluar` -- ningún cálculo
    nuevo, solo se traduce el mismo veredicto a una transición de estado.

    `dato_recibido_ts` (2026-08-11, corrección de revisión de PR): reloj
    tomado por `main()` justo antes de pedir las velas intradía de la
    etapa 2 (`provider.barras_intradia`, dentro de `construir_candidatos_
    intradia`) -- un instante real y DISTINTO de `evaluador_ts`. Antes de
    este fix, `marcar_triggered` recibía el mismo reloj dos veces
    (`evaluador_ts` también como `data_received_ts`), como si pedir los
    datos hubiera tardado cero segundos -- nunca medido, siempre
    inventado. Si no se pasa (compatibilidad), cae a `evaluador_ts`.

    Devuelve `(entradas, disparadas)` -- `disparadas` (ticker ->
    entrada) son las que quedaron TRIGGERED en esta misma corrida, pero
    TODAVÍA sin `signal_latency_ms` real: el envío de verdad a Telegram
    ocurre después, en `main()` (formatear + `enviar_telegram` tardan un
    tiempo real que no se puede medir desde acá). Corrección 2026-08-11
    (encontrado en revisión de PR): antes esta función rellenaba
    `mensaje_generado_ts`/`telegram_enviado_ts` con el mismo reloj
    capturado al EMPEZAR la función, antes de que el mensaje siquiera se
    armara -- una latencia inventada, no medida. Ahora `main()` completa
    `registrar_latencia` con relojes frescos tomados alrededor del envío
    real, igual que ya hace `revisar_watchlist`.

    En dry-run no se persiste (mismo principio que el resto de `main()`:
    dry-run calcula y muestra, nunca muta estado)."""
    ahora = ahora or datetime.now(UTC)
    entradas = watchlist.cargar()
    entradas = watchlist.agregar_nuevas(entradas, shortlist, ahora)
    por_ticker = {e.ticker: e for e in entradas}

    disparadas: dict[str, object] = {}
    for c in candidatos_intradia:
        e = por_ticker.get(c.ticker)
        if e is None or e.estado != watchlist.ESTADO_WATCHING:
            continue
        if e.gap_pct_congelado is None and c.factores.gap_pct is not None:
            # Se congela una sola vez -- el gap de apertura no cambia
            # durante el resto de la sesión (ver `_construir_candidato_
            # intradia`), así `revisar_watchlist` no necesita barras
            # diarias frescas para recalcularlo.
            e.gap_pct_congelado = c.factores.gap_pct
        if c.ticker in elegidas_tickers:
            market_ts = c.bi_hoy.timestamps[-1] if c.bi_hoy.timestamps else _ahora_iso_run(ahora)
            evaluador_ts = _ahora_iso_run(datetime.now(UTC))
            watchlist.marcar_triggered(e, market_ts, dato_recibido_ts or evaluador_ts, evaluador_ts, ahora)
            disparadas[c.ticker] = e
        else:
            _evaluar_no_disparada(e, c, cfg, ahora)

    entradas = watchlist.purgar_antiguas(entradas, ahora)
    watchlist.expirar_vencidas(entradas, cfg.minutos_maximos_en_watching, ahora)
    n_watching = len(watchlist.activas(entradas))
    log.info("watchlist: %d en observación tras esta corrida", n_watching)
    if not dry_run:
        watchlist.guardar(entradas)
    return entradas, disparadas


def _ahora_iso_run(ahora: datetime) -> str:
    return ahora.isoformat(timespec="seconds")


def _filtrar_ya_resueltas_hoy(
    oportunidades: list, entradas_watchlist: list, disparadas_watchlist: dict,
    ahora: datetime | None = None,
) -> tuple[list, set[str]]:
    """Corrección 2026-08-11 (revisión de PR): el escaneo completo
    re-evalúa TODO el universo desde cero, sin consultar la watchlist --
    si un ticker ya se disparó HOY vía el chequeo liviano
    (`revisar_watchlist`, cada ~5 min), `_actualizar_watchlist` lo salta
    (ya no está en WATCHING) pero `seleccionar_y_auditar` podía seguir
    eligiéndolo igual, mandando la misma alerta dos veces.

    Corrección 2026-08-11 (revisión de PR, quinta vuelta): no basta con
    excluir solo TRIGGERED -- un ticker que ya se resolvió HOY en
    CUALQUIER estado terminal (MISSED/INVALIDATED/EXPIRED, no solo
    TRIGGERED) también sale de WATCHING, así que `_actualizar_watchlist`
    tampoco vuelve a tocarlo -- pero `seleccionar_y_auditar` lo re-evalúa
    de cero cada corrida y podía seguir eligiéndolo igual. Sin esto era
    peor que el bug original: cada corrida de 30 minutos mandaba la
    MISMA alerta de nuevo, sin registrar ninguna transición (el ticker
    ya no está en WATCHING) -- ni siquiera quedaba rastro en el
    historial de auditoría.

    Excluye a los que ya se resolvieron HOY ANTES de esta corrida -- los
    recién disparados AHORA MISMO (en `disparadas_watchlist`) sí se
    mandan, son la primera vez.

    Devuelve `(oportunidades_nuevas, tickers_excluidos)` -- corrección
    2026-08-11 (revisión de PR, sexta vuelta): `main()` necesita el
    segundo valor para corregir el snapshot de auditoría de los
    excluidos (quedaban marcados DECISION_ALERTADA aunque nunca se
    mandó nada) y para que `elegidas` (lo que de verdad "se alertó esta
    corrida", el contrato documentado de `radar.construir_resumen`) no
    los incluya."""
    ahora = ahora or datetime.now(UTC)
    hoy_iso = ahora.date().isoformat()
    ya_resueltas_hoy = {
        e.ticker for e in entradas_watchlist
        if e.estado in watchlist.ESTADOS_TERMINALES and e.ticker not in disparadas_watchlist
        and e.actualizado_en[:10] == hoy_iso
    }
    for o in oportunidades:
        if o.ticker in ya_resueltas_hoy:
            log.info("%s ya se resolvió hoy en la watchlist -- se omite la alerta duplicada", o.ticker)
    return [o for o in oportunidades if o.ticker not in ya_resueltas_hoy], ya_resueltas_hoy


def _evaluar_no_disparada(e, c: CandidatoIntradia, cfg: MomentumConfig, ahora: datetime) -> None:
    """Traduce el resultado de una candidata que NO disparó esta vez a
    una transición de watchlist (o ninguna, si sigue en observación) --
    compartido entre `_actualizar_watchlist` y `revisar_watchlist`
    (corrección 2026-08-11, revisión de PR: antes esta lógica estaba
    duplicada en las dos funciones).

    El veredicto "tarde" es del instante, no permanente -- ver
    `EntradaWatchlist.tarde_consecutivas`: hace falta verlo
    `cfg.verificaciones_tarde_para_missed` veces SEGUIDAS antes de
    comprometerse a MISSED; cualquier otra lectura resetea el conteo.

    Corrección 2026-08-11 (revisión de PR, segunda vuelta): MISSED exige
    además `r.patron is not None` -- `early_opportunity.calcular` corre
    SIEMPRE, incluso sin patrón detectado, y `_nivel_para_patron` cae a
    EMA9/VWAP cuando no hay uno (ver su docstring), así que cualquier
    ticker simplemente cerrando arriba de su EMA9 por varios minutos leía
    "tarde" sin que jamás se hubiera formado nada que perseguir. MISSED
    documenta explícitamente "el patrón se formó pero ya no estamos a
    tiempo" (ver docstring del módulo `watchlist.py`) -- sin patrón real,
    la candidata sigue en WATCHING (nunca hay nada que "perder"), sujeta
    solo al TTL normal de `expirar_vencidas`."""
    r = c.resultado
    if r.patron is not None and not r.temprano and r.early is not None:
        e.tarde_consecutivas += 1
        if e.tarde_consecutivas >= cfg.verificaciones_tarde_para_missed:
            watchlist.marcar_missed(e, r.early.motivo_veredicto, ahora)
        return
    e.tarde_consecutivas = 0
    if not watchlist.catalizador_vigente(e, cfg.dias_ventana_catalizador, ahora):
        watchlist.marcar_invalidated(e, "El catalizador ya salió de la ventana de vigencia.", ahora)


def revisar_watchlist(
    cfg: MomentumConfig = CONFIG, provider: DataProvider | None = None, dry_run: bool = False,
    ahora: datetime | None = None,
) -> None:
    """Chequeo liviano (2026-08-11, "Fase 2") -- SOLO re-evalúa la
    watchlist activa, sin re-escanear el universo ni pedir barras
    diarias (ver `gap_pct_congelado` en `watchlist.py`). Pensado para
    correr cada ~5 minutos -- el mínimo real que garantiza GitHub
    Actions, no el "1 minuto ideal" pedido originalmente (ver README) --
    en un workflow SEPARADO del escaneo completo:
    `python -m momentum_hunter.run --solo-watchlist`.

    Reutiliza exactamente la misma competencia relativa + abogado del
    diablo + auditoría que ya usa el descubrimiento (`seleccionar_y_
    auditar`) -- si dos o más tickers vigilados confirman en el mismo
    ciclo, se aplica la misma regla de "solo la mejor" de siempre."""
    provider = provider or YahooProvider()
    ahora = ahora or datetime.now(UTC)
    entradas = watchlist.cargar()
    vigiladas = watchlist.activas(entradas)
    if not vigiladas:
        log.info("watchlist vacía -- nada que re-chequear")
        entradas = watchlist.purgar_antiguas(entradas, ahora)
        if not dry_run:
            watchlist.guardar(entradas)
        return

    tickers = [e.ticker for e in vigiladas]
    barras_intradia = provider.barras_intradia(tickers, cfg.intervalo_intradia, cfg.periodo_intradia)
    dato_recibido_ts = _ahora_iso_run(datetime.now(UTC))

    candidatos: list[CandidatoIntradia] = []
    por_ticker = {e.ticker: e for e in vigiladas}
    for e in vigiladas:
        bi = barras_intradia.get(e.ticker)
        if bi is None:
            # El proveedor falló para este ticker -- se reintenta en el
            # próximo ciclo, nunca se expira solo por esto (un fallo
            # transitorio no debe borrar una candidata real).
            continue
        try:
            candidato = _construir_candidato_intradia(
                e.ticker, e.nombre, watchlist.catalizador_de(e), watchlist.meta_de(e),
                e.es_large_cap, e.atr_diario, e.score_base, None, bi, cfg,
                gap_pct_fallback=e.gap_pct_congelado,
            )
            if candidato is not None:
                candidatos.append(candidato)
        except Exception as ex:
            log.warning("re-chequeo de %s falló: %s", e.ticker, ex)

    if not candidatos:
        entradas = watchlist.purgar_antiguas(entradas, ahora)
        watchlist.expirar_vencidas(entradas, cfg.minutos_maximos_en_watching, ahora)
        if not dry_run:
            watchlist.guardar(entradas)
        return

    oportunidades, vetadas, snapshots = seleccionar_y_auditar(candidatos, cfg, n_universo=0)
    elegidos = {o.ticker: o for o in oportunidades}

    for candidato in candidatos:
        e = por_ticker[candidato.ticker]
        if candidato.ticker in elegidos:
            oportunidad = elegidos[candidato.ticker]
            evaluador_ts = _ahora_iso_run(datetime.now(UTC))
            market_ts = (
                candidato.bi_hoy.timestamps[-1] if candidato.bi_hoy.timestamps else evaluador_ts
            )
            watchlist.marcar_triggered(e, market_ts, dato_recibido_ts, evaluador_ts, ahora)
            texto = report.formatear_entrada(oportunidad)
            mensaje_ts = _ahora_iso_run(datetime.now(UTC))
            print("\n" + report.formatear(oportunidad))
            if not dry_run:
                enviar_telegram(texto)
            telegram_ts = _ahora_iso_run(datetime.now(UTC))
            watchlist.registrar_latencia(e, mensaje_ts, telegram_ts)
            log.info("TRIGGERED %s -- latencia de la señal: %s ms", e.ticker, e.signal_latency_ms)
            if not dry_run:
                tracker.registrar([oportunidad])
        else:
            _evaluar_no_disparada(e, candidato, cfg, ahora)

    entradas = watchlist.purgar_antiguas(entradas, ahora)
    watchlist.expirar_vencidas(entradas, cfg.minutos_maximos_en_watching, ahora)
    log.info("watchlist: %d en observación tras el re-chequeo", len(watchlist.activas(entradas)))
    if not dry_run:
        audit.registrar_corrida(snapshots)
        watchlist.guardar(entradas)


def _revisar_resumen_cierre(dry_run: bool, ya_avisado_radar: bool = False) -> None:
    """Bug encontrado el 2026-07-27 corriendo el bot en vivo: el mensaje
    de "hoy no hubo nada" (heartbeat.py, PR #72) vivía solo al final de
    `main()`, después de dos `return` tempranos -- exactamente los casos
    más comunes de un día normal (ningún ticker pasa los filtros de
    universo, o ninguno tiene catalizador confirmado). El heartbeat
    nunca llegaba a evaluarse en el escenario para el que se pidió. Se
    llama en cada punto de salida de `main()`, no solo al final.

    `ya_avisado_radar` (2026-07-27, mismo día): si esta misma corrida ya
    mandó un Market Radar, ese mensaje ya cubrió "el bot corrió y no
    encontró nada" -- mandar el genérico de heartbeat.py encima sería
    redundante, dos mensajes seguidos diciendo lo mismo. Igual se
    registra como enviado para no repetir el genérico más tarde hoy."""
    if dry_run:
        return
    todas = tracker.cargar()
    ahora = datetime.now(UTC)
    hora_actual = ahora.hour + ahora.minute / 60.0
    alertas_hoy = sum(1 for a in todas if a.fecha[:10] == ahora.date().isoformat())
    estado = heartbeat.cargar_estado()
    if heartbeat.necesita_resumen_cierre(ahora.date(), hora_actual, alertas_hoy, estado):
        if ya_avisado_radar:
            log.info("resumen de cierre: ya se mandó Market Radar esta corrida, no repito el genérico")
        else:
            enviar_telegram(heartbeat.MENSAJE_SIN_ALERTAS)
            log.info("resumen de cierre enviado: hoy no hubo alertas")
        heartbeat.registrar_enviado(ahora.date())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="escanear solo los primeros N tickers del universo (pruebas/CI)")
    ap.add_argument("--universo", type=str, default=None,
                    help="archivo propio con un ticker por línea, en vez del mercado completo")
    ap.add_argument("--refrescar-universo", action="store_true",
                    help="forzar descarga fresca del universo NYSE/NASDAQ/AMEX")
    ap.add_argument("--no-catalizadores", action="store_true",
                    help="omitir noticias -- SIN catalizador nunca se manda una alerta real")
    ap.add_argument("--dry-run", action="store_true",
                    help="calcula y muestra, no manda a Telegram ni registra en el tracker")
    ap.add_argument("--actualizar-resultados", action="store_true",
                    help="no escanea: actualiza resultados de alertas pendientes y muestra stats")
    ap.add_argument("--solo-watchlist", action="store_true",
                    help="no escanea el universo -- solo re-chequea la watchlist activa "
                         "(pensado para un workflow separado, cada ~5 minutos, ver watchlist.py)")
    args = ap.parse_args()

    if args.actualizar_resultados:
        _modo_actualizar_resultados(CONFIG)
        return

    if args.solo_watchlist:
        revisar_watchlist(CONFIG, YahooProvider(), args.dry_run)
        return

    tickers = _cargar_tickers(args)
    log.info("universo candidato: %d tickers", len(tickers))

    provider = YahooProvider()
    barras = provider.barras(tickers, dias=280)
    bandas = {t: banda for t, b in barras.items() if (banda := _banda_de_universo(b, CONFIG)) is not None}
    validos = list(bandas)
    log.info("etapa 1 -- tras filtros de precio/liquidez: %d/%d (%d large-cap)",
              len(validos), len(barras), sum(1 for banda in bandas.values() if banda == "large"))
    if not validos:
        log.warning("ningún ticker pasó los filtros de universo -- no hay nada que evaluar hoy")
        _revisar_resumen_cierre(args.dry_run)
        return

    candidatos_diarios = construir_candidatos_diarios(
        validos, barras, provider, CONFIG, not args.no_catalizadores, bandas)
    shortlist = candidatos_para_etapa_intradia(candidatos_diarios, CONFIG)
    log.info("etapa 1 -- candidatos con catalizador confirmado: %d -- pasan a intradía: %d",
              sum(1 for c in candidatos_diarios if c.catalizador is not None), len(shortlist))
    if not shortlist:
        log.info("ningún candidato con catalizador confirmado hoy -- silencio (ver alerts.py)")
        _revisar_resumen_cierre(args.dry_run)
        return

    # Reloj real capturado justo DESPUÉS de que llegan las velas intradía
    # -- lo necesita `_actualizar_watchlist` para `data_received_ts` (ver
    # su docstring: antes de este fix ese campo se rellenaba con el
    # mismo reloj que `evaluador_ts`, como si pedir los datos tardara
    # cero). Corrección 2026-08-11 (revisión de PR, sexta vuelta): un
    # reloj tomado ANTES del pedido tampoco sirve -- el pedido secuencial
    # a Yahoo para hasta `max_candidatos_intradia` tickers puede tardar
    # minutos, y ese tiempo quedaba mal atribuido. `on_datos_recibidos`
    # captura el instante real en que `provider.barras_intradia` ya
    # devolvió, dentro de `construir_candidatos_intradia`.
    _reloj_dato_recibido: dict[str, str] = {}
    candidatos_intradia = construir_candidatos_intradia(
        shortlist, barras, provider, CONFIG,
        on_datos_recibidos=lambda: _reloj_dato_recibido.setdefault(
            "ts", _ahora_iso_run(datetime.now(UTC))),
    )
    dato_recibido_ts = _reloj_dato_recibido.get("ts") or _ahora_iso_run(datetime.now(UTC))
    log.info("etapa 2 -- candidatos evaluados: %d", len(candidatos_intradia))

    oportunidades, vetadas, snapshots = seleccionar_y_auditar(
        candidatos_intradia, CONFIG, n_universo=len(tickers))

    entradas_watchlist, disparadas_watchlist = _actualizar_watchlist(
        shortlist, candidatos_intradia, {o.ticker for o in oportunidades}, CONFIG, args.dry_run,
        dato_recibido_ts=dato_recibido_ts)

    oportunidades_nuevas, ya_resueltas_hoy = _filtrar_ya_resueltas_hoy(
        oportunidades, entradas_watchlist, disparadas_watchlist)
    for snap in snapshots:
        if snap["ticker"] in ya_resueltas_hoy:
            snap["decision"] = audit.DECISION_DUPLICADA_MISMO_DIA
            snap["motivos"] = ["Ya se resolvió hoy en la watchlist -- se omitió como alerta duplicada."]
            snap["que_tendria_que_cambiar"] = []

    for o in oportunidades_nuevas:
        # "Fase 1" del detector de entradas (2026-08-10): a Telegram va
        # el mensaje corto (`formatear_entrada`, 5-10 segundos de
        # lectura); la narrativa larga (`formatear`) se sigue imprimiendo
        # en el log de la corrida -- nada se pierde, solo deja de ser lo
        # que llega al chat.
        print("\n" + report.formatear(o))
        mensaje_ts = _ahora_iso_run(datetime.now(UTC))
        if not args.dry_run:
            enviar_telegram(report.formatear_entrada(o))
        # Latencia real (corrección 2026-08-11, ver docstring de
        # `_actualizar_watchlist`): se completa acá, con relojes tomados
        # alrededor del envío de VERDAD, no con el reloj de antes de
        # armar/mandar el mensaje.
        telegram_ts = _ahora_iso_run(datetime.now(UTC))
        entrada_disparada = disparadas_watchlist.get(o.ticker)
        if entrada_disparada is not None:
            watchlist.registrar_latencia(entrada_disparada, mensaje_ts, telegram_ts)

    if not args.dry_run and disparadas_watchlist:
        watchlist.guardar(entradas_watchlist)

    if not args.dry_run and oportunidades_nuevas:
        tracker.registrar(oportunidades_nuevas)
        log.info("registradas %d alerta(s) en el tracker", len(oportunidades_nuevas))
    elif not oportunidades:
        log.info("ninguna oportunidad sobrevivió todos los filtros hoy -- silencio, no es "
                 "un error (Principio 1: la mejor operación muchas veces es no operar)")

    if not args.dry_run:
        ruta = audit.registrar_corrida(snapshots)
        if ruta:
            log.info("auditoría de la corrida escrita en %s", ruta)

    elegidas = {o.ticker for o in oportunidades_nuevas}
    resumen_radar = radar.construir_resumen(candidatos_intradia, elegidas, vetadas, CONFIG)
    if resumen_radar:
        print("\n" + resumen_radar)
        if not args.dry_run:
            enviar_telegram(resumen_radar)

    # Punto 8 ("Head Trader"): el trabajo no termina al mandar Telegram.
    # Cada corrida también vigila las alertas de HOY todavía abiertas y
    # avisa SOLO cuando su estado cambia (rompió stop, alcanzó objetivo,
    # debilitándose...). En dry-run se omite: vigilar muta el tracker.
    if not args.dry_run:
        todas = tracker.cargar()
        avisos = vigilancia.vigilar(todas, provider, CONFIG)
        tracker.guardar(todas)
        for aviso in avisos:
            print("\n" + aviso)
            enviar_telegram(aviso)
        if avisos:
            log.info("vigilancia: %d cambio(s) de estado avisado(s)", len(avisos))

    _revisar_resumen_cierre(args.dry_run, ya_avisado_radar=bool(resumen_radar))


if __name__ == "__main__":
    main()
