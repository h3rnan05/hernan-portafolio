"""Genera el memo de /report TICKER bajo demanda.

Esta es la investigación profunda que el mensaje diario del screener YA NO
manda automáticamente (ver screener/report.texto_telegram_corto): se pide
explícitamente, para un ticker a la vez. Reutiliza los mismos módulos
reales que ya existen (screener, news_analyst) -- nunca corre sobre el
universo completo, así que es rápido y barato de correr on-demand.

Honestidad de los datos: solo se muestra lo que de verdad se pudo obtener.
yfinance da un snapshot real de consenso de analistas y % de tenencia
institucional/insider (no el detalle de transacciones ni el histórico
13F -- eso no está disponible gratis, así que no se inventa). Ver
telegram_bot/README.md para el detalle de qué es real y qué quedó fuera.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from news_analyst.explicador import generar_explicacion  # noqa: E402
from news_analyst.models import EntradaShortlist  # noqa: E402
from screener.data.provider import Barras, Fundamentales, YahooProvider  # noqa: E402
from screener.factors import technical as tech  # noqa: E402
from screener.report import razones  # noqa: E402
from screener.scoring import Puntuacion  # noqa: E402
from wizards_bot import titulares_google_news  # noqa: E402

log = logging.getLogger("telegram_bot.report_command")

SHORTLIST_PATH = _REPO_ROOT / "screener" / "shortlist_hoy.json"
MAX_TITULARES_REPORTE = 5


def _fmt_pct(x: float | None) -> str:
    return f"{x:.1%}" if x is not None else "No disponible"


def _shortlist_entry(ticker: str) -> dict | None:
    """La fila de hoy para este ticker (con su posición), o None si no
    pasó el screener hoy -- eso NO significa que el ticker sea malo, solo
    que no está en la shortlist de hoy."""
    if not SHORTLIST_PATH.exists():
        return None
    try:
        data = json.loads(SHORTLIST_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.debug("no pude leer shortlist_hoy.json: %s", e)
        return None
    for i, fila in enumerate(data.get("shortlist", [])):
        if fila["ticker"] == ticker:
            fila = dict(fila)
            fila["posicion"] = i + 1
            fila["universo_n"] = data.get("universo_escaneado")
            return fila
    return None


def _seccion_shortlist(entrada: dict | None) -> list[str]:
    if not entrada:
        return [
            "No está en la shortlist de hoy (no pasó todos los filtros "
            "cuantitativos del screener) -- lo de abajo es informativo, "
            "sin score comparativo contra el resto del mercado.",
        ]
    p = Puntuacion(ticker=entrada["ticker"], score_total=entrada["score"],
                   sub=entrada.get("sub_scores", {}), sector=entrada.get("sector"))
    lineas = [
        f"Posición #{entrada['posicion']} de la shortlist de hoy "
        f"({entrada['score']:.0f}/100, sobre {entrada.get('universo_n', '?')} "
        f"empresas analizadas).",
    ]
    r = razones(p, 3)
    if r:
        lineas.append("¿Por qué pasó el screener?")
        lineas += [f"  • {frase}" for frase in r]
    return lineas


def _seccion_tecnico(barras: Barras) -> list[str]:
    tendencia = tech.score_tendencia(barras)
    vol = tech.volatilidad_anual(barras)
    prox = tech.proximidad_maximo_52s(barras)
    rsi = tech.rsi(barras)
    lineas = ["Técnico:", f"  Precio actual: ${barras.close[-1]:.2f}"]
    lineas.append(f"  Tendencia (medias móviles): {tendencia:.0f}/3 condiciones alcistas"
                  if tendencia is not None else "  Tendencia: No disponible (poca historia)")
    lineas.append(f"  Volatilidad histórica anual: {_fmt_pct(vol)}")
    lineas.append(f"  A qué % del máximo de 52 semanas: {prox:.1%}" if prox is not None
                  else "  Máximo de 52 semanas: No disponible")
    lineas.append(f"  RSI (14): {rsi:.0f}" if rsi is not None else "  RSI: No disponible")
    return lineas


def _seccion_fundamentales(f: Fundamentales) -> list[str]:
    return [
        "Fundamentales:",
        f"  P/E: {f.pe:.1f}" if f.pe else "  P/E: No disponible",
        f"  P/B: {f.pb:.1f}" if f.pb else "  P/B: No disponible",
        f"  ROE: {_fmt_pct(f.roe)}",
        f"  Margen operativo: {_fmt_pct(f.margen_operativo)}",
        f"  Crecimiento de ingresos (interanual): {_fmt_pct(f.crecimiento_ingresos)}",
    ]


def _seccion_analistas_y_tenencia(f: Fundamentales) -> list[str]:
    lineas = [
        "Consenso de analistas y tenencia (snapshot de hoy, no histórico):",
        f"  Recomendación: {f.analista_recomendacion or 'No disponible'}",
    ]
    if f.analista_precio_objetivo:
        lineas.append(f"  Precio objetivo promedio: ${f.analista_precio_objetivo:.2f} "
                      f"({f.analista_num_opiniones or '?'} analistas)")
    else:
        lineas.append("  Precio objetivo: No disponible")
    lineas.append(f"  % en manos institucionales: {_fmt_pct(f.pct_institucional)}")
    lineas.append(f"  % en manos de insiders: {_fmt_pct(f.pct_insiders)}")
    return lineas


def _seccion_noticias(ticker: str, nombre: str | None, entrada: dict | None) -> list[str]:
    query = f"{nombre or ticker} stock"
    titulares = titulares_google_news(query, maximo=MAX_TITULARES_REPORTE)
    if not titulares:
        return ["Noticias: no se encontraron titulares recientes."]

    lineas = ["Noticias recientes:"]
    if not entrada:
        # Sin contexto de shortlist no hay con qué fundamentar una
        # explicación honesta del LLM -- se listan los titulares crudos
        # en vez de forzar una conexión que no existe.
        lineas += [f'  📰 "{t}"' for t in titulares]
        return lineas

    contexto = EntradaShortlist(
        ticker=ticker, posicion=entrada["posicion"], score=entrada["score"],
        sector=entrada.get("sector"), nombre=nombre, industria=entrada.get("industria"),
        sub_scores=entrada.get("sub_scores", {}),
    )
    for titular in titulares:
        explicacion = generar_explicacion(titular, contexto)
        lineas.append(f'  📰 "{titular}"')
        if explicacion:
            lineas.append(f"     {explicacion.texto}")
        lineas.append("")
    return lineas


DISCLAIMER = (
    "Esto NO es una recomendación de compra/venta. Es un resumen "
    "informativo para que investigues más a fondo."
)


def generar_reporte(ticker: str) -> str:
    """Punto de entrada de /report TICKER. Nunca lanza: cualquier fallo se
    convierte en un mensaje de error legible para el usuario."""
    ticker = ticker.upper().strip()
    provider = YahooProvider()
    barras = provider.barras([ticker], dias=400)
    if ticker not in barras:
        return (f"No pude obtener datos de precio para {ticker} -- revisa que "
                f"el ticker esté bien escrito, o Yahoo bloqueó la consulta. "
                f"Intenta de nuevo en un momento.")

    fund = provider.fundamentales([ticker]).get(ticker, Fundamentales(ticker))
    entrada = _shortlist_entry(ticker)
    nombre = fund.nombre or ((entrada or {}).get("nombre"))

    lineas = [f"📊 Reporte: {nombre or ticker} ({ticker})", ""]
    lineas += _seccion_shortlist(entrada)
    lineas.append("")
    lineas += _seccion_tecnico(barras[ticker])
    lineas.append("")
    lineas += _seccion_fundamentales(fund)
    lineas.append("")
    lineas += _seccion_analistas_y_tenencia(fund)
    lineas.append("")
    lineas += _seccion_noticias(ticker, nombre, entrada)
    lineas.append("")
    lineas.append(DISCLAIMER)
    return "\n".join(lineas)
