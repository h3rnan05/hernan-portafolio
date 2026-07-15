"""'Options Research Ideas' -- por cada acción de la shortlist, ideas de
ESTRATEGIAS a investigar según tendencia + volatilidad implícita. NUNCA una
señal de compra/venta ni una recomendación de trade.

Reglas duras de este módulo:
  1. Determinístico: tendencia y nivel de IV se clasifican con umbrales
     fijos, no con un LLM. Misma entrada, misma salida.
  2. Nunca se inventa un dato que no se pudo obtener. Si no hay IV real,
     el módulo no adivina si está "alta" o "baja": muestra las DOS ramas
     posibles (igual que los ejemplos del propio spec) y lo dice
     explícitamente.
  3. Riesgo/ganancia/breakeven con números reales SOLO cuando se cotizó un
     contrato real (Long Call/Put con strike y prima ATM obtenidos de
     verdad). Para estrategias de varias patas (Covered Call, Straddle,
     Iron Condor, Bear Call Spread) se explica la FÓRMULA, nunca un número
     inventado -- esas estrategias dependen de qué strikes elija el
     usuario al investigarlas.

Fuera de alcance de esta parte (documentado, no fabricado):
  - IV Rank real: requiere una serie histórica de IV que este screener no
    recolecta hoy. Se muestra "No disponible" siempre, nunca un número.
  - El "nivel de IV" (alta/baja) se aproxima comparando la IV actual
    contra la volatilidad histórica del propio papel (IV/HV ratio) -- es
    el sustituto estándar cuando no se tiene el percentil histórico real
    de IV. Se documenta como aproximación, no como IV Rank.
"""

from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime

from screener.scoring import Puntuacion

log = logging.getLogger("screener.options_ideas")

DISCLAIMER = (
    "Esto NO es una recomendación. Es solo un punto de partida educativo "
    "para investigar más a fondo."
)


@dataclass
class DatosOpciones:
    """Snapshot best-effort de opciones ATM (at-the-money) para un ticker.
    Cualquier campo puede ser None -- el proveedor de datos gratis puede
    fallar o Yahoo puede bloquear la IP del datacenter (ya pasó con el RSS
    de noticias en este mismo repo); el módulo degrada con gracia."""
    ticker: str
    vencimiento: str | None = None          # fecha de la expiración usada, YYYY-MM-DD
    dias_a_vencimiento: int | None = None
    strike_call_atm: float | None = None
    prima_call_atm: float | None = None
    iv_call_atm: float | None = None
    strike_put_atm: float | None = None
    prima_put_atm: float | None = None
    iv_put_atm: float | None = None
    proxima_fecha_resultados: str | None = None

    @property
    def iv_actual(self) -> float | None:
        """IV 'actual' = promedio de la IV de la call y la put ATM (deberían
        ser casi iguales por paridad put-call; promediar reduce ruido)."""
        vals = [v for v in (self.iv_call_atm, self.iv_put_atm) if v is not None]
        return sum(vals) / len(vals) if vals else None


@dataclass
class IdeaEstrategia:
    nombre: str
    razon: str
    riesgo_maximo: str
    ganancia_maxima: str
    breakeven: str
    notas: str


class ProveedorOpciones(ABC):
    """Contrato de datos de opciones -- misma idea de dependency injection
    que DataProvider en data/provider.py. Se llama solo para los tickers de
    la shortlist final (top_n, ~20), nunca para las 500 del universo:
    cotizar cadenas de opciones es caro y frágil, así que se limita el
    radio de acción a lo que de verdad se le va a mostrar al usuario."""

    @abstractmethod
    def datos(self, ticker: str, precio_actual: float | None) -> DatosOpciones:
        """Best-effort: nunca lanza, siempre devuelve un DatosOpciones
        (con campos en None si algo falló)."""


