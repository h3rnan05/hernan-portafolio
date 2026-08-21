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
    # Techo de posiciones/órdenes vivas simultáneas -- guardarraíl
    # DETERMINISTA del executor (se cuenta contra la cuenta real de
    # Alpaca, no contra un registro local), nunca una decisión de la IA.
    # Un trader disciplinado con $5,000 no debería estar en 8 jugadas a
    # la vez; 5 ya es generoso.
    maximo_posiciones_abiertas: int = 5
    # Antigüedad máxima de los niveles (entrada/stop/objetivo) para
    # colocar una orden con ellos. El precio de entrada se congela cuando
    # momentum_hunter evalúa la señal, pero la orden se coloca después:
    # en el escaneo completo, hasta ~9 minutos más tarde (el paso del
    # trader corre al final de un job de ~11 min). Y si una corrida del
    # trader falla, la señal queda TRIGGERED sin revisar y se conserva
    # varios días (ver `watchlist.RETENCION_DIAS_TERMINALES`) -- sin este
    # tope, la corrida siguiente colocaría una orden con el precio de
    # hace días. En momentum eso no es un detalle: es comprar a un precio
    # que ya no existe.
    #
    # 15 minutos = 3x la cadencia del re-chequeo de watchlist (cada 5
    # min, ver momentum_hunter_watchlist.yml). Tolera un par de corridas
    # perdidas sin tolerar un precio rancio. No se pierde la operación:
    # el siguiente re-chequeo recalcula los niveles y la orden se coloca
    # ahí, con precios de verdad.
    minutos_maximos_niveles: float = 15.0
    # Liquidar todo antes del cierre en vez de dejar posiciones abiertas
    # de un día para otro (ver `cierre.py`). Las patas de salida del
    # bracket son órdenes "del día": si no se ejecutan, se cancelan al
    # cerrar y la posición queda SIN stop y SIN objetivo durante la
    # noche. Además, este bot evalúa movimientos intradía -- mantener
    # una posición hasta mañana es una apuesta distinta (huecos de
    # apertura, noticias nocturnas) que nada en este sistema analiza.
    cerrar_antes_del_cierre: bool = True
    # 10 min antes de las 20:00 UTC = 19:50 UTC (15:50 ET en verano).
    # El re-chequeo corre cada 5 min, así que siempre cae al menos una
    # corrida dentro de la ventana.
    minutos_antes_del_cierre: int = 10
    # Si la IA decide aguantar una posición hasta mañana, se le pone un
    # stop nuevo que sobrevive a la noche, a este porcentaje por debajo
    # del precio actual. 3% es deliberadamente holgado: un stop nocturno
    # demasiado ajustado se ejecuta con cualquier ruido de la apertura,
    # que es justo cuando más ruido hay. No protege contra un hueco (ver
    # `alpaca_client.colocar_stop_protector`).
    colchon_stop_nocturno: float = 0.03

    def validar(self) -> None:
        if self.riesgo_dolares_por_operacion <= 0:
            raise ValueError("riesgo_dolares_por_operacion debe ser > 0")
        if self.minimo_acciones < 1:
            raise ValueError("minimo_acciones debe ser >= 1")
        if self.maximo_posiciones_abiertas < 1:
            raise ValueError("maximo_posiciones_abiertas debe ser >= 1")
        if self.minutos_maximos_niveles <= 0:
            raise ValueError("minutos_maximos_niveles debe ser > 0")
        if not 0 < self.minutos_antes_del_cierre < 390:
            # 390 min = la sesión regular completa (6,5 h): más que eso
            # significaría "cerrar antes de abrir".
            raise ValueError("minutos_antes_del_cierre debe estar entre 1 y 389")
        if not 0 < self.colchon_stop_nocturno < 1:
            raise ValueError("colchon_stop_nocturno debe estar entre 0 y 1 (fracción)")


CONFIG = PaperTraderConfig()
