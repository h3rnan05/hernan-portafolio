#!/usr/bin/env python3
"""
wizards_bot.py — Bot de portafolio basado en los principios de
"Los magos del mercado" (Market Wizards, Jack D. Schwager).

QUÉ CODIFICA DEL LIBRO (los principios que repiten casi todos los entrevistados):
  1. "Corta las pérdidas rápido"            -> stop inicial a 2×ATR bajo la entrada.
  2. "Deja correr las ganancias"            -> salida solo por trailing stop
                                               (mínimo de 20 días), nunca por target.
  3. "Riesgo máximo 1-2% por operación"     -> sizing: 1.5% del equity por trade
                                               (regla de Paul Tudor Jones / Kovner).
  4. "Opera con la tendencia"               -> entrada por ruptura de máximo de 55
                                               días (sistema de Richard Dennis /
                                               Turtles, entrevistado en el libro).
  5. "Diversifica mercados no correlacionados" (Bruce Kovner)
                                            -> universo de 10 ETFs en 6 clases de
                                               activo; máx. 4 posiciones a la vez.
  6. "Nunca promedies una posición perdedora" (regla universal del libro)
                                            -> prohibido añadir a posiciones abiertas.
  7. "No sobre-operes" (Ed Seykota)         -> el bot REVISA el mercado cada corrida
                                               pero solo opera cuando hay señal.
  8. Riesgo total controlado                -> "calor" del portafolio (suma de
                                               riesgos abiertos) tope en 6% del equity.

CÓMO FUNCIONA
  - Portafolio VIRTUAL de $5,000 (equity simulado; fuente de verdad =
    wizards_state.json, committeado por el workflow para persistir).
  - Precios: API de chart de Yahoo Finance con requests plano (diarios +
    último precio, ~15 min de retraso; suficiente: el sistema decide sobre
    barras DIARIAS). Sin yfinance ni pandas: menos dependencias en el CI.
  - Ejecución espejo (opcional): si hay WEBULL_APP_KEY/WEBULL_APP_SECRET,
    replica las órdenes en el sandbox de Webull para validar la integración.
    El P&L "real" del bot es el virtual; el espejo es instrumentación.
  - Noticias: digest informativo (Google News RSS) en cada aviso a Discord.
    Las noticias NO deciden trades — las decisiones son 100% por reglas.
  - Cada corrida = un chequeo y termina (mismo patrón cron que wheat_swing).

VARIABLES DE ENTORNO
  WEBULL_APP_KEY, WEBULL_APP_SECRET  (opcionales: espejo en sandbox Webull)
  WEBULL_ENDPOINT                    (default api.sandbox.webull.com)
  DISCORD_WEBHOOK                    (opcional, para avisos)
  DRY_RUN=1                          (simula sin guardar estado ni mandar órdenes)

REGLA DURA: si WEBULL_ENDPOINT no contiene "sandbox" ni "uat", el script se
detiene con error. No hay bandera para saltarlo: pasar a producción requiere
editar el código a propósito, con confirmación explícita del dueño.
"""

import itertools
import json
import os
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import requests

# ============================== CONFIGURACIÓN ==============================

EQUITY_INICIAL = 5000.0

# Universo diversificado (Kovner: mercados no correlacionados).
UNIVERSO = {
    "SPY":  "S&P 500",
    "QQQ":  "Nasdaq 100",
    "IWM":  "Small caps EEUU",
    "EFA":  "Acciones internacionales",
    "TLT":  "Bonos largos EEUU",
    "GLD":  "Oro",
    "SLV":  "Plata",
    "USO":  "Petróleo",
    "DBA":  "Agricultura (canasta)",
    "WEAT": "Trigo",
}

RISK_PCT          = 0.015   # 1.5% del equity por trade (regla 1-2% del libro)
MAX_HEAT          = 0.06    # riesgo abierto total máximo (suma de stops): 6%
MAX_POSICIONES    = 4
MAX_NOTIONAL_PCT  = 0.30    # ninguna posición > 30% del equity
CANAL_ENTRADA     = 55      # ruptura de máximo de 55 días (Turtles, sistema 2)
CANAL_SALIDA      = 20      # trailing: mínimo de 20 días
ATR_PERIODO       = 20
ATR_MULT_STOP     = 2.0     # stop inicial = entrada - 2×ATR

