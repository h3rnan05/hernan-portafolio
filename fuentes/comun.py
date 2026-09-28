"""Valor `FALTANTE`, error de fuente y helpers numéricos.

`FALTANTE` existe porque el proyecto ya perdió semanas por un
`float(v or 0)`: un dato ausente convertido en cero real. Aquí la
ausencia tiene su propio tipo, no se compara igual a nada y no se puede
usar en aritmética (levanta `TypeError` en vez de dar un número).
"""

from __future__ import annotations

from typing import Any


class Faltante:
    """Singleton: el dato no se pudo obtener o no es fiable.

    No es None (None es "la fuente dijo que no hay") ni False ni 0. Es
    falsy a propósito para que `if valor:` no lo confunda con un dato,
    pero cualquier operación aritmética levanta."""

    _instancia: "Faltante | None" = None

    def __new__(cls) -> "Faltante":
        if cls._instancia is None:
            cls._instancia = super().__new__(cls)
        return cls._instancia

    def __repr__(self) -> str:
        return "FALTANTE"

    def __bool__(self) -> bool:
        return False

    def __eq__(self, otro: object) -> bool:
        return otro is self

    def __hash__(self) -> int:
        return id(self)

    def _prohibido(self, *_: Any) -> Any:
        raise TypeError("FALTANTE no es un número: un dato ausente no se opera")

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _prohibido
    __truediv__ = __rtruediv__ = __lt__ = __le__ = __gt__ = __ge__ = _prohibido
    __float__ = __int__ = _prohibido

    def __reduce__(self):
        return (Faltante, ())


FALTANTE = Faltante()


def es_faltante(valor: object) -> bool:
    return valor is FALTANTE


class ErrorFuente(Exception):
    """Fallo de una fuente. `codigo` es una etiqueta corta (`red`,
    `auth`, `http_429`, `cuerpo`, `sin_credenciales`, `zona_horaria`).
    Nunca lleva el texto de la excepción original: puede traer una URL
    con la clave."""

    def __init__(self, codigo: str, fuente: str = "") -> None:
        self.codigo = codigo
        self.fuente = fuente
        super().__init__(f"{fuente}:{codigo}" if fuente else codigo)


def numero(valor: object) -> float | None:
    """Un int o float finito, o None. bool, texto y NaN no son un dato.
    El cero sí se conserva: si la fuente mandó 0, es un cero real."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        n = float(valor)
        return None if n != n else n
    return None


def entero(valor: object) -> int | None:
    n = numero(valor)
    if n is None or not n.is_integer():
        return None
    return int(n)


def serializable(valor: Any) -> Any:
    """Para JSON/CSV: FALTANTE -> "FALTANTE"; el resto igual. Se usa en
    el enriquecedor y en la grabadora, para que la ausencia se lea como
    ausencia en cualquier informe."""
    if valor is FALTANTE:
        return "FALTANTE"
    if isinstance(valor, dict):
        return {k: serializable(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [serializable(v) for v in valor]
    return valor
