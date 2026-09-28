"""Veto de dilución y estantería activa, desde las presentaciones EDGAR.

Complementa el veto por palabras del YAML v2 (`catalizador.veto`): ahí
se mira el titular; aquí, lo que el emisor presentó a la SEC. Reglas
decididas el 2026-09-28 (no se reabren aquí):

    424B* (prospecto de oferta)         -> veto 72 h desde la aceptación
    8-K ítem 3.02 (venta no registrada) -> veto 72 h
    S-1, F-1 y sus enmiendas (/A)       -> veto 30 días
    8-K ítems 1.03 (quiebra), 3.01 (deslistado), 4.02 (no confiar en
        los estados financieros)        -> veto 30 días
    S-3, F-3 (y ASR, /A)                -> NO es veto. Solo la columna
        `edgar_estanteria_activa`: hay una estantería presentada en los
        3 años previos (vigencia de un S-3), que hace posible una oferta
        rápida. Es información, no un filtro.

COLUMNAS (en el instante, todo lo aceptado antes de él):
    edgar_veto                  True | False | FALTANTE
    edgar_veto_motivo           "424B5 2026-08-20" | "8-K 3.02 2026-09-15" | None | FALTANTE
    edgar_veto_horas_restantes  horas hasta que vence el veto | None | FALTANTE
    edgar_estanteria_activa     True | False | FALTANTE
    edgar_estanteria_form       "S-3 2026-03-02" | None | FALTANTE
FALTANTE cuando no hay presentaciones (sin CIK / descarga caída) o cuando
una presentación de veto con aceptación ilegible cae cerca del instante:
sin hora no se sabe si el veto ya venció, y adivinar es lo prohibido.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fuentes.columnas import todas_faltantes
from fuentes.comun import ErrorFuente
from fuentes.edgar import LectorEdgar, Presentacion, es_8k

log = logging.getLogger("fuentes.edgar_veto")

VETO_72H = timedelta(hours=72)
VETO_30D = timedelta(days=30)
ESTANTERIA_VIGENCIA = timedelta(days=3 * 365)

FORMS_72H_PREFIJO = ("424B",)
FORMS_30D = {"S-1", "S-1/A", "F-1", "F-1/A"}
ITEMS_72H = {"3.02"}
ITEMS_30D = {"1.03", "3.01", "4.02"}
FORMS_ESTANTERIA = {"S-3", "S-3/A", "S-3ASR", "F-3", "F-3/A", "F-3ASR"}


def duracion_veto(p: Presentacion) -> tuple[timedelta, str] | None:
    """Cuánto veta esta presentación y por qué, o None si no veta."""
    form = p.form.upper()
    if form.startswith(FORMS_72H_PREFIJO):
        return VETO_72H, f"{p.form} {p.fecha.isoformat()}"
    if form in FORMS_30D:
        return VETO_30D, f"{p.form} {p.fecha.isoformat()}"
    if es_8k(p):
        if any(i in ITEMS_30D for i in p.items):
            item = next(i for i in p.items if i in ITEMS_30D)
            return VETO_30D, f"8-K {item} {p.fecha.isoformat()}"
        if any(i in ITEMS_72H for i in p.items):
            return VETO_72H, f"8-K 3.02 {p.fecha.isoformat()}"
    return None


def es_estanteria(p: Presentacion) -> bool:
    return p.form.upper() in FORMS_ESTANTERIA


def veto_en(pres: list[Presentacion], momento: datetime) -> tuple[bool, str | None, float | None]:
    """(vetado, motivo, horas restantes). Con varias vigentes, la que
    vence más tarde. Levanta ErrorFuente ante una aceptación ilegible
    que podría estar vigente."""
    mejor: tuple[datetime, str] | None = None
    for p in pres:
        d = duracion_veto(p)
        if d is None:
            continue
        duracion, motivo = d
        if p.aceptada is None:
            if momento.date() - duracion - timedelta(days=1) <= p.fecha <= momento.date():
                raise ErrorFuente("aceptacion_ilegible", "edgar_veto")
            continue
        vence = p.aceptada + duracion
        if p.aceptada <= momento < vence and (mejor is None or vence > mejor[0]):
            mejor = (vence, motivo)
    if mejor is None:
        return False, None, None
    return True, mejor[1], round((mejor[0] - momento).total_seconds() / 3600.0, 2)


def estanteria_en(pres: list[Presentacion], momento: datetime) -> tuple[bool, str | None]:
    reciente: Presentacion | None = None
    for p in pres:
        if not es_estanteria(p):
            continue
        cuando = p.aceptada
        if cuando is None:
            # Sin hora, la fecha alcanza: la vigencia se mide en años.
            cuando = datetime.combine(p.fecha, datetime.min.time(), tzinfo=momento.tzinfo) + timedelta(days=1)
        if cuando <= momento < cuando + ESTANTERIA_VIGENCIA and (reciente is None or p.fecha > reciente.fecha):
            reciente = p
    if reciente is None:
        return False, None
    return True, f"{reciente.form} {reciente.fecha.isoformat()}"


class EdgarVeto:
    nombre = "edgar_veto"
    _NOMBRES = ["edgar_veto", "edgar_veto_motivo", "edgar_veto_horas_restantes",
                "edgar_estanteria_activa", "edgar_estanteria_form"]

    def __init__(self, lector: LectorEdgar | None = None) -> None:
        self.lector = lector or LectorEdgar()

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def columnas(self, ticker: str, momento: datetime) -> dict:
        pres = self.lector.presentaciones(ticker)
        if pres is None:
            return todas_faltantes(self._NOMBRES)
        try:
            vetado, motivo, horas = veto_en(pres, momento)
        except ErrorFuente as ex:
            log.warning("edgar_veto: %s (%s)", ex.codigo, ticker)
            return todas_faltantes(self._NOMBRES)
        activa, form = estanteria_en(pres, momento)
        return {"edgar_veto": vetado, "edgar_veto_motivo": motivo, "edgar_veto_horas_restantes": horas,
                "edgar_estanteria_activa": activa, "edgar_estanteria_form": form}