STATE_FILE = Path(__file__).resolve().parent / "wizards_state.json"
INBOX_FILE = Path(__file__).resolve().parent / "wizards_inbox.json"
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
WEBULL_ENDPOINT = os.getenv("WEBULL_ENDPOINT", "api.sandbox.webull.com")

MAX_NOTICIAS = 6

# ============================== UTILIDADES ==============================


def ahora() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def notify(msg: str) -> None:
    print(msg, flush=True)
    if DRY_RUN:
        return
    hook = os.getenv("DISCORD_WEBHOOK")
    if hook:
        try:
            # Discord corta en 2000 chars por mensaje
            requests.post(hook, json={"content": msg[:1990]}, timeout=10)
        except Exception as e:
            print(f"  (aviso a Discord falló: {e})", flush=True)
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat, "text": msg[:4000]}, timeout=10,
            )
        except Exception as e:
            print(f"  (aviso a Telegram falló: {e})", flush=True)


def cargar_estado() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "cash": EQUITY_INICIAL,
        "posiciones": {},        # ticker -> {qty, entrada, stop, fecha}
        "pnl_realizado": 0.0,
        "trades_cerrados": 0,
        "trades_ganadores": 0,
        "curva_equity": [],      # [{ts, equity}]
        "noticias_vistas": [],   # hashes para no repetir titulares
        "eventos": [],
    }


def guardar_estado(st: dict, evento: str | None = None) -> None:
    if evento:
        st["eventos"].append({"ts": ahora(), "evento": evento})
    st["eventos"] = st["eventos"][-200:]
    st["curva_equity"] = st["curva_equity"][-500:]
    st["noticias_vistas"] = st["noticias_vistas"][-300:]
    if DRY_RUN:
        print(f"  [DRY_RUN] no guardo estado. Evento: {evento}", flush=True)
        return
    STATE_FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def mercado_abierto() -> bool:
    """Aproximación NYSE: lun-vie 13:30–20:00 UTC (horario de verano EEUU).
    El cron ya corre solo en esa ventana; esto es cinturón y tirantes."""
    t = datetime.now(UTC)
    if t.weekday() >= 5:
        return False
    minutos = t.hour * 60 + t.minute
    return 13 * 60 + 30 <= minutos < 20 * 60


# ============================== DATOS (Yahoo) ==============================


def _chart_yahoo(ticker: str) -> dict | None:
    """Barras diarias de 6 meses + último precio, vía la API de chart de Yahoo
    (misma fuente que yfinance, pero con requests plano: menos dependencias)."""
    for intento in range(3):
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"interval": "1d", "range": "6mo"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
            )
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            barras = [
                {"h": h, "l": lo, "c": c}
                for h, lo, c in zip(q["high"], q["low"], q["close"], strict=True)
                if h is not None and lo is not None and c is not None
            ]
            precio = float(res["meta"].get("regularMarketPrice") or barras[-1]["c"])
            if len(barras) >= CANAL_ENTRADA + 5 and precio > 0:
                return {"barras": barras, "precio": precio}
        except Exception as e:
            print(f"  Yahoo {ticker} intento {intento + 1} falló: {e}", flush=True)
            time.sleep(3 * (intento + 1))
    return None


def bajar_datos(extra: set[str] | None = None) -> dict[str, dict]:
    """Datos del universo + posiciones abiertas + ideas encoladas."""
    datos = {}
    for t in list(UNIVERSO) + sorted((extra or set()) - set(UNIVERSO)):
        d = _chart_yahoo(t)
        if d:
            datos[t] = d
        time.sleep(0.5)  # cortesía con Yahoo: sin ráfagas
    return datos


def indicadores(d: dict) -> dict:
    """Canales Donchian (sobre barras cerradas, sin la de hoy) + ATR + precio."""
    prev = d["barras"][:-1]  # señal con precio de HOY contra canal de barras CERRADAS
    trs = [
        max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"]))
        for a, b in itertools.pairwise(prev)
    ]
    return {
        "precio": d["precio"],
        "max55": max(b["h"] for b in prev[-CANAL_ENTRADA:]),
        "min20": min(b["l"] for b in prev[-CANAL_SALIDA:]),
        "atr": sum(trs[-ATR_PERIODO:]) / min(len(trs), ATR_PERIODO),
    }


# ============================== NOTICIAS (digest) ==============================


