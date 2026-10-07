"""Volumen del filtro de universo cuando la diaria vino de IEX.

POR QUÉ (2026-10-07, aprobado por el dueño). Con el respaldo IEX de #256
(SIP da 403 de suscripción), las diarias del escaneo llegan de IEX, que
ve ~2-10 % del volumen consolidado. El filtro de volumen del universo
(`volumen_promedio_min` / `volumen_promedio_min_large_cap`, promedio de
20 sesiones) sigue calibrado para el consolidado, así que casi nada pasa:
el 7-oct a las ~10:00 ET quedaron fuera por `vol_bajo_large` 891 de 991
(220 el 6-oct con SIP) y el embudo se vació.

QUÉ HACE. Solo para los tickers cuya diaria contestó IEX: el volumen del
filtro sale de las diarias SIP con `end` = ahora - 16 min (el plan gratis
deja SIP con 15 min de retraso; -14 da 403). Misma definición y ventana
(`run._volumen_promedio`, 20 sesiones), solo cambia la fuente. Precios,
gatillos, factores e intradía siguen en IEX. Con SIP normal no cambia
nada: ni se pide la diaria retrasada.

FAIL-CLOSED. Si la diaria retrasada falla entera o un ticker no vuelve,
ese ticker queda SIN DATO de volumen (`vol_sin_dato_sip_retrasado` en
`rechazos_universo`) y no se evalúa. No se usa el volumen IEX como si
fuera consolidado ni se rellena con 0. Con el flag apagado
(`MOMENTUM_VOLUMEN_SIP_RETRASADO=0`) vuelve el comportamiento de #256
(volumen IEX), pero rotulado `volumen_fuente=iex`.

TELEMETRÍA. `datos.volumen_fuente` (`sip`, `sip_retrasado`, `iex`, una
combinación con `+`, o `sin_dato`), `datos.volumen_sin_dato`,
`datos.volumen_retrasado_end` y `datos.volumen_retrasado_codigo`.

No toca umbrales, keywords, sizing, límites, política IA ni el websocket.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca
from momentum_hunter.models import Barras

log = logging.getLogger("momentum_hunter.data.volumen_retrasado")

ENV_VOLUMEN_SIP_RETRASADO = "MOMENTUM_VOLUMEN_SIP_RETRASADO"
FUENTE_SIP = "sip"
FUENTE_SIP_RETRASADO = "sip_retrasado"
FUENTE_IEX = "iex"
SIN_DATO = "sin_dato"
MOTIVO_SIN_DATO = "vol_sin_dato_sip_retrasado"


def habilitado() -> bool:
    """`MOMENTUM_VOLUMEN_SIP_RETRASADO` (default 1). Solo `0/false/no/off` lo apaga."""
    valor = os.environ.get(ENV_VOLUMEN_SIP_RETRASADO, "1").strip().lower()
    return valor not in ("0", "false", "no", "off")


@dataclass
class VolumenFiltro:
    """Qué serie de volumen usa el filtro para cada ticker.

    `reemplazos`: ticker -> diaria SIP retrasada (solo se mira el volumen).
    `sin_dato`: tickers con diaria IEX y sin volumen SIP retrasado: no se
    evalúan. Un ticker en ninguno de los dos usa su propia diaria."""

    reemplazos: dict[str, Barras] = field(default_factory=dict)
    sin_dato: set[str] = field(default_factory=set)
    fuente_por_ticker: dict[str, str] = field(default_factory=dict)
    codigo: str | None = None
    end: str | None = None

    def serie(self, ticker: str, propia: Barras) -> Barras | None:
        """Serie para el volumen promedio, o None si es sin dato."""
        if ticker in self.sin_dato:
            return None
        return self.reemplazos.get(ticker, propia)

    @property
    def etiqueta(self) -> str | None:
        usadas = set(self.fuente_por_ticker.values())
        if not usadas:
            return SIN_DATO if self.sin_dato else None
        orden = {FUENTE_SIP: 0, FUENTE_SIP_RETRASADO: 1, FUENTE_IEX: 2}
        return "+".join(sorted(usadas, key=lambda f: (orden.get(f, 9), f)))

    def anotar(self, metricas) -> None:
        if metricas is None:
            return
        metricas.volumen_fuente = self.etiqueta
        medido = bool(self.fuente_por_ticker or self.sin_dato)
        metricas.volumen_sin_dato = len(self.sin_dato) if medido else None
        metricas.volumen_retrasado_end = self.end
        metricas.volumen_retrasado_codigo = self.codigo


def preparar(provider, barras: dict[str, Barras]) -> VolumenFiltro:
    """Nunca lanza. Sin dato de feed (Yahoo, dobles de prueba) devuelve un
    `VolumenFiltro` vacío: el filtro usa la diaria de siempre."""
    fn_feed = getattr(provider, "feed_diario_por_ticker", None)
    if provider is None or not callable(fn_feed):
        return VolumenFiltro()
    try:
        feeds = fn_feed() or {}
    except Exception as ex:  # noqa: BLE001 -- medir no puede tumbar el escaneo
        log.warning("volumen: no se pudo leer el feed de la diaria (%s); filtro sin cambio",
                    type(ex).__name__)
        return VolumenFiltro()

    out = VolumenFiltro()
    de_iex: list[str] = []
    for t in barras:
        feed = feeds.get(t)
        if feed is None:
            continue
        if feed == FUENTE_SIP:
            out.fuente_por_ticker[t] = FUENTE_SIP
        else:
            # `iex` o `sip+iex`: la serie no es consolidada entera.
            de_iex.append(t)
    if not de_iex:
        return out

    if not habilitado():
        for t in de_iex:
            out.fuente_por_ticker[t] = FUENTE_IEX
        log.warning(
            "volumen: %d ticker(s) con diaria IEX y %s=0: el filtro usa volumen IEX "
            "(no consolidado) -- volumen_fuente=iex", len(de_iex), ENV_VOLUMEN_SIP_RETRASADO,
        )
        return out

    retrasadas: dict[str, Barras] = {}
    try:
        retrasadas, fallidos, codigo, fin = provider.volumen_sip_retrasado(de_iex)
        out.codigo = codigo if fallidos else None
        if fin is not None:
            out.end = fin.isoformat(timespec="seconds")
    except ErrorDatosAlpaca as ex:
        out.codigo = ex.codigo
        retrasadas = {}
    except Exception as ex:  # noqa: BLE001 -- fail-closed: sin dato, no IEX
        out.codigo = type(ex).__name__
        retrasadas = {}

    for t in de_iex:
        b = retrasadas.get(t) if isinstance(retrasadas, dict) else None
        if b is not None and b.volume:
            out.reemplazos[t] = b
            out.fuente_por_ticker[t] = FUENTE_SIP_RETRASADO
        else:
            out.sin_dato.add(t)
    log.info(
        "volumen del filtro: volumen_fuente=%s sip_retrasado=%d sin_dato=%d end=%s codigo=%s",
        out.etiqueta, len(out.reemplazos), len(out.sin_dato), out.end, out.codigo,
    )
    if out.sin_dato:
        log.warning(
            "volumen: %d ticker(s) con diaria IEX sin volumen SIP retrasado (%s): quedan sin dato "
            "y no se evalúan (%s)", len(out.sin_dato), out.codigo or "sin_serie",
            ", ".join(sorted(out.sin_dato)[:8]),
        )
    return out
