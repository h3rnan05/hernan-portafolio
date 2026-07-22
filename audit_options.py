#!/usr/bin/env python3
"""Auditoría de por qué el ranking de /options quedó como quedó -- imprime
TODOS los números intermedios (IV real por leg vs. la IV de referencia,
EV, riesgo, EV/riesgo, probabilidad, liquidez, percentiles y score final)
Y compara el ranking VIEJO (una única IV plana ATM para todo -- como
funcionaba antes del fix de superficie de volatilidad) contra el NUEVO
(IV local interpolada en el breakeven de cada estrategia -- lo que
options_command.py usa hoy en producción), para poder verificar con
datos reales cuánto cambió y por qué. No manda nada a Telegram -- solo
imprime en el log del job/consola.

Reusa exactamente las mismas funciones públicas que options_command.py
usa en producción (options_ideas.obtener_cadena, options_strategies.
construir_estrategias/rankear/iv_referencia) para el ranking NUEVO. Para
reconstruir el ranking VIEJO, recalcula EV/probabilidad con la MISMA
fórmula pero sustituyendo la IV local por la IV plana sobre los mismos
patas/breakevens reales ya construidos -- no duplica lógica de negocio,
solo reevalúa con otra IV.

USO
  python audit_options.py TICKER
"""

from __future__ import annotations

import argparse

from screener.data.provider import YahooProvider
from screener.factors import technical as tech
from screener.options_ideas import CadenaOpciones, clasificar_tendencia, obtener_cadena
from screener.options_math import probabilidad_sobre, valor_esperado_payoff
from screener.options_strategies import (
    EstrategiaOpciones,
    _iv_local,
    construir_estrategias,
    direccion_estrategia,
    evaluar_payoff,
    iv_referencia,
)


def _iv_de_leg(cadena: CadenaOpciones, tipo: str, strike: float) -> float | None:
    tabla = cadena.calls if tipo == "call" else cadena.puts
    for c in tabla:
        if abs(c.strike - strike) < 1e-6:
            return c.iv
    return None


def _percentil(valores: list[float], x: float) -> float:
    n = len(valores)
    return 1.0 if n <= 1 else sum(1 for v in valores if v <= x) / n


def _ev_por_riesgo(valor_esperado: float | None, riesgo_maximo: float | None) -> float:
    if valor_esperado is not None and riesgo_maximo and riesgo_maximo > 0:
        return valor_esperado / riesgo_maximo
    return 0.0


def _payoff_generico(e: EstrategiaOpciones, spot: float):
    """Payoff real de la estrategia -- envuelve options_strategies.
    evaluar_payoff() (pública) fijando `spot` para Covered Call, que lo
    necesita porque esa estrategia también posee 100 acciones."""
    return lambda s: evaluar_payoff(e, s, spot)


def _probabilidad_generica(spot: float, dias: int, iv: float, breakevens: list[float], payoff) -> float | None:
    """Recalcula la probabilidad de éxito con una IV dada, infiriendo la
    dirección (éxito = precio arriba/abajo del breakeven, o entre dos
    breakevens para iron condor) directamente del payoff -- así no hace
    falta duplicar la lógica de cada estrategia una por una."""
    if len(breakevens) == 1:
        b = breakevens[0]
        eps = max(0.01, abs(b) * 0.001)
        favorece_arriba = payoff(b + eps) > payoff(b - eps)
        p = probabilidad_sobre(spot, b, dias, iv)
        if p is None:
            return None
        return p if favorece_arriba else 1.0 - p
    if len(breakevens) == 2:
        lo, hi = sorted(breakevens)
        p_lo = probabilidad_sobre(spot, lo, dias, iv)
        p_hi = probabilidad_sobre(spot, hi, dias, iv)
        return (p_lo - p_hi) if p_lo is not None and p_hi is not None else None
    return None


