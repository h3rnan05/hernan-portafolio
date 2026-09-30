"""Backtest de la estrategia v2 (config/estrategia_v2.yaml).

Extiende el backtest de #203 con las reglas v2. Offline: no escribe la
watchlist, no ordena nada, no importa el ejecutor. Las reglas NO se
copian: vienen de `estrategia_v2/` (la misma implementación que usarán
el hunter y el ejecutor). Ver `motor.py` para lo que no se puede
reproducir y cómo se reporta.
"""
