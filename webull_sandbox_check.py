#!/usr/bin/env python3
"""
webull_sandbox_check.py — Sonda de verificación del sandbox de Webull OpenAPI.

PASO 1 de la migración del bot de trigo (wheat_swing_zw.py) de Alpaca a Webull.
Este script NO es el bot: solo responde, con evidencia real, las 3 preguntas
que bloquean la migración:

  1. ¿El sandbox devuelve precios reales/realistas de futuros (ZW)?
     -> Pide snapshot de ZWU6 vía futures_market_data y lo compara contra
        el precio de ZW=F en Yahoo Finance (misma fuente que usa el bot hoy).
  2. ¿Se pueden colocar órdenes de futuros con fills simulados y P&L?
     -> SOLO con la bandera --order: coloca 1 contrato a mercado, consulta
        el fill vía order_v3, cierra la posición y muestra el balance.
  3. ¿La cuenta de prueba tiene account_id de tipo futures?
     -> Lista cuentas vía account_v2.get_account_list() e imprime tipos.

USO
  export WEBULL_APP_KEY=...      (generado en el Portal Sandbox de Webull)
  export WEBULL_APP_SECRET=...
  python webull_sandbox_check.py            # checks de solo lectura (1 y 3)
  python webull_sandbox_check.py --order    # además prueba una orden (2)

REGLA DURA: este script solo habla con el sandbox. Si el endpoint no contiene
"sandbox" ni "uat", aborta. No existe bandera para saltarse esto: el chequeo
contra producción sencillamente no es trabajo de este script.
"""

import json
import os
import sys
import time
import uuid

import requests

from webull.core.client import ApiClient
from webull.data.common.category import Category
from webull.data.data_client import DataClient
from webull.trade.trade_client import TradeClient

ENDPOINT = os.getenv("WEBULL_ENDPOINT", "api.sandbox.webull.com")
REGION = "us"
CONTRATO = os.getenv("WEBULL_ZW_SYMBOL", "ZWU6")  # trigo SRW sep-2026


def die(msg: str) -> None:
    print(f"\nERROR: {msg}", flush=True)
    sys.exit(1)


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 60}\n{titulo}\n{'=' * 60}", flush=True)


def dump(etiqueta: str, res) -> dict | list | None:
    """Imprime la respuesta cruda del SDK (evidencia) y regresa el JSON."""
    try:
        cuerpo = res.json()
    except Exception:
        cuerpo = None
    print(f"{etiqueta}: HTTP {res.status_code}")
    print(json.dumps(cuerpo, indent=2, ensure_ascii=False, default=str)[:3000],
          flush=True)
    return cuerpo


def precio_yahoo_zw() -> float | None:
    """Precio de referencia de ZW=F vía la API de chart de Yahoo (sin API key)."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/ZW=F",
            params={"interval": "5m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        meta = r.json()["chart"]["result"][0]["meta"]
        return float(meta["regularMarketPrice"])
    except Exception as e:
        print(f"  (no pude leer Yahoo como referencia: {e})", flush=True)
        return None


def main() -> None:
    # ---- Regla dura: solo sandbox/uat ----
    if "sandbox" not in ENDPOINT and "uat" not in ENDPOINT:
        die(f"El endpoint '{ENDPOINT}' NO parece de sandbox/test. "
            f"Este script se niega a correr contra producción.")

    key = os.getenv("WEBULL_APP_KEY")
    sec = os.getenv("WEBULL_APP_SECRET")
    if not key or not sec:
        die("Faltan WEBULL_APP_KEY / WEBULL_APP_SECRET en el entorno.\n"
            "Genéralas en el Portal Sandbox de Webull:\n"
            "  1. https://passport.webull.com/auth/simple/login/inst\n"
            "  2. [Open API] -> [Open API Application]\n"
            "  3. [API Keys Management] -> crear key")

    print(f"Endpoint: {ENDPOINT} | contrato: {CONTRATO}")
    api = ApiClient(key, sec, REGION)
    api.add_endpoint(REGION, ENDPOINT)

    # ================= PREGUNTA 3: cuenta futures en account_v2 =============
    seccion("3) account_v2.get_account_list() — ¿hay cuenta futures?")
    trade = TradeClient(api)
    cuentas = dump("get_account_list", trade.account_v2.get_account_list())
    futures_account_id = None
    lista = (cuentas or {}).get("data", cuentas) if isinstance(cuentas, dict) else cuentas
    for c in lista or []:
        if isinstance(c, dict) and "future" in json.dumps(c).lower():
            futures_account_id = c.get("account_id")
    if futures_account_id:
        print(f"\n  -> CUENTA FUTURES ENCONTRADA: {futures_account_id}")
    else:
        print("\n  -> No identifiqué una cuenta de tipo futures en la respuesta "
              "(revisa el JSON de arriba: la evidencia manda).")

    # ================= PREGUNTA 1: precios de ZW reales o dummy ============
    seccion(f"1) Snapshot de {CONTRATO} — ¿precio real o dummy?")
    data = DataClient(api)
    try:
        snap = dump(
            f"get_futures_snapshot({CONTRATO})",
            data.futures_market_data.get_futures_snapshot(
                CONTRATO, Category.US_FUTURES.name),
        )
    except Exception as e:
        snap = None
        print(f"  snapshot falló: {e}", flush=True)

    yahoo = precio_yahoo_zw()
    if yahoo:
        print(f"\n  Referencia Yahoo ZW=F: {yahoo:.2f} centavos")
        print("  Compara contra el precio del snapshot de arriba: si difieren "
              "por más de ~1-2% (fuera de horario aparte), el sandbox es dummy.")

    # ================= PREGUNTA 2: orden simulada con fill (opt-in) ========
    if "--order" not in sys.argv:
        seccion("2) Orden de prueba — OMITIDA (corre con --order para probarla)")
        return
    if not futures_account_id:
        die("No hay account_id de futures; no puedo probar la orden (pregunta 2).")

    seccion(f"2) Orden de mercado de 1 {CONTRATO} — ¿fill simulado con P&L?")
    oid = uuid.uuid4().hex
    orden = {
        "client_order_id": oid,
        "symbol": CONTRATO,
        "instrument_type": "FUTURES",
        "market": "US",
        "order_type": "MARKET",
        "quantity": "1",
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    dump("place_order", trade.order_v3.place_order(futures_account_id, [orden]))
    time.sleep(5)
    detalle = dump("get_order_detail",
                   trade.order_v3.get_order_detail(futures_account_id, oid))

    # Cerrar lo que se haya llenado, para no dejar posición abierta en sandbox.
    if detalle and "FILLED" in json.dumps(detalle).upper():
        cierre = dict(orden, client_order_id=uuid.uuid4().hex, side="SELL")
        dump("place_order (cierre)",
             trade.order_v3.place_order(futures_account_id, [cierre]))
        time.sleep(5)
    dump("get_account_balance",
         trade.account_v2.get_account_balance(futures_account_id))
    print("\n  -> Si arriba ves fill price realista y el balance cambió, "
          "la pregunta 2 queda respondida que SÍ.")


if __name__ == "__main__":
    main()
