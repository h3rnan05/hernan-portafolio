"""Configuración del paper trader -- separado de momentum_hunter/config.py
a propósito (misma filosofía que todo el repo: cada módulo con su propia
config, cero acoplamiento con la lógica de decisión de momentum_hunter).

Este sistema es 100% paper trading -- nunca dinero real. El endpoint de
Alpaca está hardcodeado a paper en `alpaca_client.py`, no es un parámetro
de esta config ni de ningún otro lugar: ver el README para qué
requeriría (y con cuánto escrutinio) pasar a una cuenta real algún día."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperTraderConfig:
    # Cuánto arriesgar por operación (dólares de la cuenta PAPER, nunca
    # reales). El tamaño de posición se deriva de esto y de la distancia
    # al stop que ya calculó momentum_hunter -- nunca un número de
    # acciones fijo, para que el riesgo real de cada trade sea siempre
    # el mismo, sin importar qué tan ajustado esté el stop.
    riesgo_dolares_por_operacion: float = 100.0
    # Si el riesgo pedido no alcanza para comprar ni 1 acción entera con
    # el stop de esta señal (stop muy amplio, riesgo muy chico), se omite
    # la orden en vez de redondear hacia arriba y arriesgar de más.
    minimo_acciones: int = 1

    def validar(self) -> None:
        if self.riesgo_dolares_por_operacion <= 0:
            raise ValueError("riesgo_dolares_por_operacion debe ser > 0")
        if self.minimo_acciones < 1:
            raise ValueError("minimo_acciones debe ser >= 1")


CONFIG = PaperTraderConfig()
