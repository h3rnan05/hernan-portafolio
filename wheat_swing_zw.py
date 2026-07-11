#!/usr/bin/env python3
"""
wheat_swing_zw.py — Sigue la operación swing de trigo (análisis del 11-jul-2026).

CÓMO FUNCIONA
  Señal:     ZW=F (futuro de trigo SRW en Yahoo Finance, ~15 min de retraso;
             suficiente porque la operación es en gráfico DIARIO).
  Ejecución: WEAT (ETF de trigo Teucrium) en Alpaca PAPER, porque Alpaca
             no opera futuros. WEAT sigue al trigo con precio de ~$5/acción,
             perfecto para la cuenta paper de $1,000.

  Diseñado para correr por cron / GitHub Actions cada 15 min en horario de
  mercado. CADA CORRIDA = UN CHEQUEO Y TERMINA. Sin loops infinitos ni
  schedulers en memoria (eso fue lo que mató al bot anterior).

  Estado persistente en wheat_state.json (mismo directorio).
  Para reiniciar la operación desde cero: borra ese archivo.

PLAN CODIFICADO (niveles en centavos de ZW sep-26) — ver conversación:
  ESPERANDO:
    - ZW toca <= 628            -> pasa a ZONA_TOCADA (llegó a la zona de compra)
    - ZW >= 651 en 2 lecturas
      seguidas y ZW <= 658      -> COMPRA por ruptura (2 lecturas = anti-barrida;
                                   tope 658 = no perseguir precio de euforia)
    - ZW < 612                  -> setup cancelado (se metió de vuelta a la caja)
  ZONA_TOCADA:
    - ZW recupera a 625–634     -> COMPRA por retroceso ("la zona aguantó")
    - ZW > 634 sin comprar      -> se escapó; regresa a ESPERANDO (queda la ruptura)
    - ZW < 612                  -> cancelado (la zona falló)
  EN_POSICION:
    - ZW < stop (inicia en 607) -> vende TODO                       [terminal]
    - ZW >= 646 (TP1)           -> vende 1/3, stop sube a la entrada (breakeven)
    - ZW >= 668 (TP2)           -> vende 1/3, stop sube a 645
    - ZW >= 684                 -> vende el resto (antes del muro de 688) [terminal]
  Además: stop "paracaídas" REAL (orden GTC en WEAT) por si el cron muere.
  El stop solo se mueve HACIA ARRIBA, nunca en contra.

SIZING
  Riesgo por operación = RISK_PCT del equity (1% de $1,000 = $10).
  qty = riesgo_usd / (precio_weat * distancia_stop_%), con tope de notional.

VARIABLES DE ENTORNO
  ALPACA_API_KEY, ALPACA_SECRET_KEY   (obligatorias, cuenta PAPER)
  DISCORD_WEBHOOK                     (opcional, para avisos)
  DRY_RUN=1                           (opcional: simula sin mandar órdenes)

OJO: estos niveles son para ESTA operación específica. Cuando el escenario
cambie (se ejecute, se cancele, o el mercado haga otra cosa), este script
se archiva o se le cargan niveles nuevos. No es una estrategia perpetua.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yfinance as yf
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (GetOrdersRequest, MarketOrderRequest,
                                     StopOrderRequest)

# ============================== CONFIGURACIÓN ==============================

ZW_TICKER = "ZW=F"        # futuro de trigo SRW (Yahoo Finance)
ETF = "WEAT"              # vehículo de ejecución en Alpaca

# --- Niveles del plan (centavos de ZW) ---
ZONA_TECHO       = 628.0  # tocar aquí o menos = llegó a la zona de compra
ZONA_CONFIRMA    = 625.0  # recuperar este nivel tras tocar la zona = "aguantó"
ZONA_MAX_COMPRA  = 634.0  # arriba de esto ya no se compra el retroceso
SETUP_INVALIDO   = 612.0  # debajo de esto la tesis de ruptura murió
RUPTURA          = 651.0  # ataque a la pared de 650
RUPTURA_LECTURAS = 2      # lecturas consecutivas >= 651 para confirmar
NO_PERSEGUIR     = 658.0  # arriba de esto no se compra ruptura (extendido)
STOP_INICIAL     = 607.0
TP1              = 646.0  # primera parcial, antes de la pared de 650
TP2              = 668.0  # segunda parcial, antes del measured move ~675
SALIDA_FINAL     = 684.0  # fuera de todo antes del muro de 688.25
STOP_TRAS_TP2    = 645.0

# --- Riesgo ---
RISK_PCT         = 0.01   # 1% del equity por operación
MAX_NOTIONAL_PCT = 0.40   # nunca meter más del 40% de la cuenta en la posición
PARACAIDAS_EXTRA = 0.995  # el stop GTC en WEAT va 0.5% más abajo que el lógico
                          # (el stop "de verdad" lo maneja este script vía ZW;
                          #  la orden GTC es red de seguridad si el cron muere)

STATE_FILE = Path(__file__).resolve().parent / "wheat_state.json"
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# ============================== UTILIDADES ==============================


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def notify(msg: str) -> None:
    """Imprime al log y, si hay webhook de Discord, avisa ahí también."""
    linea = f"[{ahora()}] {msg}"
    print(linea, flush=True)
    hook = os.getenv("DISCORD_WEBHOOK")
    if hook:
        try:
            requests.post(hook, json={"content": f"🌾 {linea}"}, timeout=10)
        except Exception as e:
            print(f"  (aviso a Discord falló: {e})", flush=True)


def cargar_estado() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "estado": "ESPERANDO",
        "confirmaciones_ruptura": 0,
        "qty_inicial": 0,
        "qty_actual": 0,
        "entrada_zw": None,
        "entrada_weat": None,
        "stop_zw": STOP_INICIAL,
        "tp1_hecho": False,
        "tp2_hecho": False,
        "eventos": [],
    }


def guardar_estado(st: dict, evento: str | None = None) -> None:
    if evento:
        st["eventos"].append({"ts": ahora(), "evento": evento})
        st["eventos"] = st["eventos"][-100:]
    STATE_FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def precio_zw() -> float | None:
    """Último precio de ZW=F vía Yahoo, con reintentos (por los 429)."""
    for intento in range(3):
        try:
            t = yf.Ticker(ZW_TICKER)
            p = None
            try:
                fi = t.fast_info
                p = getattr(fi, "last_price", None) or fi.get("lastPrice")
            except Exception:
                p = None
            if not p:
                h = t.history(period="1d", interval="5m")
                if len(h):
                    p = float(h["Close"].iloc[-1])
            if p and p > 0:
                return round(float(p), 2)
        except Exception as e:
            print(f"  yfinance intento {intento + 1} falló: {e}", flush=True)
        time.sleep(5 * (intento + 1))
    return None


# ============================== ALPACA ==============================


def clientes():
    key = os.getenv("ALPACA_API_KEY")
    sec = os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        print("ERROR: faltan ALPACA_API_KEY / ALPACA_SECRET_KEY en el entorno.")
        sys.exit(1)
    # paper=True es regla dura: este script jamás toca la cuenta live.
    return TradingClient(key, sec, paper=True), StockHistoricalDataClient(key, sec)


def precio_weat(data_client) -> float:
    req = StockLatestTradeRequest(symbol_or_symbols=ETF)
    return float(data_client.get_stock_latest_trade(req)[ETF].price)


def cancelar_ordenes_weat(trading) -> None:
    """Cancela órdenes abiertas de WEAT (necesario antes de vender:
    el stop paracaídas 'apartaría' las acciones y la venta sería rechazada)."""
    if DRY_RUN:
        print("  [DRY_RUN] cancelaría órdenes abiertas de WEAT", flush=True)
        return
    abiertas = trading.get_orders(
        GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[ETF])
    )
    for o in abiertas:
        trading.cancel_order_by_id(o.id)
    if abiertas:
        time.sleep(2)  # dar tiempo a que las cancelaciones asienten


def poner_paracaidas(trading, st: dict) -> None:
    """Orden stop GTC real en WEAT como red de seguridad si el cron muere.
    Nivel = entrada_weat escalada al stop lógico de ZW, un pelín más abajo."""
    if st["qty_actual"] <= 0:
        return
    nivel = round(
        st["entrada_weat"] * (st["stop_zw"] / st["entrada_zw"]) * PARACAIDAS_EXTRA, 2
    )
    if DRY_RUN:
        print(f"  [DRY_RUN] pondría stop GTC de {st['qty_actual']} WEAT en ${nivel}",
              flush=True)
        return
    trading.submit_order(StopOrderRequest(
        symbol=ETF, qty=st["qty_actual"], side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC, stop_price=nivel,
    ))
    print(f"  paracaídas: stop GTC de {st['qty_actual']} WEAT en ${nivel}", flush=True)


def orden_mercado(trading, side: OrderSide, qty: int) -> None:
    if DRY_RUN:
        print(f"  [DRY_RUN] orden de mercado: {side.value} {qty} {ETF}", flush=True)
        return
    trading.submit_order(MarketOrderRequest(
        symbol=ETF, qty=qty, side=side, time_in_force=TimeInForce.DAY,
    ))
    time.sleep(3)  # esperar el fill (ETF líquido, llena casi al instante)


def calcular_qty(equity: float, p_weat: float, zw: float) -> int:
    """Tamaño por riesgo: que tocar el stop cueste ~RISK_PCT de la cuenta."""
    dist_stop = (zw - STOP_INICIAL) / zw          # ej. (624-607)/624 ≈ 2.7%
    riesgo_usd = equity * RISK_PCT                # ej. $1,000 * 1% = $10
    riesgo_por_accion = p_weat * dist_stop        # ej. $5.00 * 2.7% ≈ $0.135
    qty = int(riesgo_usd / riesgo_por_accion)     # ej. ≈ 74 acciones
    tope = int(equity * MAX_NOTIONAL_PCT / p_weat)
    return max(0, min(qty, tope))


# ============================== ACCIONES ==============================


def comprar(trading, data_client, st: dict, zw: float, motivo: str) -> None:
    equity = float(trading.get_account().equity)
    p_weat = precio_weat(data_client)
    qty = calcular_qty(equity, p_weat, zw)
    if qty < 1:
        notify(f"Señal de compra ({motivo}) pero el sizing dio 0 acciones. "
               f"Equity ${equity:.2f}, WEAT ${p_weat:.2f}. Revisa la cuenta.")
        return
    orden_mercado(trading, OrderSide.BUY, qty)

    # precio real de entrada (si no hay posición aún en DRY_RUN, usa el quote)
    entrada = p_weat
    if not DRY_RUN:
        try:
            entrada = float(trading.get_open_position(ETF).avg_entry_price)
        except Exception:
            pass

    riesgo = qty * entrada * (zw - STOP_INICIAL) / zw
    st.update({
        "estado": "EN_POSICION", "qty_inicial": qty, "qty_actual": qty,
        "entrada_zw": zw, "entrada_weat": entrada, "stop_zw": STOP_INICIAL,
    })
    poner_paracaidas(trading, st)
    guardar_estado(st, f"COMPRA {motivo}: {qty} WEAT @ ${entrada:.2f} (ZW {zw})")
    notify(
        f"✅ COMPRA ejecutada ({motivo}). {qty} WEAT @ ${entrada:.2f} "
        f"(~${qty * entrada:,.0f} notional) | referencia ZW {zw} | "
        f"stop ZW {STOP_INICIAL} | riesgo estimado ${riesgo:.2f} "
        f"({riesgo / equity * 100:.1f}% de la cuenta)"
    )


def vender(trading, st: dict, qty: int, zw: float, motivo: str, terminal: bool) -> None:
    qty = min(qty, st["qty_actual"])
    if qty < 1:
        return
    cancelar_ordenes_weat(trading)          # primero soltar el paracaídas
    orden_mercado(trading, OrderSide.SELL, qty)
    st["qty_actual"] -= qty
    if st["qty_actual"] <= 0 or terminal:
        if st["qty_actual"] > 0:            # terminal con remanente: liquidar todo
            orden_mercado(trading, OrderSide.SELL, st["qty_actual"])
            st["qty_actual"] = 0
        st["estado"] = "CERRADA"
        guardar_estado(st, f"VENTA FINAL ({motivo}) con ZW en {zw}")
        notify(f"🏁 Operación CERRADA — {motivo} (ZW {zw}). "
               f"Revisa el P&L en el dashboard de Alpaca paper.")
    else:
        poner_paracaidas(trading, st)       # re-armar paracaídas con la qty nueva
        guardar_estado(st, f"PARCIAL ({motivo}): -{qty} WEAT con ZW en {zw}")
        notify(f"💰 Parcial ejecutada ({motivo}): vendí {qty} WEAT (ZW {zw}). "
               f"Quedan {st['qty_actual']}. Stop ZW ahora en {st['stop_zw']}.")


# ============================== STATE MACHINE ==============================


def ciclo() -> None:
    st = cargar_estado()

    if st["estado"] == "CERRADA":
        print(f"[{ahora()}] Operación ya cerrada. Nada que hacer. "
              f"(borra {STATE_FILE.name} para reiniciar)", flush=True)
        return

    zw = precio_zw()
    if zw is None:
        notify("⚠️ No pude leer ZW=F de Yahoo (¿429/red?). Reintento en el próximo cron.")
        return

    trading, data_client = clientes()
    mercado_abierto = trading.get_clock().is_open
    print(f"[{ahora()}] estado={st['estado']} | ZW={zw} | "
          f"mercado_equities={'abierto' if mercado_abierto else 'cerrado'}"
          f"{' | DRY_RUN' if DRY_RUN else ''}", flush=True)

    if not mercado_abierto:
        # Sin mercado de equities no se puede ejecutar WEAT; solo observamos.
        return

    e = st["estado"]

    # ---------- ESPERANDO: sin posición, vigilando las dos entradas ----------
    if e == "ESPERANDO":
        if zw < SETUP_INVALIDO:
            st["estado"] = "CERRADA"
            guardar_estado(st, f"Cancelado: ZW {zw} < {SETUP_INVALIDO} sin dar entrada")
            notify(f"❌ Setup cancelado: ZW cayó a {zw}, de vuelta dentro de la caja. "
                   f"La ruptura falló antes de dar entrada. No se compra nada.")
        elif zw <= ZONA_TECHO:
            st["estado"] = "ZONA_TOCADA"
            st["confirmaciones_ruptura"] = 0
            guardar_estado(st, f"ZW tocó la zona de compra ({zw} <= {ZONA_TECHO})")
            notify(f"👀 ZW llegó a la zona de compra ({zw}). Ahora a ver si AGUANTA: "
                   f"compro si recupera {ZONA_CONFIRMA}, cancelo si pierde {SETUP_INVALIDO}.")
        elif zw >= RUPTURA:
            st["confirmaciones_ruptura"] += 1
            if zw > NO_PERSEGUIR:
                guardar_estado(st, f"ZW {zw} sobre {NO_PERSEGUIR}: extendido, no perseguir")
                print(f"  ZW {zw} arriba de {NO_PERSEGUIR}: demasiado extendido, "
                      f"no se persigue. Se espera retroceso.", flush=True)
            elif st["confirmaciones_ruptura"] >= RUPTURA_LECTURAS:
                comprar(trading, data_client, st, zw, "ruptura confirmada de 651")
            else:
                guardar_estado(st, f"Ruptura lectura {st['confirmaciones_ruptura']}"
                                   f"/{RUPTURA_LECTURAS} (ZW {zw})")
                print(f"  ZW {zw} >= {RUPTURA}: lectura "
                      f"{st['confirmaciones_ruptura']}/{RUPTURA_LECTURAS}. "
                      f"Si la próxima también aguanta, compro.", flush=True)
        else:
            if st["confirmaciones_ruptura"]:
                st["confirmaciones_ruptura"] = 0   # la ruptura no aguantó
                guardar_estado(st, f"ZW regresó bajo {RUPTURA}: contador de ruptura a 0")
            else:
                guardar_estado(st)
            print(f"  Sin señal. Esperando retroceso a <= {ZONA_TECHO} "
                  f"o ruptura >= {RUPTURA}.", flush=True)

    # ---------- ZONA_TOCADA: llegó a la zona, esperando confirmación ----------
    elif e == "ZONA_TOCADA":
        if zw < SETUP_INVALIDO:
            st["estado"] = "CERRADA"
            guardar_estado(st, f"Cancelado: la zona falló (ZW {zw} < {SETUP_INVALIDO})")
            notify(f"❌ Setup cancelado: ZW perdió {SETUP_INVALIDO} (está en {zw}). "
                   f"La zona no aguantó. Mejor fuera que mal dentro.")
        elif ZONA_CONFIRMA <= zw <= ZONA_MAX_COMPRA:
            comprar(trading, data_client, st, zw,
                    f"retroceso: la zona aguantó y recuperó {ZONA_CONFIRMA}")
        elif zw > ZONA_MAX_COMPRA:
            st["estado"] = "ESPERANDO"
            guardar_estado(st, f"ZW se escapó a {zw} sin confirmar; de vuelta a esperar")
            notify(f"🏃 ZW rebotó hasta {zw} antes de poder confirmar la entrada. "
                   f"No se persigue: queda viva la entrada por ruptura de {RUPTURA}.")
        else:
            guardar_estado(st)
            print(f"  En/bajo la zona (ZW {zw}). Esperando recuperación de "
                  f"{ZONA_CONFIRMA} para comprar, o {SETUP_INVALIDO} para cancelar.",
                  flush=True)

    # ---------- EN_POSICION: gestionar stop y parciales ----------
    elif e == "EN_POSICION":
        tercio = max(1, st["qty_inicial"] // 3)
        if zw < st["stop_zw"]:
            vender(trading, st, st["qty_actual"], zw,
                   f"STOP: ZW perdió {st['stop_zw']}", terminal=True)
        elif zw >= SALIDA_FINAL:
            vender(trading, st, st["qty_actual"], zw,
                   f"objetivo final {SALIDA_FINAL} (fuera antes del muro de 688)",
                   terminal=True)
        elif zw >= TP2 and not st["tp2_hecho"]:
            st["tp2_hecho"] = True
            st["stop_zw"] = STOP_TRAS_TP2
            vender(trading, st, tercio, zw, f"TP2 {TP2}", terminal=False)
        elif zw >= TP1 and not st["tp1_hecho"]:
            st["tp1_hecho"] = True
            st["stop_zw"] = st["entrada_zw"]   # breakeven: ya no se puede perder
            vender(trading, st, tercio, zw, f"TP1 {TP1} → stop a breakeven",
                   terminal=False)
        else:
            guardar_estado(st)
            pnl_aprox = (zw - st["entrada_zw"]) / st["entrada_zw"] * 100
            print(f"  Holding {st['qty_actual']} WEAT | entrada ZW "
                  f"{st['entrada_zw']} | stop ZW {st['stop_zw']} | "
                  f"ZW ahora {zw} ({pnl_aprox:+.1f}% aprox)", flush=True)


if __name__ == "__main__":
    ciclo()
