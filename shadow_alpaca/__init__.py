"""Sombra de solo lectura contra el host de DATOS de Alpaca.

No forma parte del camino que busca señales ni del que coloca órdenes.
El hunter y el ejecutor no importan este paquete: si lo hicieran, una
telemetría de comparación podría colarse en la watchlist o en una orden.
La prueba de aislamiento fija esa dirección.

Lo que sí se reutiliza, en solo lectura, es el detector de catalizadores
(`momentum_hunter.catalysts.detector`): las mismas keywords y los mismos
umbrales. Este paquete no los copia y no los modifica.

Host único: `https://data.alpaca.markets`. No hay parámetro para apuntar
a otro. Un campo que la fuente no manda queda en null; no se rellena con
cero.
"""

from __future__ import annotations

__all__ = ["DATA_BASE"]

DATA_BASE = "https://data.alpaca.markets"