def _score(ev_riesgo: float, prob: float, liq: float, ev_riesgo_vals, prob_vals, liq_vals) -> float:
    return (_percentil(ev_riesgo_vals, ev_riesgo) * 0.5
            + _percentil(prob_vals, prob) * 0.3
            + _percentil(liq_vals, liq) * 0.2)


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
    iv_plana = iv_referencia(cadena, spot)
    print(f"Vencimiento: {cadena.vencimiento} ({cadena.dias_a_vencimiento} días) | "
          f"IV de referencia (promedio ATM call/put): {iv_plana:.2%}")
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
                      f"IV real: {iv_real:6.2%}  IV ref: {iv_plana:6.2%}  diff: {iv_real - iv_plana:+6.2%}")
            else:
                print(f"  {e.nombre:20s} {p.accion:8s} {p.tipo:4s} ${p.strike:7.2f}  IV real: No disponible")
    print()

    # --- Recalcular EV/probabilidad "viejos" (IV plana) sobre los MISMOS
    # patas/breakevens reales que ya se construyeron -- ninguna estrategia
    # se reconstruye, solo se reevalúa la parte de probabilidad/EV.
    dias = cadena.dias_a_vencimiento
    viejos = {}
    for e in estrategias:
        payoff = _payoff_generico(e, spot)
        ev_viejo = valor_esperado_payoff(spot, dias, iv_plana, payoff)
        prob_viejo = _probabilidad_generica(spot, dias, iv_plana, e.breakevens, payoff)
        iv_local_usada = _iv_local(cadena, spot, e.breakevens, iv_plana)
        viejos[e.nombre] = (ev_viejo, prob_viejo, iv_local_usada)

    print("--- IV local (nueva) vs. IV plana (vieja) por estrategia ---")
    for e in estrategias:
        _, _, iv_local_usada = viejos[e.nombre]
        print(f"  {e.nombre:20s} breakeven(s)={[round(b, 2) for b in e.breakevens]}  "
              f"IV local: {iv_local_usada:.2%}  IV plana: {iv_plana:.2%}  "
              f"diff: {iv_local_usada - iv_plana:+.2%}")
    print()

    print("--- EV y probabilidad: viejo (IV plana) vs. nuevo (IV local) ---")
    for e in estrategias:
        ev_viejo, prob_viejo, _ = viejos[e.nombre]
        print(f"  {e.nombre:20s} EV viejo=${ev_viejo:9.2f}  EV nuevo=${e.valor_esperado:9.2f}  "
              f"(diff ${e.valor_esperado - ev_viejo:+8.2f})   "
              f"prob viejo={prob_viejo:.1%}  prob nuevo={e.probabilidad_exito:.1%}")
    print()

    ev_riesgo_nuevo = {e.nombre: _ev_por_riesgo(e.valor_esperado, e.riesgo_maximo) for e in estrategias}
    ev_riesgo_viejo = {e.nombre: _ev_por_riesgo(viejos[e.nombre][0], e.riesgo_maximo) for e in estrategias}
    liq_vals = [e.liquidez_score or 0.0 for e in estrategias]

    ev_riesgo_nuevo_vals = list(ev_riesgo_nuevo.values())
    prob_nuevo_vals = [e.probabilidad_exito or 0.0 for e in estrategias]
    ev_riesgo_viejo_vals = list(ev_riesgo_viejo.values())
    prob_viejo_vals = [viejos[e.nombre][1] or 0.0 for e in estrategias]

    ranking_nuevo = sorted(
        estrategias,
        key=lambda e: _score(ev_riesgo_nuevo[e.nombre], e.probabilidad_exito or 0.0,
                             e.liquidez_score or 0.0, ev_riesgo_nuevo_vals, prob_nuevo_vals, liq_vals),
        reverse=True,
    )
    ranking_viejo = sorted(
        estrategias,
        key=lambda e: _score(ev_riesgo_viejo[e.nombre], viejos[e.nombre][1] or 0.0,
                             e.liquidez_score or 0.0, ev_riesgo_viejo_vals, prob_viejo_vals, liq_vals),
        reverse=True,
    )
    posicion_vieja = {e.nombre: i for i, e in enumerate(ranking_viejo, 1)}
    posicion_nueva = {e.nombre: i for i, e in enumerate(ranking_nuevo, 1)}

    print("--- Ranking VIEJO (IV plana, como estaba antes del fix) ---")
    for i, e in enumerate(ranking_viejo, 1):
        print(f"  {i}. {e.nombre}")
    print()

    print("--- Ranking NUEVO (IV local, producción actual) ---")
    for i, e in enumerate(ranking_nuevo, 1):
        print(f"  {i}. {e.nombre}")
    print()

    print("--- Qué cambió de posición y por qué ---")
    cambios = False
    for nombre in posicion_nueva:
        antes, despues = posicion_vieja[nombre], posicion_nueva[nombre]
        if antes != despues:
            cambios = True
            _, _, iv_local_usada = viejos[nombre]
            print(f"  {nombre}: #{antes} -> #{despues}  "
                  f"(IV local {iv_local_usada:.2%} vs. IV plana {iv_plana:.2%}, "
                  f"diff {iv_local_usada - iv_plana:+.2%})")
    if not cambios:
        print("  Ninguna estrategia cambió de posición en este ticker/día.")
    print()

    if ranking_nuevo:
        lider = ranking_nuevo[0].nombre
        direccion_lider = direccion_estrategia(lider)
        if tendencia == "alcista" and direccion_lider == "bajista":
            print(f"  Nota: el líder del ranking nuevo ({lider}) es una estrategia BAJISTA "
                  f"con tesis técnica ALCISTA -- puede ser legítimo (ver disclaimer: el ranking "
                  f"es risk-neutral, no direccional), pero vale revisarlo caso por caso.")
        elif tendencia == "bajista" and direccion_lider == "alcista":
            print(f"  Nota: el líder del ranking nuevo ({lider}) es una estrategia ALCISTA "
                  f"con tesis técnica BAJISTA -- puede ser legítimo, pero vale revisarlo caso por caso.")
        else:
            print(f"  El líder del ranking nuevo ({lider}) es coherente en dirección con la "
                  f"tesis técnica ({tendencia}), o es una estrategia neutral (Iron Condor).")


if __name__ == "__main__":
    main()
