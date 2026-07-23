"""Contratos del AI Analyst de noticias."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntradaShortlist:
    """Una fila de la shortlist del screener, tal como quedó persistida en
    shortlist_hoy.json -- lo mínimo que este módulo necesita para saber
    POR QUÉ el modelo eligió esta empresa (se lo pasa al LLM como contexto,
    no lo inventa)."""
    ticker: str
    posicion: int          # 1-indexado: "#8 de 20"
    score: float
    sector: str | None
    nombre: str | None
    industria: str | None
    sub_scores: dict[str, float]


@dataclass(frozen=True)
class Mencion:
    """Resultado determinístico de detectar_mencion(): si un titular
    menciona (o no) a una empresa de la shortlist."""
    entrada: EntradaShortlist
    coincidio_por: str      # "nombre" | "ticker" -- auditable, no una caja negra


@dataclass(frozen=True)
class Explicacion:
    texto: str
    tono: str               # "positivo" | "negativo" | "neutral" | "incierto"
    nivel_importancia: int  # 1-5


@dataclass(frozen=True)
class NoticiaRelevante:
    titular: str
    mencion: Mencion
    explicacion: Explicacion | None  # None si se topó el tope de MAX_EXPLICACIONES


@dataclass(frozen=True)
class ResumenNoticias:
    """Resumen agregado de varios titulares -- para telegram_bot/
    report_command.py, que ya no muestra una lista de titulares sueltos
    por defecto, sino este resumen de una frase + hasta 3 hechos
    concretos (ver news_analyst.explicador.resumir_noticias)."""
    resumen: str
    puntos: list[str]
