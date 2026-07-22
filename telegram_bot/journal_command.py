"""Comandos /journal open|close|list|stats -- registra automáticamente
cada operación de paper trading con los mismos números que /options ya
calculó (score del screener, tesis, estrategia, costo, riesgo máximo,
ganancia máxima, probabilidad, valor esperado) más el motivo que dé el
usuario. Al cerrar, el USUARIO reporta el resultado real -- este sistema
no tiene acceso a la cuenta de paper trading, así que nunca infiere un
resultado que no se le dio (sería inventar un dato).

Persistencia: igual que wizards_inbox.json/wizards_state.json, el
journal se guarda como un archivo en el repo (journal/journal.json) leído
y escrito vía la API de contenidos de GitHub -- Render no tiene disco
persistente en este servicio.

Ningún LLM interviene en ningún punto de este módulo: construir la
estrategia, calcular el costo, y las estadísticas (win rate, expectancy,
drawdown) son puro cálculo determinístico sobre datos ya reales.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402

from journal.models import EntradaJournal  # noqa: E402
from journal.stats import EstadisticasJournal, calcular_estadisticas, calcular_estadisticas_por_estrategia  # noqa: E402
from journal.store import abrir_entrada, cerrar_entrada, encontrar_abierta_por_ticker  # noqa: E402
from screener.data.provider import YahooProvider  # noqa: E402
from screener.factors import technical as tech  # noqa: E402
from screener.options_ideas import clasificar_tendencia, obtener_cadena  # noqa: E402
from screener.options_strategies import construir_estrategias, costo_apertura  # noqa: E402

log = logging.getLogger("telegram_bot.journal_command")

GITHUB_REPO = os.getenv("GITHUB_REPO", "h3rnan05/hernan-portafolio")
JOURNAL_PATH = "journal/journal.json"
SHORTLIST_PATH = _REPO_ROOT / "screener" / "shortlist_hoy.json"

NOMBRES_ESTRATEGIA = [
    "Long Call", "Long Put", "Bull Call Spread", "Bear Put Spread", "Bull Put Spread",
    "Bear Call Spread", "Covered Call", "Cash Secured Put", "Iron Condor",
]


def _env(nombre: str) -> str:
    return os.getenv(nombre, "").strip()


def _github_headers() -> dict:
    return {"Authorization": f"Bearer {_env('GITHUB_BOT_TOKEN')}"}


def _leer_journal() -> tuple[list[EntradaJournal], str | None]:
    """Lee journal/journal.json de main vía la API de GitHub. Archivo
    inexistente (primera vez) devuelve una lista vacía, no un error."""
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{JOURNAL_PATH}",
        headers=_github_headers(), timeout=15,
    )
    if r.status_code == 404:
        return [], None
    r.raise_for_status()
    body = r.json()
    contenido = json.loads(base64.b64decode(body["content"]).decode())
    entradas = [EntradaJournal.from_dict(d) for d in contenido.get("entradas", [])]
    return entradas, body["sha"]


def _escribir_journal(entradas: list[EntradaJournal], sha: str | None) -> None:
    payload = {
        "message": "journal: actualiza registro de paper trading [skip ci]",
        "content": base64.b64encode(
            json.dumps({"entradas": [e.to_dict() for e in entradas]}, indent=2, ensure_ascii=False).encode()
        ).decode(),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{JOURNAL_PATH}",
        headers=_github_headers(), json=payload, timeout=30,
    )
    r.raise_for_status()


def _shortlist_entry(ticker: str) -> dict | None:
    """La fila de hoy para este ticker (con su posición), o None si no
    pasó el screener hoy -- eso NO significa que el ticker sea malo, solo
    que no está en la shortlist de hoy (mismo criterio que report_command)."""
    if not SHORTLIST_PATH.exists():
        return None
    try:
        data = json.loads(SHORTLIST_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    for i, fila in enumerate(data.get("shortlist", [])):
        if fila["ticker"] == ticker:
            fila = dict(fila)
            fila["posicion"] = i + 1
            return fila
    return None


def _tesis(tendencia_label: str) -> str:
    return {"alcista": "Alcista", "bajista": "Bajista", "neutral": "Neutral"}.get(
        tendencia_label, "No determinable")


def _parsear_estrategia_y_motivo(texto: str) -> tuple[str | None, str]:
    """Los 9 nombres de estrategia son un vocabulario fijo y conocido, así
    que se hace match del más largo que sea prefijo del texto (para que
    'Bull Call Spread' no se confunda con nada más corto) -- todo lo que
    sigue es el motivo en texto libre."""
    texto_lower = texto.lower()
    for nombre in sorted(NOMBRES_ESTRATEGIA, key=len, reverse=True):
        if texto_lower.startswith(nombre.lower()):
            return nombre, texto[len(nombre):].strip()
    return None, texto


def abrir_operacion(ticker: str, texto_resto: str) -> str:
    """Punto de entrada de /journal open TICKER ESTRATEGIA [motivo].
    Nunca lanza: cualquier fallo se convierte en un mensaje legible."""
    ticker = ticker.upper().strip()
    nombre_estrategia, motivo_usuario = _parsear_estrategia_y_motivo(texto_resto)
    if nombre_estrategia is None:
        return ("No reconocí la estrategia. Usa uno de estos nombres exactos: "
                + ", ".join(NOMBRES_ESTRATEGIA))

    provider = YahooProvider()
    barras_por_ticker = provider.barras([ticker], dias=400)
    if ticker not in barras_por_ticker:
        return (f"No pude obtener datos de precio para {ticker} -- revisa que "
                f"el ticker esté bien escrito, o Yahoo bloqueó la consulta.")
    barras = barras_por_ticker[ticker]
    spot = barras.close[-1]
    tesis = _tesis(clasificar_tendencia(tech.score_tendencia(barras)))

    cadena = obtener_cadena(ticker)
    if cadena is None:
        return f"No pude obtener la cadena de opciones de {ticker} -- intenta de nuevo en un momento."
    estrategias = construir_estrategias(cadena, spot)
    coincidencia = next((e for e in estrategias if e.nombre == nombre_estrategia), None)
    if coincidencia is None:
        disponibles = ", ".join(e.nombre for e in estrategias) or "ninguna (cadena insuficiente hoy)"
        return (f"No pude construir '{nombre_estrategia}' para {ticker} con los datos de hoy. "
                f"Disponibles ahora mismo: {disponibles}")

    entrada_shortlist = _shortlist_entry(ticker)
    score = entrada_shortlist["score"] if entrada_shortlist else None
    posicion = entrada_shortlist.get("posicion") if entrada_shortlist else None
    costo = costo_apertura(coincidencia)
    motivo = motivo_usuario or coincidencia.razon

    entrada = abrir_entrada(
        ticker=ticker, score_screener=score, posicion_shortlist=posicion, tesis=tesis,
        estrategia=coincidencia.nombre,
        patas=[{"tipo": p.tipo, "accion": p.accion, "strike": p.strike, "prima": p.prima}
               for p in coincidencia.patas],
        costo=costo, riesgo_maximo=coincidencia.riesgo_maximo, ganancia_maxima=coincidencia.ganancia_maxima,
        probabilidad_exito=coincidencia.probabilidad_exito, valor_esperado=coincidencia.valor_esperado,
        motivo=motivo,
    )

    entradas, sha = _leer_journal()
    entradas.append(entrada)
    _escribir_journal(entradas, sha)

    ganancia_txt = (f"${coincidencia.ganancia_maxima:,.2f}"
                    if coincidencia.ganancia_maxima is not None else "Ilimitada")
    score_txt = f"{score:.0f}/100" if score is not None else "No disponible (no está en la shortlist de hoy)"
    return (
        f"📝 Registrado en el journal (id {entrada.id})\n\n"
        f"{ticker} -- {coincidencia.nombre}\n"
        f"Tesis: {tesis} | Score del screener: {score_txt}\n"
        f"Costo: ${costo:,.2f} | Riesgo máx: ${coincidencia.riesgo_maximo:,.2f} | Ganancia máx: {ganancia_txt}\n"
        f"Motivo: {motivo}\n\n"
        f"Para cerrarla: /journal close {ticker} RESULTADO (ej. /journal close {ticker} +150)"
    )


def cerrar_operacion(ticker: str, resultado: float, notas: str | None) -> str:
    """Punto de entrada de /journal close TICKER RESULTADO [notas]."""
    ticker = ticker.upper().strip()
    entradas, sha = _leer_journal()
    abierta = encontrar_abierta_por_ticker(entradas, ticker)
    if abierta is None:
        return f"No encontré ninguna operación abierta de {ticker} en el journal."
    cerrada = cerrar_entrada(entradas, abierta.id, resultado, notas)
    _escribir_journal(entradas, sha)
    pct_txt = f"{cerrada.resultado_pct:+.1%} del riesgo máximo" if cerrada.resultado_pct is not None else "No disponible"
    lineas = [
        f"✅ Cerrada: {ticker} -- {cerrada.estrategia}",
        f"Resultado: ${resultado:+,.2f} ({pct_txt})",
    ]
    if notas:
        lineas.append(f"Notas: {notas}")
    return "\n".join(lineas)


def listar_abiertas() -> str:
    """Punto de entrada de /journal list."""
    entradas, _ = _leer_journal()
    abiertas = [e for e in entradas if e.estado == "abierta"]
    if not abiertas:
        return "No tienes operaciones abiertas en el journal."
    lineas = [f"📂 Operaciones abiertas ({len(abiertas)}):", ""]
    for e in sorted(abiertas, key=lambda x: x.fecha_apertura, reverse=True):
        lineas.append(f"{e.ticker} -- {e.estrategia} (id {e.id})")
        lineas.append(f"  Abierta: {e.fecha_apertura[:10]} | Costo: ${e.costo:,.2f} | Tesis: {e.tesis}")
        lineas.append(f"  Motivo: {e.motivo}")
        lineas.append("")
    return "\n".join(lineas)


def _fmt_estadisticas(titulo: str, s: EstadisticasJournal) -> list[str]:
    if s.n_operaciones == 0:
        return [f"{titulo}: sin operaciones cerradas todavía."]
    ganancia_txt = f"${s.ganancia_promedio:,.2f}" if s.ganancia_promedio is not None else "No disponible"
    perdida_txt = f"${s.perdida_promedio:,.2f}" if s.perdida_promedio is not None else "No disponible"
    expectancy_txt = f"${s.expectancy:,.2f} por operación" if s.expectancy is not None else "No disponible"
    return [
        f"{titulo}: {s.n_operaciones} operaciones cerradas",
        f"  Win rate: {s.win_rate:.1%} ({s.n_ganadoras}G / {s.n_perdedoras}P)",
        f"  Ganancia promedio: {ganancia_txt}",
        f"  Pérdida promedio: {perdida_txt}",
        f"  Expectancy: {expectancy_txt}",
        f"  P&L total: ${s.pnl_total:+,.2f}",
        f"  Drawdown máximo: ${s.drawdown_maximo:,.2f}",
    ]


def mostrar_estadisticas() -> str:
    """Punto de entrada de /journal stats."""
    entradas, _ = _leer_journal()
    generales = calcular_estadisticas(entradas)
    if generales.n_operaciones == 0:
        return "Todavía no hay operaciones cerradas en el journal -- no hay estadísticas que calcular."
    lineas = ["📊 Estadísticas del journal", ""]
    lineas += _fmt_estadisticas("General", generales)
    por_estrategia = calcular_estadisticas_por_estrategia(entradas)
    if por_estrategia:
        lineas.append("")
        lineas.append("Por estrategia:")
        for nombre, s in por_estrategia.items():
            lineas.append("")
            lineas += _fmt_estadisticas(nombre, s)
    return "\n".join(lineas)