def noticias_digest(st: dict, tickers_interes: list[str]) -> str:
    """Titulares de Google News RSS. Informativo: NO influye en las decisiones."""
    consultas = ["stock market today"]
    consultas += [f"{UNIVERSO.get(t, f'{t} stock')} market news"
                  for t in tickers_interes[:3]]
    titulares: list[tuple[str, str]] = []
    for q in consultas:
        try:
            r = requests.get(
                "https://news.google.com/rss/search",
                params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                timeout=10, headers={"User-Agent": "Mozilla/5.0"},
            )
            for item in ET.fromstring(r.content).iter("item"):
                titulo = (item.findtext("title") or "").strip()
                if titulo:
                    titulares.append((q, titulo))
                if len([x for x in titulares if x[0] == q]) >= 2:
                    break
        except Exception as e:
            print(f"  (RSS '{q}' falló: {e})", flush=True)
    nuevos = []
    for _, titulo in titulares:
        h = str(hash(titulo) % 10**12)
        if h not in st["noticias_vistas"]:
            st["noticias_vistas"].append(h)
            nuevos.append(titulo)
        if len(nuevos) >= MAX_NOTICIAS:
            break
    if not nuevos:
        return ""
    return "📰 Noticias:\n" + "\n".join(f"  • {t}" for t in nuevos)


# ============================== WEBULL (espejo opcional) ==============================


def webull_espejo(side: str, ticker: str, qty: int) -> None:
    """Replica la orden en el sandbox de Webull si hay credenciales.
    Nunca bloquea al portafolio virtual: cualquier fallo solo se loguea."""
    key, sec = os.getenv("WEBULL_APP_KEY"), os.getenv("WEBULL_APP_SECRET")
    if not key or not sec or DRY_RUN:
        return
    if "sandbox" not in WEBULL_ENDPOINT and "uat" not in WEBULL_ENDPOINT:
        # Regla dura: jamás contra producción sin cambio de código deliberado.
        print(f"ERROR: endpoint Webull '{WEBULL_ENDPOINT}' no es sandbox/uat. "
              f"Me detengo.", flush=True)
        sys.exit(1)
    try:
        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient
        api = ApiClient(key, sec, "us")
        api.add_endpoint("us", WEBULL_ENDPOINT)
        trade = TradeClient(api)
        cuentas = trade.account_v2.get_account_list().json()
        margin = next((c["account_id"] for c in cuentas
                       if c.get("account_class") == "INDIVIDUAL_MARGIN"), None)
        if not margin:
            print("  espejo Webull: no hay cuenta INDIVIDUAL_MARGIN", flush=True)
            return
        orden = [{
            "combo_type": "NORMAL", "client_order_id": uuid.uuid4().hex,
            "symbol": ticker, "instrument_type": "EQUITY", "market": "US",
            "order_type": "MARKET", "quantity": str(qty), "side": side,
            "time_in_force": "DAY", "entrust_type": "QTY",
            "support_trading_session": "CORE",
        }]
        r = trade.order_v3.place_order(margin, orden)
        print(f"  espejo Webull: {side} {qty} {ticker} -> HTTP {r.status_code}",
              flush=True)
    except Exception as e:
        print(f"  espejo Webull falló ({side} {ticker}): {str(e)[:200]}", flush=True)


# ============================== PORTAFOLIO ==============================


def equity_total(st: dict, precios: dict[str, float]) -> float:
    valor = sum(p["qty"] * precios.get(t, p["entrada"])
                for t, p in st["posiciones"].items())
    return st["cash"] + valor


def calor_actual(st: dict, precios: dict[str, float]) -> float:
    """Riesgo abierto total: lo que se pierde si TODOS los stops se tocan."""
    eq = equity_total(st, precios)
    if eq <= 0:
        return 1.0
    riesgo = sum(p["qty"] * max(0.0, precios.get(t, p["entrada"]) - p["stop"])
                 for t, p in st["posiciones"].items())
    return riesgo / eq


def vender(st: dict, ticker: str, precio: float, motivo: str) -> str:
    pos = st["posiciones"].pop(ticker)
    st["cash"] += pos["qty"] * precio
    pnl = (precio - pos["entrada"]) * pos["qty"]
    st["pnl_realizado"] += pnl
    st["trades_cerrados"] += 1
    if pnl > 0:
        st["trades_ganadores"] += 1
    webull_espejo("SELL", ticker, pos["qty"])
    linea = (f"🔴 VENTA {ticker}: {pos['qty']} @ ${precio:.2f} ({motivo}) | "
             f"P&L ${pnl:+.2f}")
    guardar_estado(st, linea)
    return linea


