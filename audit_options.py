#!/usr/bin/env python3
"""Auditoría de por qué el ranking de /options quedó como quedó -- imprime
TODOS los números intermedios (IV real por leg vs. la IV de referencia,
EV, riesgo, EV/riesgo, probabilidad, liquidez, percentiles y score final)
para poder verificar con datos reales si el ranking es matemáticamente
coherente antes de cambiar código. No manda nada a Telegram -- solo
imprime en el log del job/consola. Reusa exactamente las mismas funciones
públicas que options_command.py usa en producción (options_ideas.
obtener_cadena, options_strategies.construir_estrategias/rankear/
iv_referencia) -- ninguna lógica propia de negocio, solo instrumentación.

USO
  python audit_options.py TICKER
"""

from __future__ import annotations

import argparse

from screener.data.provider import YahooProvider
from screener.factors import technical as tech
from screener.options_ideas import CadenaOpciones, clasificar_tendencia, obtener_cadena
from screener.options_strategies import EstrategiaOpciones, construir_estrategias, iv_referencia


def _iv_de_leg(cadena: CadenaOpciones, tipo: str, strike: float) -> float | None:
    tabla = cadena.calls if tipo == "call" else cadena.puts
    for c in tabla:
        if abs(c.strike - strike) < 1e-6:
            return c.iv
    return None


def _percentil(valores: list[float], x: float) -> float:
    n = len(valores)
    return 1.0 if n <= 1 else sum(1 for v in valores if v <= x) / n


def _ev_por_riesgo(e: EstrategiaOpciones) -> float:
    if e.valor_esperado is not None and e.riesgo_maximo and e.riesgo_maximo > 0:
        return e.valor_esperado / e.riesgo_maximo
    return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    args = ap.parse_args()
    ticker = args.ticker.upper()

    provider = YahooProvider()
    barras = provider.barras([ticker], dias=400)[ticker]
    spot = barras.close[-1]
    tendencia = clasificar_tendencia(tech.score_tendencia(barras))
    print(f"Ticker: {ticker} | Spot: ${spot:.2f} | Tesis técnica: {tendencia}")

    cadena = obtener_cadena(ticker)
    if cadena is None:
        print("No pude obtener la cadena de opciones.")
        return
    iv_ref = iv_referencia(cadena, spot)
    print(f"Vencimiento: {cadena.vencimiento} ({cadena.dias_a_vencimiento} días) | "
          f"IV de referencia (promedio ATM call/put): {iv_ref:.2%}")
    print()

    estrategias = construir_estrategias(cadena, spot)
    if not estrategias:
        print("No se pudo construir ninguna estrategia.")
        return

    print("--- IV real por leg vs. IV de referencia (evidencia de skew) ---")
    for e in estrategias:
        for p in e.patas:
            iv_real = _iv_de_leg(cadena, p.tipo, p.strike)
            if iv_real is not None:
                print(f"  {e.nombre:20s} {p.accion:8s} {p.tipo:4s} ${p.strike:7.2f}  "
                      f"IV real: {iv_real:6.2%}  IV ref: {iv_ref:6.2%}  diff: {iv_real - iv_ref:+6.2%}")
            else:
                print(f"  {e.nombre:20s} {p.accion:8s} {p.tipo:4s} ${p.strike:7.2f}  IV real: No disponible")
    print()

    print("--- Desglose crudo por estrategia ---")
    for e in estrategias:
        print(f"  {e.nombre:20s} EV=${e.valor_esperado:9.2f}  riesgo=${e.riesgo_maximo:10.2f}  "
              f"EV/riesgo={_ev_por_riesgo(e):+.4%}  prob={e.probabilidad_exito:.1%}  "
              f"liq={e.liquidez_score:.1f}")
    print()

    ev_riesgo_vals = [_ev_por_riesgo(e) for e in estrategias]
    prob_vals = [e.probabilidad_exito or 0.0 for e in estrategias]
    liq_vals = [e.liquidez_score or 0.0 for e in estrategias]

    print("--- Percentiles y score final (50% EV/riesgo, 30% probabilidad, 20% liquidez) ---")
    resultados = []
    for e in estrategias:
        p_ev = _percentil(ev_riesgo_vals, _ev_por_riesgo(e))
        p_prob = _percentil(prob_vals, e.probabilidad_exito or 0.0)
        p_liq = _percentil(liq_vals, e.liquidez_score or 0.0)
        score = p_ev * 0.5 + p_prob * 0.3 + p_liq * 0.2
        resultados.append((e.nombre, p_ev, p_prob, p_liq, score))
        print(f"  {e.nombre:20s} percentil_EV={p_ev:.3f}  percentil_prob={p_prob:.3f}  "
              f"percentil_liq={p_liq:.3f}  SCORE={score:.4f}")
    print()

    print("--- Ranking final (score descendente) ---")
    for i, (nombre, *_ , score) in enumerate(sorted(resultados, key=lambda r: r[-1], reverse=True), 1):
        print(f"  {i}. {nombre} (score {score:.4f})")


if __name__ == "__main__":
    main()
