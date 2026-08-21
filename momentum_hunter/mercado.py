"""¿El mercado general está ayudando o estorbando hoy?

Hasta el 2026-08-21 el bot analizaba cada acción como si el resto del
mercado no existiera: no había una sola referencia a un índice en todo
`momentum_hunter/`. Eso significa que en una sesión en la que todo se
está cayendo, una ruptura al alza se evaluaba exactamente igual que en
un día tranquilo -- cuando en la práctica es de lo primero que mira un
trader de momentum antes de entrar (una marea que baja hunde también a
las señales buenas).

NINGÚN UMBRAL NUEVO. La prueba es la MISMA que el bot ya aplica a cada
acción para decidir si está fuerte -- precio contra su VWAP de la sesión
y contra su EMA9 (`factors/intradia`) -- solo que aplicada al índice.
Reutilizar el criterio en el que el sistema ya confía evita inventar un
número "a ojo", que es exactamente lo que produjo el
`score_minimo_alerta` inalcanzable de 85 (ver `config.py`).

DECISIÓN EXPLÍCITA DEL USUARIO (2026-08-21): esto NO bloquea nada. Es
"suave": mide el clima, lo pasa como un dato más a la capa de decisión
con IA (`momentum_paper_trader/ia_decision.py`) y lo menciona en el
mensaje. La razón es que el sistema todavía no tiene ni una operación
medida, y un filtro duro reduciría las operaciones justo cuando lo que
más falta hace es acumular historial. Cuando existan datos, `replay.py`
podrá decidir con evidencia si conviene endurecerlo.

Mejor esfuerzo: si el índice no se puede leer, el veredicto es
`desconocido` y todo sigue igual que antes de este módulo -- nunca se
inventa un clima que no se midió, y nunca una falla acá tumba la
corrida."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from momentum_hunter.data.provider import DataProvider
from momentum_hunter.factors import intradia

log = logging.getLogger("momentum_hunter.mercado")

# SPY (S&P 500) es el termómetro estándar de "el mercado" para acciones
# de EEUU y el ETF más líquido que existe -- si Yahoo tiene datos
# intradía de algo, los tiene de este.
TICKER_INDICE = "SPY"

FAVORABLE = "favorable"
DEBIL = "debil"
DESCONOCIDO = "desconocido"


@dataclass(frozen=True)
class ClimaMercado:
    """`veredicto` es uno de FAVORABLE/DEBIL/DESCONOCIDO. Los campos
    numéricos quedan para la auditoría y para explicarlo en el mensaje
    -- nunca se muestran crudos al usuario (ver `report.py`: prohibido
    mostrar indicadores)."""
    veredicto: str
    precio: float | None = None
    vwap: float | None = None
    ema9: float | None = None

    @property
    def favorable(self) -> bool:
        return self.veredicto == FAVORABLE

    @property
    def debil(self) -> bool:
        return self.veredicto == DEBIL

    def frase(self) -> str:
        """Una línea en lenguaje humano para el mensaje de Telegram y
        para el paquete de evidencia de la IA."""
        if self.veredicto == FAVORABLE:
            return "El mercado en general viene sostenido hoy, así que no está remando en contra."
        if self.veredicto == DEBIL:
            return ("El mercado en general viene flojo hoy -- las rupturas al alza fallan "
                    "más seguido cuando la marea va en contra.")
        return "No pude leer cómo viene el mercado en general hoy."


def evaluar(provider: DataProvider, ticker: str = TICKER_INDICE) -> ClimaMercado:
    """Lee el índice y aplica la misma prueba de fuerza que el bot ya usa
    para una acción: por encima de su VWAP y de su EMA9 = favorable.

    Se exige estar por encima de AMBOS a propósito: el VWAP dice "los que
    compraron hoy están en ganancia" y la EMA9 dice "la tendencia corta
    sigue apuntando arriba". Con uno solo, un rebote débil dentro de un
    día malo se leería como mercado sano."""
    try:
        barras = provider.barras_intradia([ticker])
    except Exception as ex:
        log.warning("no se pudo leer el clima de mercado (%s): %s", ticker, ex)
        return ClimaMercado(DESCONOCIDO)

    bi = barras.get(ticker)
    if bi is None or not bi.close:
        log.info("sin datos intradía de %s -- clima de mercado desconocido", ticker)
        return ClimaMercado(DESCONOCIDO)

    hoy = intradia.barras_de_hoy(bi)
    if not hoy.close:
        return ClimaMercado(DESCONOCIDO)

    precio = hoy.close[-1]
    vwap = intradia.vwap_real(hoy)
    ema9 = intradia.ema9_intradia(hoy)
    if vwap is None or ema9 is None:
        # Muy temprano en la sesión todavía no hay velas suficientes --
        # eso es "no se sabe", no "está mal".
        return ClimaMercado(DESCONOCIDO, precio=precio, vwap=vwap, ema9=ema9)

    veredicto = FAVORABLE if (precio > vwap and precio > ema9) else DEBIL
    log.info("clima de mercado (%s): %s", ticker, veredicto)
    return ClimaMercado(veredicto, precio=precio, vwap=vwap, ema9=ema9)
