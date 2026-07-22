"""Operaciones puras sobre el journal -- trabajan sobre listas de
EntradaJournal en memoria, sin tocar red ni GitHub (eso lo maneja
telegram_bot/journal_command.py, igual que ya hace wizards_inbox.json en
telegram_bot/app.py: este servicio no tiene disco persistente en Render,
así que el estado se guarda committeando al repo)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from journal.models import EntradaJournal, PataJournal


def nuevo_id() -> str:
    return uuid.uuid4().hex[:10]


def abrir_entrada(
    ticker: str, score_screener: float | None, posicion_shortlist: int | None,
    tesis: str, estrategia: str, patas: list[dict], costo: float,
    riesgo_maximo: float | None, ganancia_maxima: float | None,
    probabilidad_exito: float | None, valor_esperado: float | None, motivo: str,
) -> EntradaJournal:
    return EntradaJournal(
        id=nuevo_id(),
        ticker=ticker.upper(),
        fecha_apertura=datetime.now(UTC).isoformat(timespec="seconds"),
        score_screener=score_screener,
        posicion_shortlist=posicion_shortlist,
        tesis=tesis,
        estrategia=estrategia,
        patas=[PataJournal(**p) for p in patas],
        costo=costo,
        riesgo_maximo=riesgo_maximo,
        ganancia_maxima=ganancia_maxima,
        probabilidad_exito=probabilidad_exito,
        valor_esperado=valor_esperado,
        motivo=motivo,
    )


def cerrar_entrada(
    entradas: list[EntradaJournal], entry_id: str, resultado: float, notas: str | None = None,
) -> EntradaJournal | None:
    """Marca la entrada como cerrada IN PLACE (muta el objeto dentro de
    `entradas`) y la devuelve, o None si no existe / ya estaba cerrada."""
    for e in entradas:
        if e.id == entry_id and e.estado == "abierta":
            e.estado = "cerrada"
            e.fecha_cierre = datetime.now(UTC).isoformat(timespec="seconds")
            e.resultado = resultado
            e.resultado_pct = (resultado / e.riesgo_maximo) if e.riesgo_maximo else None
            e.notas_cierre = notas
            return e
    return None


def encontrar_abierta_por_ticker(entradas: list[EntradaJournal], ticker: str) -> EntradaJournal | None:
    """La más reciente abierta para ese ticker -- si el usuario tiene
    varias posiciones abiertas del mismo ticker, cierra la última que
    abrió (lo más común en la práctica; si necesita cerrar una distinta
    debe pasar su id directamente vía una interfaz futura)."""
    candidatas = [e for e in entradas if e.ticker == ticker.upper() and e.estado == "abierta"]
    return max(candidatas, key=lambda e: e.fecha_apertura) if candidatas else None
