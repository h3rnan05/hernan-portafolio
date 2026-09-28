"""Cuántas consultas le hace el sistema a Alpaca por minuto.

Paquete neutral a propósito: lo importan el host de DATOS del hunter
(`momentum_hunter/data/alpaca_datos.py`), el cliente paper del ejecutor
(`momentum_paper_trader/alpaca_client.py`) y el panel. No sabe de
órdenes ni de señales: solo cuenta y avisa. Ver `contador.py`.
"""