class YahooOpcionesProvider(ProveedorOpciones):
    def __init__(self, pausa: float = 0.2) -> None:
        self.pausa = pausa

    def datos(self, ticker: str, precio_actual: float | None) -> DatosOpciones:
        vacio = DatosOpciones(ticker=ticker)
        try:
            import yfinance as yf
        except ImportError:
            log.warning("yfinance no instalado: ideas de opciones quedan vacías")
            return vacio
        try:
            tk = yf.Ticker(ticker)
            expiraciones = tk.options
            if not expiraciones:
                return vacio
            vencimiento = _elegir_vencimiento(expiraciones)
            cadena = tk.option_chain(vencimiento)
            call_atm = _fila_atm(cadena.calls, precio_actual)
            put_atm = _fila_atm(cadena.puts, precio_actual)
            resultado = DatosOpciones(
                ticker=ticker,
                vencimiento=vencimiento,
                dias_a_vencimiento=_dias_hasta(vencimiento),
                strike_call_atm=_get(call_atm, "strike"),
                prima_call_atm=_prima_media(call_atm),
                iv_call_atm=_get(call_atm, "impliedVolatility"),
                strike_put_atm=_get(put_atm, "strike"),
                prima_put_atm=_prima_media(put_atm),
                iv_put_atm=_get(put_atm, "impliedVolatility"),
                proxima_fecha_resultados=_proxima_fecha_resultados(tk),
            )
        except Exception as e:
            log.debug("datos de opciones %s fallaron: %s", ticker, e)
            resultado = vacio
        time.sleep(self.pausa)
        return resultado


def _elegir_vencimiento(expiraciones: tuple[str, ...]) -> str:
    """Prefiere la expiración más cercana a 30-45 días (la referencia
    estándar para 'la IV' de un papel); si ninguna cae en ese rango, usa la
    más próxima que sea >=14 días para no tomar una semanal ruidosa."""
    hoy = date.today()
    candidatas = []
    for exp in expiraciones:
        try:
            dias = (date.fromisoformat(exp) - hoy).days
        except ValueError:
            continue
        if dias >= 14:
            candidatas.append((abs(dias - 35), exp))
    if candidatas:
        return sorted(candidatas)[0][1]
    return expiraciones[0]


def _dias_hasta(vencimiento: str | None) -> int | None:
    if not vencimiento:
        return None
    try:
        return (date.fromisoformat(vencimiento) - date.today()).days
    except ValueError:
        return None


def _fila_atm(tabla, precio_actual: float | None):
    """Fila (call o put) con strike más cercano al precio actual. `tabla`
    es un DataFrame de pandas (lo que devuelve yfinance); se accede de
    forma defensiva para no acoplarse a pandas como dependencia dura."""
    if tabla is None or precio_actual is None or len(tabla) == 0:
        return None
    diffs = (tabla["strike"] - precio_actual).abs()
    return tabla.loc[diffs.idxmin()]


def _get(fila, campo: str) -> float | None:
    if fila is None:
        return None
    v = fila.get(campo)
    try:
        f = float(v)
        return f if f == f else None  # descarta NaN
    except (TypeError, ValueError):
        return None


def _prima_media(fila) -> float | None:
    """Prima = punto medio bid/ask; cae a lastPrice si no hay bid/ask."""
    if fila is None:
        return None
    bid, ask = _get(fila, "bid"), _get(fila, "ask")
    if bid and ask and bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    return _get(fila, "lastPrice")


def _proxima_fecha_resultados(tk) -> str | None:
    try:
        df = tk.get_earnings_dates(limit=4)
        if df is None or len(df) == 0:
            return None
        hoy = datetime.now(UTC)
        futuras = [idx for idx in df.index if idx.to_pydatetime().replace(tzinfo=UTC) >= hoy]
        if not futuras:
            return None
        return min(futuras).strftime("%Y-%m-%d")
    except Exception:
        return None


def clasificar_tendencia(tendencia_cruda: float | None) -> str:
    """tendencia_cruda viene de tech.score_tendencia(): 0..3, cuántas
    condiciones alcistas de medias móviles se cumplen. Absoluto, no un
    percentil cross-sectional -- por eso sirve para clasificar la acción
    en sí misma, no contra el resto del universo."""
    if tendencia_cruda is None:
        return "desconocida"
    if tendencia_cruda >= 3.0:
        return "alcista"
    if tendencia_cruda <= 0.0:
        return "bajista"
    return "neutral"


def clasificar_iv(iv_actual: float | None, vol_historica: float | None) -> str | None:
    """Aproximación estándar cuando no hay IV Rank real: comparar la IV
    actual contra la volatilidad histórica realizada del propio papel.
    IV >= HV -> las opciones cotizan "caras" frente a su propio historial;
    IV < HV -> cotizan "baratas". Devuelve None si falta cualquiera de
    los dos datos (nunca supone)."""
    if iv_actual is None or not vol_historica or vol_historica <= 0:
        return None
    return "alta" if iv_actual >= vol_historica else "baja"


