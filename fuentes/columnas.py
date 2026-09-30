"""Contrato de una fuente como columnas del backtest v2.

Una `Fuente` responde, para un símbolo y un instante (el cierre de la
vela de la señal, aware en UTC), un dict de columnas. La pregunta es
siempre "¿qué se SABÍA en ese minuto?": nada publicado después del
instante puede entrar (sesgo de anticipación). Cada columna vale un
dato, None cuando la fuente respondió y no hay evento, o `FALTANTE`.

`nombres()` declara las columnas para que el informe las liste aunque
una corrida no llegue a calcularlas. `Registro` junta fuentes y se lo
pasa el enriquecedor al backtest.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from fuentes.comun import FALTANTE


@runtime_checkable
class Fuente(Protocol):
    nombre: str

    def nombres(self) -> list[str]:
        """Columnas que produce, con su prefijo."""

    def columnas(self, ticker: str, momento: datetime) -> dict:
        """Columnas para (ticker, momento UTC). Nunca levanta por un
        fallo de la fuente: devuelve FALTANTE en cada columna."""


def todas_faltantes(nombres: list[str]) -> dict:
    return {n: FALTANTE for n in nombres}


class Registro:
    def __init__(self, fuentes: list[Fuente] | None = None) -> None:
        self.fuentes: list[Fuente] = []
        for f in fuentes or []:
            self.agregar(f)

    def agregar(self, fuente: Fuente) -> None:
        if not isinstance(fuente, Fuente):
            raise TypeError("no cumple el contrato Fuente")
        repetidas = set(self.nombres()) & set(fuente.nombres())
        if repetidas:
            raise ValueError(f"columnas repetidas: {sorted(repetidas)}")
        self.fuentes.append(fuente)

    def nombres(self) -> list[str]:
        return [n for f in self.fuentes for n in f.nombres()]

    def columnas(self, ticker: str, momento: datetime) -> dict:
        out: dict = {}
        for f in self.fuentes:
            try:
                fila = f.columnas(ticker, momento)
            except Exception:
                # Un bug en una fuente no tumba la corrida ni inventa
                # "sin evento": esa fuente queda FALTANTE en esa fila.
                fila = todas_faltantes(f.nombres())
            for n in f.nombres():
                out[n] = fila.get(n, FALTANTE)
        return out
