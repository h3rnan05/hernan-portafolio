"""Construcción y ranking de estrategias de opciones -- la pieza central
de /options. Todo lo que hay en este archivo es determinístico: los
strikes se eligen con reglas fijas de delta objetivo (convenciones
estándar de trading: ~30 delta para el leg corto de un spread vertical,
~16 delta para las alas de un iron condor, etc.), y el ranking se calcula
con una fórmula fija de pesos documentados. Ningún LLM interviene aquí --
un LLM podrá más adelante EXPLICAR estos resultados ya calculados en
lenguaje llano, pero nunca decidir strikes ni el orden del ranking
(Principio #3 del proyecto: la IA nunca decide qué operar).

Fuera de alcance de esta pieza (documentado, no fabricado):
  - Calendar Spread: necesita una segunda fecha de vencimiento (esta
    pieza solo cotiza UN vencimiento por ticker, elegido por
    options_ideas._elegir_vencimiento). Se puede agregar después.
  - IV Rank real: sigue sin estar disponible (ver options_ideas.py) --
    este módulo no lo usa para nada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from screener.options_ideas import CadenaOpciones, ContratoOpcion
from screener.options_math import greeks, probabilidad_sobre, valor_esperado_payoff

log = logging.getLogger("screener.options_strategies")

TipoPata = Literal["call", "put"]
AccionPata = Literal["comprar", "vender"]

# Convenciones estándar de trading para elegir strikes por delta objetivo
# (aproximación de "qué tan OTM" según cuánto riesgo/probabilidad se busca).
DELTA_SPREAD_VERTICAL = 0.30   # leg corto de spreads verticales, covered call, cash secured put
DELTA_ALA_IRON_CONDOR = 0.16   # legs cortos del iron condor
DELTA_ALA_PROTECCION = 0.08    # legs de protección (más OTM que el corto)


@dataclass(frozen=True)
class PataOpcion:
    tipo: TipoPata
    accion: AccionPata
    strike: float
    prima: float  # prima de mercado real de ESE contrato, no teórica


@dataclass(frozen=True)
class EstrategiaOpciones:
    nombre: str
    patas: list[PataOpcion]
    razon: str
    riesgo_maximo: float | None      # dólares, por 1 contrato (100 acciones)
    ganancia_maxima: float | None    # dólares; None = ilimitada
    breakevens: list[float] = field(default_factory=list)
    probabilidad_exito: float | None = None  # bajo la medida risk-neutral implícita en la IV
    valor_esperado: float | None = None      # dólares, por 1 contrato
    delta_neto: float | None = None
    theta_neto: float | None = None
    liquidez_score: float | None = None      # 0-100, aproximación (ver _liquidez)
    capital_adicional_requerido: bool = False  # covered call (acciones) / cash secured put (efectivo)


def _payoff_pata(pata: PataOpcion, precio_final: float) -> float:
    intrinseco = (max(precio_final - pata.strike, 0.0) if pata.tipo == "call"
                  else max(pata.strike - precio_final, 0.0))
    if pata.accion == "comprar":
        return intrinseco - pata.prima
    return pata.prima - intrinseco


def _payoff_posicion(patas: list[PataOpcion], precio_final: float) -> float:
    """Resultado en dólares por 1 contrato (100 acciones) de la posición
    completa al vencimiento."""
    return sum(_payoff_pata(p, precio_final) for p in patas) * 100


def _iv_o_referencia(contrato: ContratoOpcion, iv_referencia: float) -> float:
    return contrato.iv if contrato.iv and contrato.iv > 0 else iv_referencia


def _delta_contrato(contrato: ContratoOpcion, spot: float, dias: int,
                     tipo: TipoPata, iv_referencia: float) -> float | None:
    g = greeks(spot, contrato.strike, dias, _iv_o_referencia(contrato, iv_referencia), tipo)
    return g.delta if g else None


def _contrato_atm(contratos: list[ContratoOpcion], spot: float) -> ContratoOpcion | None:
    validos = [c for c in contratos if c.prima is not None]
    return min(validos, key=lambda c: abs(c.strike - spot)) if validos else None


def _contrato_delta_objetivo(
    contratos: list[ContratoOpcion], delta_objetivo_abs: float, tipo: TipoPata,
    spot: float, dias: int, iv_referencia: float,
    filtro_strike=None,
) -> ContratoOpcion | None:
    """El contrato cuyo |delta| está más cerca del objetivo, entre los que
    cumplen filtro_strike (ej. 'strike por encima del leg largo')."""
    candidatos = []
    for c in contratos:
        if c.prima is None or (filtro_strike and not filtro_strike(c.strike)):
            continue
        d = _delta_contrato(c, spot, dias, tipo, iv_referencia)
        if d is None:
            continue
        candidatos.append((abs(abs(d) - delta_objetivo_abs), c))
    return min(candidatos, key=lambda x: x[0])[1] if candidatos else None


def _liquidez_contrato(c: ContratoOpcion) -> float | None:
    partes = []
    if c.open_interest is not None:
        partes.append(min(c.open_interest / 500.0, 1.0) * 100)
    if c.bid is not None and c.ask is not None and c.bid > 0 and c.ask > 0:
        medio = (c.bid + c.ask) / 2
        spread_pct = (c.ask - c.bid) / medio if medio else 1.0
        partes.append(max(0.0, 1.0 - spread_pct / 0.20) * 100)
    return sum(partes) / len(partes) if partes else None


def _liquidez(contratos: list[ContratoOpcion]) -> float | None:
    """Aproximación 0-100 de qué tan fácil es operar estos contratos en la
    práctica: promedia, por cada pata, open interest (>=500 contratos =
    liquidez máxima) y qué tan angosto es el bid-ask spread relativo al
    precio medio (<=20% del medio = referencia aceptable). Deliberadamente
    simple y documentado como aproximación, no un modelo de microestructura
    real. None si falta el dato en TODAS las patas (nunca se inventa)."""
    valores = [v for c in contratos if (v := _liquidez_contrato(c)) is not None]
    return sum(valores) / len(valores) if valores else None


def _prob_sobre(spot: float, umbral: float, dias: int, iv: float) -> float | None:
    return probabilidad_sobre(spot, umbral, dias, iv)


def _prob_bajo(spot: float, umbral: float, dias: int, iv: float) -> float | None:
    p = probabilidad_sobre(spot, umbral, dias, iv)
    return None if p is None else 1.0 - p


def _greeks_neto(patas_con_tipo: list[tuple[PataOpcion, float]], spot: float, dias: int) -> tuple[float | None, float | None]:
    """patas_con_tipo: [(pata, iv_a_usar), ...]. Suma delta/theta con
    signo (+ si se compró la pata, - si se vendió)."""
    delta_total, theta_total = 0.0, 0.0
    for pata, iv in patas_con_tipo:
        g = greeks(spot, pata.strike, dias, iv, pata.tipo)
        if g is None:
            return None, None
        signo = 1.0 if pata.accion == "comprar" else -1.0
        delta_total += signo * g.delta
        theta_total += signo * g.theta
    return delta_total, theta_total


def _long_call(calls: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    atm = _contrato_atm(calls, spot)
    if atm is None:
        return None
    pata = PataOpcion("call", "comprar", atm.strike, atm.prima)
    breakeven = atm.strike + atm.prima
    delta_neto, theta_neto = _greeks_neto([(pata, _iv_o_referencia(atm, iv_ref))], spot, dias)
    return EstrategiaOpciones(
        nombre="Long Call", patas=[pata],
        razon=f"Compra call ${atm.strike:.2f}: apuesta simple a que el precio suba, "
              f"pérdida limitada a la prima pagada.",
        riesgo_maximo=atm.prima * 100, ganancia_maxima=None, breakevens=[breakeven],
        probabilidad_exito=_prob_sobre(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion([pata], s)),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([atm]),
    )


def _long_put(puts: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    atm = _contrato_atm(puts, spot)
    if atm is None:
        return None
    pata = PataOpcion("put", "comprar", atm.strike, atm.prima)
    breakeven = atm.strike - atm.prima
    delta_neto, theta_neto = _greeks_neto([(pata, _iv_o_referencia(atm, iv_ref))], spot, dias)
    return EstrategiaOpciones(
        nombre="Long Put", patas=[pata],
        razon=f"Compra put ${atm.strike:.2f}: apuesta simple a que el precio baje, "
              f"pérdida limitada a la prima pagada.",
        riesgo_maximo=atm.prima * 100, ganancia_maxima=max(atm.strike - atm.prima, 0) * 100,
        breakevens=[breakeven],
        probabilidad_exito=_prob_bajo(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion([pata], s)),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([atm]),
    )


def _bull_call_spread(calls: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    largo = _contrato_atm(calls, spot)
    if largo is None:
        return None
    corto = _contrato_delta_objetivo(calls, DELTA_SPREAD_VERTICAL, "call", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k > largo.strike)
    if corto is None:
        return None
    patas = [PataOpcion("call", "comprar", largo.strike, largo.prima),
             PataOpcion("call", "vender", corto.strike, corto.prima)]
    costo_neto = largo.prima - corto.prima
    if costo_neto <= 0:
        return None
    breakeven = largo.strike + costo_neto
    delta_neto, theta_neto = _greeks_neto(
        [(patas[0], _iv_o_referencia(largo, iv_ref)), (patas[1], _iv_o_referencia(corto, iv_ref))], spot, dias)
    return EstrategiaOpciones(
        nombre="Bull Call Spread", patas=patas,
        razon=f"Compra call ${largo.strike:.2f} / vende call ${corto.strike:.2f}: reduce el costo de "
              f"apostar al alza a cambio de limitar la ganancia.",
        riesgo_maximo=costo_neto * 100, ganancia_maxima=(corto.strike - largo.strike - costo_neto) * 100,
        breakevens=[breakeven],
        probabilidad_exito=_prob_sobre(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion(patas, s)),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([largo, corto]),
    )


def _bear_put_spread(puts: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    largo = _contrato_atm(puts, spot)
    if largo is None:
        return None
    corto = _contrato_delta_objetivo(puts, DELTA_SPREAD_VERTICAL, "put", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k < largo.strike)
    if corto is None:
        return None
    patas = [PataOpcion("put", "comprar", largo.strike, largo.prima),
             PataOpcion("put", "vender", corto.strike, corto.prima)]
    costo_neto = largo.prima - corto.prima
    if costo_neto <= 0:
        return None
    breakeven = largo.strike - costo_neto
    delta_neto, theta_neto = _greeks_neto(
        [(patas[0], _iv_o_referencia(largo, iv_ref)), (patas[1], _iv_o_referencia(corto, iv_ref))], spot, dias)
    return EstrategiaOpciones(
        nombre="Bear Put Spread", patas=patas,
        razon=f"Compra put ${largo.strike:.2f} / vende put ${corto.strike:.2f}: reduce el costo de "
              f"apostar a la baja a cambio de limitar la ganancia.",
        riesgo_maximo=costo_neto * 100, ganancia_maxima=(largo.strike - corto.strike - costo_neto) * 100,
        breakevens=[breakeven],
        probabilidad_exito=_prob_bajo(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion(patas, s)),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([largo, corto]),
    )


def _bull_put_spread(puts: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    corto = _contrato_delta_objetivo(puts, DELTA_SPREAD_VERTICAL, "put", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k < spot)
    if corto is None:
        return None
    largo = _contrato_delta_objetivo(puts, DELTA_ALA_PROTECCION, "put", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k < corto.strike)
    if largo is None:
        return None
    patas = [PataOpcion("put", "vender", corto.strike, corto.prima),
             PataOpcion("put", "comprar", largo.strike, largo.prima)]
    credito_neto = corto.prima - largo.prima
    if credito_neto <= 0:
        return None
    breakeven = corto.strike - credito_neto
    delta_neto, theta_neto = _greeks_neto(
        [(patas[0], _iv_o_referencia(corto, iv_ref)), (patas[1], _iv_o_referencia(largo, iv_ref))], spot, dias)
    return EstrategiaOpciones(
        nombre="Bull Put Spread", patas=patas,
        razon=f"Vende put ${corto.strike:.2f} / compra put ${largo.strike:.2f} como protección: cobra "
              f"una prima apostando a que el precio se mantenga por encima del breakeven.",
        riesgo_maximo=(corto.strike - largo.strike - credito_neto) * 100, ganancia_maxima=credito_neto * 100,
        breakevens=[breakeven],
        probabilidad_exito=_prob_sobre(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion(patas, s)),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([corto, largo]),
    )


def _bear_call_spread(calls: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    corto = _contrato_delta_objetivo(calls, DELTA_SPREAD_VERTICAL, "call", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k > spot)
    if corto is None:
        return None
    largo = _contrato_delta_objetivo(calls, DELTA_ALA_PROTECCION, "call", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k > corto.strike)
    if largo is None:
        return None
    patas = [PataOpcion("call", "vender", corto.strike, corto.prima),
             PataOpcion("call", "comprar", largo.strike, largo.prima)]
    credito_neto = corto.prima - largo.prima
    if credito_neto <= 0:
        return None
    breakeven = corto.strike + credito_neto
    delta_neto, theta_neto = _greeks_neto(
        [(patas[0], _iv_o_referencia(corto, iv_ref)), (patas[1], _iv_o_referencia(largo, iv_ref))], spot, dias)
    return EstrategiaOpciones(
        nombre="Bear Call Spread", patas=patas,
        razon=f"Vende call ${corto.strike:.2f} / compra call ${largo.strike:.2f} como protección: cobra "
              f"una prima apostando a que el precio se mantenga por debajo del breakeven.",
        riesgo_maximo=(largo.strike - corto.strike - credito_neto) * 100, ganancia_maxima=credito_neto * 100,
        breakevens=[breakeven],
        probabilidad_exito=_prob_bajo(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion(patas, s)),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([corto, largo]),
    )


def _covered_call(calls: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    corto = _contrato_delta_objetivo(calls, DELTA_SPREAD_VERTICAL, "call", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k > spot)
    if corto is None:
        return None
    pata = PataOpcion("call", "vender", corto.strike, corto.prima)
    breakeven = spot - corto.prima

    def payoff(precio_final: float) -> float:
        return (precio_final - spot) * 100 + _payoff_posicion([pata], precio_final)

    g = greeks(spot, corto.strike, dias, _iv_o_referencia(corto, iv_ref), "call")
    delta_neto = (1.0 - g.delta) if g else None  # 100 acciones = delta 1.0, menos el delta de la call vendida
    theta_neto = -g.theta if g else None
    return EstrategiaOpciones(
        nombre="Covered Call", patas=[pata],
        razon=f"Compra 100 acciones + vende call ${corto.strike:.2f}: genera ingreso por la prima, "
              f"limita la ganancia si el precio sube mucho.",
        riesgo_maximo=(spot - corto.prima) * 100, ganancia_maxima=(corto.strike - spot + corto.prima) * 100,
        breakevens=[breakeven],
        probabilidad_exito=_prob_sobre(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, payoff),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([corto]),
        capital_adicional_requerido=True,
    )


def _cash_secured_put(puts: list[ContratoOpcion], spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    corto = _contrato_delta_objetivo(puts, DELTA_SPREAD_VERTICAL, "put", spot, dias, iv_ref,
                                      filtro_strike=lambda k: k < spot)
    if corto is None:
        return None
    pata = PataOpcion("put", "vender", corto.strike, corto.prima)
    breakeven = corto.strike - corto.prima
    delta_neto, theta_neto = _greeks_neto([(pata, _iv_o_referencia(corto, iv_ref))], spot, dias)
    delta_neto = -delta_neto if delta_neto is not None else None
    theta_neto = -theta_neto if theta_neto is not None else None
    return EstrategiaOpciones(
        nombre="Cash Secured Put", patas=[pata],
        razon=f"Vende put ${corto.strike:.2f} con efectivo reservado para comprar 100 acciones si "
              f"te asignan: cobra una prima apostando a que el precio se mantenga por encima del breakeven.",
        riesgo_maximo=(corto.strike - corto.prima) * 100, ganancia_maxima=corto.prima * 100,
        breakevens=[breakeven],
        probabilidad_exito=_prob_sobre(spot, breakeven, dias, iv_ref),
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion([pata], s)),
        delta_neto=delta_neto, theta_neto=theta_neto, liquidez_score=_liquidez([corto]),
        capital_adicional_requerido=True,
    )


def _iron_condor(calls: list[ContratoOpcion], puts: list[ContratoOpcion],
                  spot: float, dias: int, iv_ref: float) -> EstrategiaOpciones | None:
    put_corto = _contrato_delta_objetivo(puts, DELTA_ALA_IRON_CONDOR, "put", spot, dias, iv_ref,
                                          filtro_strike=lambda k: k < spot)
    if put_corto is None:
        return None
    put_largo = _contrato_delta_objetivo(puts, DELTA_ALA_PROTECCION, "put", spot, dias, iv_ref,
                                          filtro_strike=lambda k: k < put_corto.strike)
    call_corto = _contrato_delta_objetivo(calls, DELTA_ALA_IRON_CONDOR, "call", spot, dias, iv_ref,
                                           filtro_strike=lambda k: k > spot)
    if call_corto is None:
        return None
    call_largo = _contrato_delta_objetivo(calls, DELTA_ALA_PROTECCION, "call", spot, dias, iv_ref,
                                           filtro_strike=lambda k: k > call_corto.strike)
    if put_largo is None or call_largo is None:
        return None

    patas = [
        PataOpcion("put", "vender", put_corto.strike, put_corto.prima),
        PataOpcion("put", "comprar", put_largo.strike, put_largo.prima),
        PataOpcion("call", "vender", call_corto.strike, call_corto.prima),
        PataOpcion("call", "comprar", call_largo.strike, call_largo.prima),
    ]
    credito_neto = ((put_corto.prima - put_largo.prima) + (call_corto.prima - call_largo.prima))
    if credito_neto <= 0:
        return None
    ancho_puts = put_corto.strike - put_largo.strike
    ancho_calls = call_largo.strike - call_corto.strike
    riesgo = max(ancho_puts, ancho_calls) * 100 - credito_neto * 100
    breakeven_inf = put_corto.strike - credito_neto
    breakeven_sup = call_corto.strike + credito_neto
    prob_inf = _prob_sobre(spot, breakeven_inf, dias, iv_ref)
    prob_sup = _prob_sobre(spot, breakeven_sup, dias, iv_ref)
    prob = (prob_inf - prob_sup) if prob_inf is not None and prob_sup is not None else None
    contratos_iv = [(patas[0], _iv_o_referencia(put_corto, iv_ref)), (patas[1], _iv_o_referencia(put_largo, iv_ref)),
                     (patas[2], _iv_o_referencia(call_corto, iv_ref)), (patas[3], _iv_o_referencia(call_largo, iv_ref))]
    delta_neto, theta_neto = _greeks_neto(contratos_iv, spot, dias)
    return EstrategiaOpciones(
        nombre="Iron Condor", patas=patas,
        razon=f"Vende put ${put_corto.strike:.2f} / compra put ${put_largo.strike:.2f} + vende call "
              f"${call_corto.strike:.2f} / compra call ${call_largo.strike:.2f}: cobra una prima "
              f"apostando a que el precio se quede dentro de un rango hasta el vencimiento.",
        riesgo_maximo=riesgo, ganancia_maxima=credito_neto * 100,
        breakevens=[breakeven_inf, breakeven_sup],
        probabilidad_exito=prob,
        valor_esperado=valor_esperado_payoff(spot, dias, iv_ref, lambda s: _payoff_posicion(patas, s)),
        delta_neto=delta_neto, theta_neto=theta_neto,
        liquidez_score=_liquidez([put_corto, put_largo, call_corto, call_largo]),
    )


_CONSTRUCTORES = [
    lambda cadena, spot, dias, iv_ref: _long_call(cadena.calls, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _long_put(cadena.puts, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _bull_call_spread(cadena.calls, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _bear_put_spread(cadena.puts, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _bull_put_spread(cadena.puts, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _bear_call_spread(cadena.calls, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _covered_call(cadena.calls, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _cash_secured_put(cadena.puts, spot, dias, iv_ref),
    lambda cadena, spot, dias, iv_ref: _iron_condor(cadena.calls, cadena.puts, spot, dias, iv_ref),
]


def construir_estrategias(cadena: CadenaOpciones, spot: float) -> list[EstrategiaOpciones]:
    """Punto de entrada: construye las estrategias que se puedan armar con
    los datos reales de la cadena (una estrategia se omite, no se inventa,
    si no hay un contrato con el delta objetivo disponible). Calendar
    Spread no está incluido -- ver docstring del módulo."""
    dias = cadena.dias_a_vencimiento
    if not dias or dias <= 0 or not cadena.calls or not cadena.puts:
        return []
    atm_call = _contrato_atm(cadena.calls, spot)
    atm_put = _contrato_atm(cadena.puts, spot)
    ivs_atm = [c.iv for c in (atm_call, atm_put) if c and c.iv]
    if not ivs_atm:
        return []
    iv_ref = sum(ivs_atm) / len(ivs_atm)
    if iv_ref <= 0:
        return []

    resultado = []
    for construir in _CONSTRUCTORES:
        try:
            estrategia = construir(cadena, spot, dias, iv_ref)
        except Exception:
            log.debug("no se pudo construir una estrategia de opciones", exc_info=True)
            estrategia = None
        if estrategia is not None:
            resultado.append(estrategia)
    return resultado


def _ev_por_riesgo(e: EstrategiaOpciones) -> float:
    if e.valor_esperado is not None and e.riesgo_maximo and e.riesgo_maximo > 0:
        return e.valor_esperado / e.riesgo_maximo
    return 0.0


def _percentil(valores: list[float], x: float) -> float:
    """Percentil (0..1) de x dentro de `valores` -- misma técnica de
    scoring cross-sectional que screener/scoring.py usa para los factores
    del stock screener, aplicada aquí a las ~9 estrategias candidatas.
    Necesaria porque EV/riesgo, probabilidad y liquidez viven en escalas
    completamente distintas (EV/riesgo es típicamente una fracción de
    ~0.1%-1%, probabilidad y liquidez son 0-1): sumarlas directamente con
    pesos fijos deja que probabilidad/liquidez dominen el score sin que
    el peso nominal de EV/riesgo importe. Normalizar por percentil dentro
    del propio grupo evita ese sesgo de escala."""
    n = len(valores)
    if n <= 1:
        return 1.0
    return sum(1 for v in valores if v <= x) / n


def rankear(estrategias: list[EstrategiaOpciones]) -> list[EstrategiaOpciones]:
    """Orden puramente cuantitativo, de mejor a peor score compuesto --
    nunca decide cuál operar, la decisión es del usuario. Cada métrica se
    normaliza a su percentil DENTRO de las estrategias candidatas (ver
    _percentil) antes de combinarlas con pesos fijos y documentados, que
    no se ajustan por ticker ni por LLM:
      - 50%: valor esperado por dólar de riesgo (rendimiento esperado
        ajustado por lo que se arriesga -- la métrica central).
      - 30%: probabilidad de éxito bajo la IV implícita.
      - 20%: liquidez (penaliza contratos caros de operar en la práctica
        aunque la matemática se vea bien en el papel).
    No es una recomendación de cuál operar -- ver DISCLAIMER en
    options_ideas.py, que aplica igual aquí."""
    if not estrategias:
        return []
    ev_riesgo = [_ev_por_riesgo(e) for e in estrategias]
    probs = [e.probabilidad_exito or 0.0 for e in estrategias]
    liquidez = [e.liquidez_score or 0.0 for e in estrategias]

    scores = [
        _percentil(ev_riesgo, ev_riesgo[i]) * 0.5
        + _percentil(probs, probs[i]) * 0.3
        + _percentil(liquidez, liquidez[i]) * 0.2
        for i in range(len(estrategias))
    ]
    orden = sorted(range(len(estrategias)), key=lambda i: scores[i], reverse=True)
    return [estrategias[i] for i in orden]