# (tendencia, nivel_iv) -> (nombre de estrategia, razón en lenguaje llano)
_REGLAS: dict[tuple[str, str], tuple[str, str]] = {
    ("alcista", "baja"): (
        "Long Call",
        "Tendencia alcista y primas de opciones relativamente baratas "
        "(la volatilidad implícita está por debajo de la histórica).",
    ),
    ("alcista", "alta"): (
        "Covered Call",
        "La volatilidad implícita alta encarece las primas de las opciones.",
    ),
    ("neutral", "baja"): (
        "Long Straddle",
        "Las opciones están relativamente baratas si se espera un movimiento grande.",
    ),
    ("neutral", "alta"): (
        "Iron Condor",
        "La volatilidad implícita alta hace más atractivo vender prima con "
        "riesgo definido que comprarla.",
    ),
    ("bajista", "baja"): (
        "Long Put",
        "Tendencia bajista y primas de opciones relativamente baratas.",
    ),
    ("bajista", "alta"): (
        "Bear Call Spread",
        "Tendencia bajista y volatilidad implícita alta favorecen cobrar "
        "una prima rica con riesgo definido.",
    ),
}

_NOTAS_EDUCATIVAS: dict[str, str] = {
    "Long Call": "Comprar una call da el derecho (no la obligación) de comprar 100 "
                 "acciones al strike antes del vencimiento. Se usa para apostar a "
                 "que el precio suba, arriesgando solo la prima pagada.",
    "Long Put": "Comprar una put da el derecho de vender 100 acciones al strike "
                "antes del vencimiento. Se usa para apostar a que el precio baje, "
                "o para cubrir una posición que ya se tiene.",
    "Covered Call": "Vender una call sobre acciones que ya se poseen. Genera ingreso "
                    "por la prima, pero limita la ganancia si la acción sube mucho "
                    "por encima del strike vendido.",
    "Long Straddle": "Comprar una call y una put al mismo strike y vencimiento. Gana "
                      "si el precio se mueve mucho en cualquier dirección; pierde si "
                      "se queda quieto (ambas primas se erosionan con el tiempo).",
    "Iron Condor": "Vender un spread de calls y un spread de puts alrededor del "
                   "precio actual. Gana si el precio se queda dentro de un rango "
                   "hasta el vencimiento; el riesgo queda definido por el ancho de "
                   "los spreads.",
    "Bear Call Spread": "Vender una call y comprar otra de strike más alto (mismo "
                        "vencimiento). Apuesta a que el precio se quede por debajo "
                        "del strike vendido, con riesgo y ganancia definidos de antemano.",
}


def _idea_long_call(datos: DatosOpciones, razon: str) -> IdeaEstrategia:
    if datos.strike_call_atm is not None and datos.prima_call_atm is not None:
        riesgo = f"${datos.prima_call_atm * 100:,.0f} (prima pagada por 1 contrato, 100 acciones)"
        breakeven = f"${datos.strike_call_atm + datos.prima_call_atm:.2f} (strike + prima pagada)"
    else:
        riesgo = "La prima pagada por el contrato -- depende del strike y vencimiento que elijas."
        breakeven = "Strike + prima pagada."
    return IdeaEstrategia(
        nombre="Long Call", razon=razon, riesgo_maximo=riesgo,
        ganancia_maxima="Ilimitada (sube junto con el precio de la acción).",
        breakeven=breakeven, notas=_NOTAS_EDUCATIVAS["Long Call"],
    )


def _idea_long_put(datos: DatosOpciones, razon: str) -> IdeaEstrategia:
    if datos.strike_put_atm is not None and datos.prima_put_atm is not None:
        riesgo = f"${datos.prima_put_atm * 100:,.0f} (prima pagada por 1 contrato, 100 acciones)"
        ganancia = f"${(datos.strike_put_atm - datos.prima_put_atm) * 100:,.0f} como máximo (si la acción cae a $0)"
        breakeven = f"${datos.strike_put_atm - datos.prima_put_atm:.2f} (strike − prima pagada)"
    else:
        riesgo = "La prima pagada por el contrato -- depende del strike y vencimiento que elijas."
        ganancia = "Strike − prima pagada, como máximo (si la acción cae a $0)."
        breakeven = "Strike − prima pagada."
    return IdeaEstrategia(
        nombre="Long Put", razon=razon, riesgo_maximo=riesgo,
        ganancia_maxima=ganancia, breakeven=breakeven, notas=_NOTAS_EDUCATIVAS["Long Put"],
    )


