"""Modelo de datos del journal de paper trading -- registra cada
operación abierta (ticker, score del screener ese día, tesis, estrategia
elegida con todos sus números YA calculados por el motor determinístico
de /options, y el motivo) y su resultado al cerrarla.

Ningún LLM interviene: se registra lo que /report y /options ya
calcularon en el momento de abrir, más el resultado real que el usuario
reporta al cerrar -- este sistema no tiene acceso a la cuenta de paper
trading real del usuario, así que no puede inferir el resultado solo
(sería inventar un dato, y este proyecto no inventa datos)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PataJournal:
    tipo: str
    accion: str
    strike: float
    prima: float


@dataclass
class EntradaJournal:
    id: str
    ticker: str
    fecha_apertura: str              # ISO 8601
    score_screener: float | None     # score de la shortlist ese día, o None si no estaba
    posicion_shortlist: int | None   # posición #N de la shortlist ese día, o None
    tesis: str                       # Alcista | Bajista | Neutral | No determinable
    estrategia: str                  # nombre exacto, ej. "Bull Call Spread"
    patas: list[PataJournal]
    costo: float                     # ver telegram_bot/journal_command._costo_apertura
    riesgo_maximo: float | None
    ganancia_maxima: float | None
    probabilidad_exito: float | None
    valor_esperado: float | None
    motivo: str
    estado: str = "abierta"          # abierta | cerrada
    fecha_cierre: str | None = None
    resultado: float | None = None       # P&L real en dólares, reportado por el usuario
    resultado_pct: float | None = None   # resultado / riesgo_maximo
    notas_cierre: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> EntradaJournal:
        d = dict(d)
        patas = [PataJournal(**p) for p in d.pop("patas", [])]
        return EntradaJournal(patas=patas, **d)
