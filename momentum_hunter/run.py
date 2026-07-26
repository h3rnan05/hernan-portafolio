"""Orchestrator del Momentum Opportunity Hunter -- inyección de
dependencias de principio a fin (universo → provider de precios →
factores/catalizadores/score → filtro de alertas → reporte → Telegram →
tracker), mismo patrón que `screener/run.py`, implementación
independiente.

USO
  python -m momentum_hunter.run                      # universo completo NYSE+NASDAQ+AMEX
  python -m momentum_hunter.run --limit 500           # subconjunto (recomendado en CI, ver
                                                       # la limitación de rendimiento en universe.py)
  python -m momentum_hunter.run --universo watchlist.txt  # archivo propio, un ticker por línea
  python -m momentum_hunter.run --no-catalizadores    # sin noticias (solo para pruebas rápidas
                                                       # de los factores técnicos -- SIN
                                                       # catalizador nunca se manda una alerta real)
  python -m momentum_hunter.run --dry-run             # calcula y muestra, no manda a Telegram
                                                       # ni registra en el tracker
  python -m momentum_hunter.run --actualizar-resultados  # NO escanea: actualiza resultados de
                                                       # alertas pendientes y muestra estadísticas

VARIABLES DE ENTORNO
  MOMENTUM_TELEGRAM_BOT_TOKEN / MOMENTUM_TELEGRAM_CHAT_ID
      Chat dedicado a este bot (recomendado, para no mezclar sus alertas
      con las del Investment Analyst). Si no están, cae a
      TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (el mismo chat del otro bot)
      -- nunca falla por falta de secrets, solo deja de enviar y sigue
      registrando en el tracker.
"""

from __future__ import annotations

import argparse
import logging
import os

import requests

from momentum_hunter import outcomes, report, stats, tracker, universe
from momentum_hunter.alerts import Candidato, filtrar_alertas
from momentum_hunter.catalysts.detector import YahooNewsProvider, detectar_catalizador
from momentum_hunter.config import CONFIG, MomentumConfig
from momentum_hunter.data.provider import DataProvider, YahooProvider
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


def construir_candidatos(
    tickers_validos: list[str], barras: dict[str, Barras], provider: DataProvider,
    cfg: MomentumConfig, con_catalizadores: bool,
) -> list[Candidato]:
    """Núcleo puro y testeable: recibe todo ya inyectado (barras,
    metadata, catalizadores), nunca llama red directamente -- eso lo hace
    `main()`. Un ticker que falle no debe tumbar la corrida completa."""
    metadata = provider.metadata(tickers_validos)
    noticias = YahooNewsProvider() if con_catalizadores else None

    candidatos: list[Candidato] = []
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
            candidatos.append(Candidato(
                ticker=t, nombre=meta.nombre, precio=b.close[-1], volumen_promedio=vol_prom,
                factores=factores, catalizador=catalizador, meta=meta, puntuacion=puntuacion,
            ))
        except Exception as e:
            log.warning("candidato %s falló: %s", t, e)
    return candidatos


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
    log.info("tras filtros de precio/liquidez: %d/%d", len(validos), len(barras))
    if not validos:
        log.warning("ningún ticker pasó los filtros de universo -- no hay nada que evaluar hoy")
        return

    candidatos = construir_candidatos(validos, barras, provider, CONFIG, not args.no_catalizadores)
    calificados = filtrar_alertas(candidatos, CONFIG)
    log.info("candidatos evaluados: %d -- califican para alerta: %d", len(candidatos), len(calificados))

    oportunidades = [report.construir_oportunidad(c, CONFIG) for c in calificados]
    for o in oportunidades:
        texto = report.formatear(o)
        print("\n" + texto)
        if not args.dry_run:
            enviar_telegram(texto)

    if not args.dry_run and oportunidades:
        tracker.registrar(oportunidades)
        log.info("registradas %d alerta(s) en el tracker", len(oportunidades))
    elif not oportunidades:
        log.info("ninguna oportunidad calificó hoy -- silencio, no es un error (ver alerts.py)")


if __name__ == "__main__":
    main()