def comprar(st: dict, ticker: str, ind: dict, eq: float,
            motivo: str | None = None, origen: str = "sistema") -> str | None:
    precio, atr = ind["precio"], ind["atr"]
    stop = round(precio - ATR_MULT_STOP * atr, 2)
    riesgo_accion = precio - stop
    if riesgo_accion <= 0:
        return None
    qty = int(eq * RISK_PCT / riesgo_accion)                 # regla 1-2%
    qty = min(qty, int(eq * MAX_NOTIONAL_PCT / precio))      # tope de notional
    qty = min(qty, int(st["cash"] / precio))                 # sin apalancamiento
    if qty < 1:
        return None
    st["cash"] -= qty * precio
    st["posiciones"][ticker] = {
        "qty": qty, "entrada": precio, "stop": stop, "fecha": ahora(),
        "origen": origen,
    }
    webull_espejo("BUY", ticker, qty)
    motivo = motivo or f"ruptura de máx. {CANAL_ENTRADA}d"
    linea = (f"🟢 COMPRA {ticker} ({UNIVERSO.get(ticker, origen)}): "
             f"{qty} @ ${precio:.2f} | {motivo} | stop ${stop:.2f} "
             f"(riesgo ${qty * riesgo_accion:.2f} = "
             f"{qty * riesgo_accion / eq * 100:.1f}% del equity)")
    guardar_estado(st, linea)
    return linea


# ============================== INBOX (ideas desde Telegram) ==============================


def cargar_inbox() -> list[dict]:
    """Ideas aprobadas por el evaluador de Telegram, pendientes de ejecutar."""
    if INBOX_FILE.exists():
        try:
            return json.loads(INBOX_FILE.read_text()).get("pendientes", [])
        except Exception as e:
            print(f"  inbox ilegible ({e}); lo ignoro esta corrida", flush=True)
    return []


def limpiar_inbox() -> None:
    if DRY_RUN:
        print("  [DRY_RUN] no limpio el inbox", flush=True)
        return
    if INBOX_FILE.exists():
        INBOX_FILE.write_text(json.dumps({"pendientes": []}, indent=2) + "\n")


def procesar_ideas(st: dict, ideas: list[dict], inds: dict,
                   precios: dict[str, float]) -> list[str]:
    """Ejecuta las ideas del chat, SUJETAS a los mismos límites de riesgo
    que el sistema. El LLM propuso; aquí manda el código."""
    lineas = []
    for idea in ideas:
        t, tesis = idea.get("ticker", "?"), idea.get("tesis") or ""
        eq = equity_total(st, precios)
        if t in st["posiciones"]:
            veredicto = f"ya hay posición abierta en {t} (nunca se promedia)"
        elif t not in inds:
            veredicto = (f"sin datos/histórico suficiente de {t} en Yahoo "
                         f"(necesito {CANAL_ENTRADA + 5} barras diarias)")
        elif len(st["posiciones"]) >= MAX_POSICIONES:
            veredicto = f"sin slots: ya hay {MAX_POSICIONES} posiciones abiertas"
        elif calor_actual(st, precios) >= MAX_HEAT:
            veredicto = f"calor del portafolio ya en el tope de {MAX_HEAT:.0%}"
        else:
            linea = comprar(st, t, inds[t], eq,
                            motivo=f"idea vía Telegram: {tesis[:90]}",
                            origen="idea")
            if linea:
                lineas.append(linea)
                continue
            veredicto = "el sizing dio 0 acciones (precio alto vs cash/límites)"
        lineas.append(f"🚫 Idea {t} RECHAZADA por riesgo: {veredicto}.")
        guardar_estado(st, f"Idea {t} rechazada: {veredicto}")
    return lineas


# ============================== CICLO PRINCIPAL ==============================


