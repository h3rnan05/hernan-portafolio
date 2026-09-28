"""Números que vinieron de afuera. Un ausente no es un cero.

El hunter ya perdió semanas por un `float(v or 0)` que convirtió un
volumen faltante en un cero real. Acá la misma regla, en un solo lugar,
para que noticias, screener e historia no la reimplementen cada uno a
su manera.
"""

from __future__ import annotations


def numero(valor: object) -> float | None:
    """Un int o un float finito. None, bool, texto y NaN no son un dato.

    El cero sí lo es: si la fuente mandó 0, eso es un cero de verdad y
    se conserva. Lo que no se conserva es la ausencia disfrazada.
    """
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        n = float(valor)
        if n != n:  # NaN
            return None
        return n
    return None
