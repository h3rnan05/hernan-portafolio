"""Formatea la shortlist para humanos: texto para Telegram y markdown para
archivo. Cada acción viene con las razones (los factores en que puntúa alto),
que es el 'why' que pide el spec — el analista lee el porqué, no solo el rank."""

from __future__ import annotations

from datetime import UTC, datetime

from screener.config import ScreenerConfig
from screener.scoring import Puntuacion

_ETIQUETAS = {
    "momentum": "momentum fuerte (3/6/12m)",
    "tendencia": "tendencia alcista (sobre medias)",
    "baja_vol": "baja volatilidad",
    "liquidez": "muy líquida",
    "calidad": "alta calidad (ROE/márgenes)",
    "valor": "valuación atractiva",
}


def razones(p: Puntuacion, n: int) -> list[str]:
    """Los n factores con mayor sub-score (>=60) que explican el ranking."""
    top = sorted(p.sub.items(), key=lambda kv: kv[1], reverse=True)
    return [f"{_ETIQUETAS.get(f, f)} ({s:.0f})" for f, s in top[:n] if s >= 60]


def texto_telegram(ranking: list[Puntuacion], cfg: ScreenerConfig,
                   universo_n: int) -> str:
    hoy = datetime.now(UTC).strftime("%Y-%m-%d")
    top = ranking[:cfg.top_n]
    lineas = [
        f"🔬 Screener cuantitativo — {hoy}",
        f"Escaneadas {universo_n} acciones → shortlist de {len(top)}.",
        "\"El computador busca; tú decides.\"",
        "",
    ]
    for i, p in enumerate(top, 1):
        r = razones(p, cfg.razones_por_accion)
        motivo = "; ".join(r) if r else "score compuesto balanceado"
        sec = f" · {p.sector}" if p.sector else ""
        lineas.append(f"{i:2d}. {p.ticker}  {p.score_total:.0f}/100{sec}")
        lineas.append(f"     {motivo}")
    lineas.append("")
    lineas.append("No es una recomendación de compra: es una lista para "
                  "investigar más a fondo.")
    return "\n".join(lineas)


def markdown(ranking: list[Puntuacion], cfg: ScreenerConfig,
            universo_n: int) -> str:
    hoy = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# Shortlist cuantitativa — {hoy}", "",
           f"Universo escaneado: **{universo_n}** acciones · "
           f"shortlist: **{min(cfg.top_n, len(ranking))}**", "",
           "| # | Ticker | Score | Sector | Factores destacados |",
           "|--:|--------|------:|--------|---------------------|"]
    for i, p in enumerate(ranking[:cfg.top_n], 1):
        r = ", ".join(razones(p, cfg.razones_por_accion)) or "—"
        out.append(f"| {i} | {p.ticker} | {p.score_total:.0f} | "
                   f"{p.sector or '—'} | {r} |")
    return "\n".join(out) + "\n"