def ciclo() -> None:
    st = cargar_estado()
    ideas = cargar_inbox()
    tickers_extra = set(st["posiciones"]) | {
        i["ticker"] for i in ideas if i.get("ticker")
    }
    datos = bajar_datos(tickers_extra)
    if len(datos) < 5:
        notify(f"[{ahora()}] ⚠️ Solo obtuve datos de {len(datos)} tickers de "
               f"Yahoo. No opero a ciegas; reintento en el próximo cron.")
        return

    inds = {t: indicadores(df) for t, df in datos.items()}
    precios = {t: i["precio"] for t, i in inds.items()}
    eq = equity_total(st, precios)
    abierto = mercado_abierto()
    lineas: list[str] = []

    # ---- 1) Gestionar posiciones abiertas (stops primero: cortar pérdidas) ----
    for ticker in list(st["posiciones"]):
        pos = st["posiciones"][ticker]
        ind = inds.get(ticker)
        if not ind:
            continue
        # El trailing (mín. 20d) solo SUBE el stop, nunca lo baja.
        nuevo_stop = max(pos["stop"], round(ind["min20"], 2))
        if nuevo_stop > pos["stop"]:
            pos["stop"] = nuevo_stop
            guardar_estado(st, f"{ticker}: stop sube a ${nuevo_stop:.2f} (mín. 20d)")
        if abierto and ind["precio"] < pos["stop"]:
            lineas.append(vender(st, ticker, ind["precio"],
                                 f"stop ${pos['stop']:.2f} tocado"))

    # ---- 2) Ideas del chat primero (tienen prioridad sobre las señales) ----
    if ideas:
        if abierto:
            lineas += procesar_ideas(st, ideas, inds, precios)
            limpiar_inbox()
        else:
            print(f"  {len(ideas)} idea(s) en el inbox esperando apertura "
                  f"del mercado.", flush=True)

    # ---- 3) Buscar entradas del sistema (mercado abierto y capacidad) ----
    if abierto:
        señales = [
            (ind["precio"] / ind["max55"], t)
            for t, ind in inds.items()
            if t not in st["posiciones"] and ind["precio"] > ind["max55"]
        ]
        for _, ticker in sorted(señales, reverse=True):  # la ruptura más fuerte primero
            if len(st["posiciones"]) >= MAX_POSICIONES:
                break
            if calor_actual(st, precios) >= MAX_HEAT:
                lineas.append(f"⚠️ Señal en {ticker} ignorada: calor del "
                              f"portafolio ya en el tope de {MAX_HEAT:.0%}.")
                break
            linea = comprar(st, ticker, inds[ticker], eq)
            if linea:
                lineas.append(linea)

    # ---- 4) Revisión de mercado (la "lectura" del libro, informativa) ----
    eq = equity_total(st, precios)
    st["curva_equity"].append({"ts": ahora(), "equity": round(eq, 2)})
    revision = []
    for t, ind in sorted(inds.items()):
        if t in st["posiciones"]:
            pos = st["posiciones"][t]
            pnl = (ind["precio"] - pos["entrada"]) * pos["qty"]
            revision.append(f"  {t}: EN POSICIÓN {pos['qty']} u. | "
                            f"${ind['precio']:.2f} | stop ${pos['stop']:.2f} | "
                            f"P&L ${pnl:+.2f}")
        else:
            dist = (ind["max55"] / ind["precio"] - 1) * 100
            estado = ("🔥 en ruptura" if dist < 0
                      else f"a {dist:.1f}% de señal")
            revision.append(f"  {t}: ${ind['precio']:.2f} | {estado}")

    wr = (st["trades_ganadores"] / st["trades_cerrados"] * 100
          if st["trades_cerrados"] else 0.0)
    resumen = (
        f"[{ahora()}] 🧙 Wizards bot | equity ${eq:,.2f} "
        f"({(eq / EQUITY_INICIAL - 1) * 100:+.2f}%) | cash ${st['cash']:,.2f} | "
        f"posiciones {len(st['posiciones'])}/{MAX_POSICIONES} | "
        f"calor {calor_actual(st, precios):.1%} | "
        f"trades {st['trades_cerrados']} (WR {wr:.0f}%)"
        f"{' | mercado CERRADO' if not abierto else ''}"
        f"{' | DRY_RUN' if DRY_RUN else ''}"
    )

    partes = [resumen]
    partes += lineas
    partes.append("Revisión de mercado (canales 55/20d):")
    partes += revision
    digest = noticias_digest(st, list(st["posiciones"]) or list(UNIVERSO))
    if digest:
        partes.append(digest)

    # Aviso completo a Discord solo cuando hubo acción; si no, solo log.
    mensaje = "\n".join(partes)
    if lineas:
        notify(mensaje)
    else:
        print(mensaje, flush=True)

    guardar_estado(st)


if __name__ == "__main__":
    ciclo()
