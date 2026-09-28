"""Registro de subcomandos de `python -m fuentes`.

Vive aparte de `__main__.py` a propósito. Con `python -m fuentes`, ese
archivo corre como el módulo `__main__`; si las fuentes hicieran
`from fuentes import __main__`, importarían OTRA copia (`fuentes.__main__`)
con su propio diccionario, y el registro caería donde `main()` no mira
(así falló en el VPS el 2026-09-28: "Disponibles: (ninguna)"). Aquí hay
un solo módulo y un solo diccionario para todos.
"""

from __future__ import annotations

from typing import Callable

COMANDOS: dict[str, Callable[[list[str]], int]] = {}


def registrar(nombre: str, fn: Callable[[list[str]], int]) -> None:
    COMANDOS[nombre] = fn
