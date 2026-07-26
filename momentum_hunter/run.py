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
from dataclasses import replace
from datetime import UTC, datetime

import requests

from momentum_hunter import classification, evaluator, outcomes, radar, report, stats, tracker, universe
from momentum_hunter.alerts import (
    CandidatoDiario,
    CandidatoIntradia,
    candidatos_para_etapa_intradia,
    filtrar_alertas,
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


def _pasa_filtros_de_universo(b: Barras, cfg: MomentumConfig) -> bool:
    if not b.close or b.close[-1] <= 0:
        return False
    precio = b.close[-1]
    vol_prom = _volumen_promedio(b)
    return (
        cfg.precio_min <= precio <= cfg.precio_max
        and vol_prom is not None and vol_prom >= cfg.volumen_promedio_min
    )


def construir_candidatos_diarios(
    tickers_validos: list[str], barras: dict[str, Barras], provider: DataProvider,
    cfg: MomentumConfig, con_catalizadores: bool,
) -> list[CandidatoDiario]:
    """Etapa 1 -- núcleo puro y testeable: recibe todo ya inyectado
    (barras, metadata, catalizadores), nunca llama red directamente. Un
    ticker que falle no debe tumbar la corrida completa."""
    metadata = provider.metadata(tickers_validos)
    noticias = YahooNewsProvider() if con_catalizadores else None

    candidatos: list[CandidatoDiario] = []
    for t in tickers_validos:
        try:
            b = barras[t]
            meta = metadata.get(t)
            if meta is None:
                continue
            if meta.es_etf or (cfg.excluir_spac and meta.es_spac) or (cfg.excluir_cef and meta.es_cef):
                continue
            if meta.market_cap is not None and meta.market_cap > cfg.market_cap_max:
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


def construir_candidatos_intradia(
    shortlist: list[CandidatoDiario], barras_diarias: dict[str, Barras],
    provider: DataProvider, cfg: MomentumConfig,
) -> list[CandidatoIntradia]:
    """Etapa 2 -- SOLO sobre `shortlist` (ya recortada por
    `alerts.candidatos_para_etapa_intradia`). Un ticker que falle no
    tumba la corrida completa."""
    tickers = [c.ticker for c in shortlist]
    barras_intradia = provider.barras_intradia(tickers, cfg.intervalo_intradia, cfg.periodo_intradia)

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
            factores = fi.calcular(bi, cierre_ant)

            patron_preliminar = classification.detectar_patron(hoy, factores)
            nivel_ruptura = _nivel_para_patron(patron_preliminar, factores)
            if nivel_ruptura is not None:
                velas = fi.velas_desde_ruptura(hoy, nivel_ruptura)
                factores = replace(factores, velas_desde_ruptura=velas)

            minutos = minutos_desde_catalizador(c.catalizador)
            niveles = report.niveles_entrada_salida(factores, c.factores.atr)
            entrada = factores.precio_actual if factores.precio_actual is not None else c.precio

            resultado_eval = evaluator.evaluar(
                c.catalizador, minutos, factores, hoy, c.meta,
                entrada, niveles["stop"], niveles["objetivo"], c.puntuacion.score_total, cfg,
            )
            resultado.append(CandidatoIntradia(
                ticker=c.ticker, nombre=c.nombre, catalizador=c.catalizador,
                minutos_desde_catalizador=minutos, factores=factores, bi_hoy=hoy,
                meta=c.meta, atr_diario=c.factores.atr, resultado=resultado_eval,
            ))
        except Exception as e:
            log.warning("candidato intradía %s falló: %s", c.ticker, e)
    return resultado


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
    tracker.guardar(alertas)
    for h in cfg.horizontes_seguimiento:
        e = stats.calcular_estadisticas(alertas, h)
        log.info(
            "horizonte %dd: n=%d win_rate=%s retorno_prom=%s drawdown=%s expectancy=%s sharpe=%s",
            h, e.n, e.win_rate, e.retorno_promedio, e.drawdown_maximo, e.expectancy, e.sharpe,
        )


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
    args = ap.parse_args()

    if args.actualizar_resultados:
        _modo_actualizar_resultados(CONFIG)
        return

    tickers = _cargar_tickers(args)
    log.info("universo candidato: %d tickers", len(tickers))

    provider = YahooProvider()
    barras = provider.barras(tickers, dias=280)
    validos = [t for t, b in barras.items() if _pasa_filtros_de_universo(b, CONFIG)]
    log.info("etapa 1 -- tras filtros de precio/liquidez: %d/%d", len(validos), len(barras))
    if not validos:
        log.warning("ningún ticker pasó los filtros de universo -- no hay nada que evaluar hoy")
        return

    candidatos_diarios = construir_candidatos_diarios(validos, barras, provider, CONFIG, not args.no_catalizadores)
    shortlist = candidatos_para_etapa_intradia(candidatos_diarios, CONFIG)
    log.info("etapa 1 -- candidatos con catalizador confirmado: %d -- pasan a intradía: %d",
              sum(1 for c in candidatos_diarios if c.catalizador is not None), len(shortlist))
    if not shortlist:
        log.info("ningún candidato con catalizador confirmado hoy -- silencio (ver alerts.py)")
        return

    candidatos_intradia = construir_candidatos_intradia(shortlist, barras, provider, CONFIG)
    log.info("etapa 2 -- candidatos evaluados: %d", len(candidatos_intradia))

    accionables = filtrar_alertas(candidatos_intradia, CONFIG)
    oportunidades = [report.construir_oportunidad(c, CONFIG.velas_maximas_desde_patron) for c in accionables]
    for o in oportunidades:
        texto = report.formatear(o)
        print("\n" + texto)
        if not args.dry_run:
            enviar_telegram(texto)

    if not args.dry_run and oportunidades:
        tracker.registrar(oportunidades)
        log.info("registradas %d alerta(s) en el tracker", len(oportunidades))
    elif not oportunidades:
        log.info("ninguna oportunidad accionable hoy -- silencio, no es un error (ver evaluator.py)")

    resumen_radar = radar.construir_resumen(candidatos_intradia)
    if resumen_radar:
        print("\n" + resumen_radar)
        if not args.dry_run:
            enviar_telegram(resumen_radar)


if __name__ == "__main__":
    main()