# Estrategias de varias patas: SIEMPRE fórmula, nunca un número inventado --
# este módulo solo cotiza un contrato ATM por lado (call y put), no las
# combinaciones de strikes que cada una de estas estrategias necesitaría.
_FORMULAS_MULTI_PATA: dict[str, IdeaEstrategia] = {
    "Covered Call": IdeaEstrategia(
        nombre="Covered Call", razon="",
        riesgo_maximo="El riesgo de tener las 100 acciones (pueden caer a $0), "
                       "reducido por la prima recibida al vender la call.",
        ganancia_maxima="(Strike vendido − precio de compra de la acción) + prima "
                         "recibida -- se limita si la acción sube por encima del strike.",
        breakeven="Precio de compra de la acción − prima recibida.",
        notas=_NOTAS_EDUCATIVAS["Covered Call"],
    ),
    "Long Straddle": IdeaEstrategia(
        nombre="Long Straddle", razon="",
        riesgo_maximo="La suma de las dos primas pagadas (call + put), si el precio no se mueve.",
        ganancia_maxima="Ilimitada al alza; sustancial a la baja (el precio no puede "
                         "bajar de $0).",
        breakeven="Dos puntos: strike + prima total pagada (al alza), y strike − "
                   "prima total pagada (a la baja).",
        notas=_NOTAS_EDUCATIVAS["Long Straddle"],
    ),
    "Iron Condor": IdeaEstrategia(
        nombre="Iron Condor", razon="",
        riesgo_maximo="El ancho del spread (de calls o de puts) menos la prima neta recibida.",
        ganancia_maxima="La prima neta recibida al abrir la posición.",
        breakeven="Dos puntos: strike corto de puts − prima neta, y strike corto de "
                   "calls + prima neta.",
        notas=_NOTAS_EDUCATIVAS["Iron Condor"],
    ),
    "Bear Call Spread": IdeaEstrategia(
        nombre="Bear Call Spread", razon="",
        riesgo_maximo="La diferencia entre los strikes de las dos calls, menos la "
                       "prima neta recibida.",
        ganancia_maxima="La prima neta recibida al abrir la posición.",
        breakeven="Strike vendido + prima neta recibida.",
        notas=_NOTAS_EDUCATIVAS["Bear Call Spread"],
    ),
}


def _construir_idea(nombre: str, razon: str, datos: DatosOpciones) -> IdeaEstrategia:
    if nombre == "Long Call":
        return _idea_long_call(datos, razon)
    if nombre == "Long Put":
        return _idea_long_put(datos, razon)
    plantilla = _FORMULAS_MULTI_PATA[nombre]
    return IdeaEstrategia(
        nombre=plantilla.nombre, razon=razon, riesgo_maximo=plantilla.riesgo_maximo,
        ganancia_maxima=plantilla.ganancia_maxima, breakeven=plantilla.breakeven,
        notas=plantilla.notas,
    )


def generar_ideas(p: Puntuacion, datos: DatosOpciones) -> list[IdeaEstrategia]:
    """Punto de entrada del módulo. Determinístico: la tendencia se lee de
    p.tendencia_cruda (0..3, absoluto), el nivel de IV se aproxima con
    clasificar_iv(). Si no se pudo determinar el nivel de IV (sin datos de
    opciones), devuelve las DOS ideas posibles (alta y baja) en vez de
    adivinar -- igual que el formato condicional del propio spec."""
    tendencia = clasificar_tendencia(p.tendencia_cruda)
    if tendencia == "desconocida":
        return []
    nivel = clasificar_iv(datos.iv_actual, p.vol_historica_anual)
    niveles = [nivel] if nivel else ["baja", "alta"]
    return [
        _construir_idea(*_REGLAS[(tendencia, niv)], datos)
        for niv in niveles
    ]


def movimiento_esperado(p: Puntuacion, datos: DatosOpciones) -> str:
    """Expected move = precio × IV × sqrt(días/365) -- la fórmula estándar
    de cuánto "espera" el mercado de opciones que se mueva el precio hasta
    el vencimiento. Solo se calcula con IV real; nunca se estima con la
    volatilidad histórica disfrazada de esperada."""
    iv, dias, precio = datos.iv_actual, datos.dias_a_vencimiento, p.precio_actual
    if iv is None or dias is None or precio is None:
        return "No disponible sin volatilidad implícita actual."
    movimiento = precio * iv * math.sqrt(dias / 365.0)
    pct = movimiento / precio if precio else 0.0
    return (f"±${movimiento:.2f} (~{pct:.1%}) hacia el vencimiento del "
            f"{datos.vencimiento} ({dias} días)")
