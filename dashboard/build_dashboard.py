#!/usr/bin/env python3
"""Genera el panel del bot como una página HTML estática. Solo lectura.

Fuentes:
  - watchlist.json            salida del hunter
  - logs/events.jsonl         eventos escritos con dashboard.events.log_event
  - API de Alpaca PAPER       solo peticiones GET, endpoint fijo

Regla del panel: un dato que falta se muestra como "—", nunca como 0.

Uso:  python -m dashboard.build_dashboard
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ALPACA_PAPER = "https://paper-api.alpaca.markets"  # fijo: el panel nunca habla con la cuenta real
NY = ZoneInfo("America/New_York")


# ───────────────────────── configuración ─────────────────────────

def _env_float(nombre: str, defecto: float | None = None) -> float | None:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        return defecto
    try:
        return float(valor)
    except ValueError:
        return defecto


def cargar_config() -> dict:
    return {
        # Canónico (lo escribe GHA) y overlay de estado del VPS (fuera de git).
        "watchlist": Path(os.environ.get("DASH_WATCHLIST", "momentum_hunter/watchlist.json")),
        "watchlist_estado": Path(os.environ.get(
            "DASH_WATCHLIST_ESTADO",
            os.environ.get("MOMENTUM_WATCHLIST_STATE", "/var/lib/momentum/watchlist_vps_state.json"))),
        "eventos": Path(os.environ.get("DASH_EVENTOS", "logs/events.jsonl")),
        "salida": Path(os.environ.get("DASH_SALIDA", "dashboard_site")),
        "presupuesto_velas": _env_float("DASH_PRESUPUESTO_VELAS", 8.0),
        "hunter_max_min": _env_float("DASH_HUNTER_MAX_MIN", 45.0),
        "rechequeo_max_min": _env_float("DASH_RECHEQUEO_MAX_MIN", 12.0),
        "tz": ZoneInfo(os.environ.get("DASH_TZ", "UTC")),
    }


# ───────────────────────── utilidades ─────────────────────────

def parse_ts(valor) -> datetime | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        segundos = valor / 1000 if valor > 1e12 else valor
        try:
            return datetime.fromtimestamp(segundos, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(valor, str):
        texto = valor.strip().replace("Z", "+00:00")
        texto = re.sub(r"(\.\d{6})\d+", r"\1", texto)  # Alpaca manda nanosegundos
        try:
            d = datetime.fromisoformat(texto)
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def num(valor) -> float | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _primero(d: dict, *claves):
    for c in claves:
        v = d.get(c)
        if v not in (None, "", []):
            return v
    return None


def inicio_dia_ny(ahora: datetime) -> datetime:
    return ahora.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)


def sesion_abierta(ahora: datetime) -> bool:
    """Horario regular de NYSE. No conoce feriados."""
    ny = ahora.astimezone(NY)
    return ny.weekday() < 5 and time(9, 30) <= ny.time() < time(16, 0)


def percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(0, math.ceil(p / 100 * len(ordenados)) - 1)
    return ordenados[k]


# ───────────────────────── fuentes ─────────────────────────

def leer_eventos(ruta: Path, desde: datetime):
    eventos, malas = [], 0
    try:
        with ruta.open(encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    e = json.loads(linea)
                except json.JSONDecodeError:
                    malas += 1
                    continue
                ts = parse_ts(e.get("ts")) if isinstance(e, dict) else None
                if ts is None:
                    malas += 1
                    continue
                if ts >= desde:
                    e["_ts"] = ts
                    eventos.append(e)
    except FileNotFoundError:
        return [], 0, f"No existe el log de eventos ({ruta})."
    except OSError as exc:
        return [], 0, f"No se pudo leer el log de eventos: {exc}"
    eventos.sort(key=lambda e: e["_ts"])
    return eventos, malas, None


CLAVES_LISTA = ("entradas", "watchlist", "tickers", "candidatos", "candidates", "items", "symbols")


def _cap(x: dict):
    if "es_large_cap" in x and isinstance(x["es_large_cap"], bool):
        return "large" if x["es_large_cap"] else "small"
    return _primero(x, "cap", "cap_class", "segmento", "universe")  # ausente = None, nunca "small"


def _catalizador(x: dict):
    tipo = _primero(x, "catalizador_tipo")
    titular = _primero(x, "catalizador_titular")
    if tipo or titular:
        return " · ".join(str(v) for v in (tipo, titular) if v)
    return _primero(x, "catalizador", "catalyst", "keyword", "keywords", "motivo")


def leer_estado_vps(ruta: Path):
    """Overlay que escribe el VPS (--solo-watchlist). Devuelve ({ticker: campos}, error).

    Si el archivo no existe no es error: el overlay puede estar apagado
    (MOMENTUM_WATCHLIST_VPS_STATE=0) y entonces manda el canónico.
    """
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"No se pudo leer el estado VPS de la watchlist ({type(exc).__name__})."
    entradas = crudo.get("entries") if isinstance(crudo, dict) else None
    if not isinstance(entradas, dict):
        return {}, "Estado VPS de la watchlist con formato no reconocido."
    return {str(k).upper(): v for k, v in entradas.items() if isinstance(v, dict)}, None


def fecha_ultimo_commit(ruta: Path) -> datetime | None:
    """Fecha del último commit que tocó `ruta` (`git log -1 --format=%cI`).
    None si no es un repo git, git no está o falla por lo que sea. En un clon
    superficial (`--depth`) el historial está cortado y git atribuiría el
    archivo al commit más viejo que tiene, así que ahí tampoco hay dato."""
    try:
        sup = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ruta.parent, capture_output=True, text=True, timeout=10,
        )
        if sup.returncode != 0 or sup.stdout.strip() != "false":
            return None
        r = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", ruta.name],
            cwd=ruta.parent, capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return parse_ts(r.stdout.strip())


# Marcas que escribe el hunter dentro de cada entrada (no el overlay VPS).
CLAVES_TS_ENTRADA = ("actualizado_en", "watchlist_escrito_ts", "creado_en",
                     "updated_at", "detectado", "detected_at", "added_at", "timestamp", "ts")


def leer_watchlist(ruta: Path, ruta_estado: Path | None = None):
    """Devuelve (items, momento_generado, error).

    `momento_generado` sale del propio JSON (marca de nivel superior o la
    entrada más reciente) o, si no hay, del último commit que tocó el
    archivo. Nunca del mtime: tras un `git clone`/`git pull` el mtime es la
    hora de la descarga, no la del hunter, y daría un "OK" falso. Si no hay
    ninguna marca, None ("Sin datos")."""
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None, f"No existe {ruta}."
    except (OSError, json.JSONDecodeError) as exc:
        return [], None, f"No se pudo leer la watchlist: {exc}"

    generado, lista = None, crudo
    if isinstance(crudo, dict):
        generado = parse_ts(_primero(crudo, "generado", "generated_at", "updated_at", "ts"))
        lista = next((crudo[c] for c in CLAVES_LISTA if isinstance(crudo.get(c), list)), None)
    if not isinstance(lista, list):
        return [], None, "Formato de watchlist no reconocido: ajusta leer_watchlist()."

    overlay, err_estado = leer_estado_vps(ruta_estado) if ruta_estado else ({}, None)

    items = []
    mas_reciente = None
    for x in lista:
        if isinstance(x, str):
            x = {"ticker": x}
        if not isinstance(x, dict):
            continue
        for clave in CLAVES_TS_ENTRADA:
            ts = parse_ts(x.get(clave))
            if ts is not None and (mas_reciente is None or ts > mas_reciente):
                mas_reciente = ts
        catalizador = _catalizador(x)
        if isinstance(catalizador, list):
            catalizador = ", ".join(map(str, catalizador))
        ticker = _primero(x, "ticker", "symbol", "simbolo")
        vps = overlay.get(str(ticker).upper(), {}) if ticker else {}
        items.append({
            "ticker": ticker,
            "cap": _cap(x),
            "catalizador": catalizador,
            "detectado": parse_ts(_primero(x, "detectado", "detected_at", "creado_en", "timestamp", "added_at", "ts")),
            "estado": _primero(vps, "estado") or _primero(x, "estado", "status", "state"),
            "actualizado": parse_ts(_primero(vps, "actualizado_en") or _primero(x, "actualizado_en", "updated_at")),
        })
    if generado is None:
        candidatos = [t for t in (mas_reciente, fecha_ultimo_commit(ruta)) if t is not None]
        generado = max(candidatos) if candidatos else None
    return items, generado, err_estado


def alpaca_get(ruta: str, params: dict | None = None, timeout: float = 10):
    # Mismos nombres que usa momentum_paper_trader/run.py; APCA_* queda como respaldo.
    clave = os.environ.get("ALPACA_PAPER_API_KEY") or os.environ.get("APCA_API_KEY_ID")
    secreto = os.environ.get("ALPACA_PAPER_API_SECRET") or os.environ.get("APCA_API_SECRET_KEY")
    if not clave or not secreto:
        return None, "Faltan ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET."
    url = ALPACA_PAPER + ruta
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET", headers={
        "APCA-API-KEY-ID": clave,
        "APCA-API-SECRET-KEY": secreto,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, f"Alpaca respondió HTTP {exc.code} en {ruta}."
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, f"Alpaca no respondió en {ruta}: {exc}"


# ───────────────────────── cálculos ─────────────────────────

MEDIDA_LATENCIA = "ruptura_a_orden"


def latencias(eventos: list[dict]) -> list[tuple[str, float]]:
    """Velas de 1 min entre la RUPTURA y la orden enviada, la misma medida que
    el presupuesto de 8 velas. Solo cuenta órdenes que traen `velas` con
    `medida == "ruptura_a_orden"`: si el hunter no guardó las velas previas
    al disparo, esa orden no entra al gráfico (no se reconstruye por tiempo,
    porque eso mediría otra cosa)."""
    resultado = []
    for e in eventos:
        if e.get("tipo") != "orden" or e.get("estado") != "enviada" or not e.get("ticker"):
            continue
        if e.get("medida") != MEDIDA_LATENCIA:
            continue
        velas = num(e.get("velas"))
        if velas is not None and velas >= 0:
            resultado.append((e["ticker"], round(velas, 1)))
    return resultado


def _edad_min(ahora: datetime, momento: datetime | None) -> float | None:
    return None if momento is None else (ahora - momento).total_seconds() / 60


def _estado_frescura(edad: float | None, maximo: float | None, en_sesion: bool) -> str:
    if edad is None:
        return "sin-datos"
    if en_sesion and maximo is not None and edad > maximo:
        return "alerta"
    return "ok"


ESTADOS_ORDEN = {
    "filled": "ejecutada", "partially_filled": "parcial", "rejected": "rechazada",
    "canceled": "cancelada", "expired": "expirada", "new": "abierta", "accepted": "abierta",
    "pending_new": "abierta",
}


ESTADOS_ACTIVOS = ("watching", "triggered")


def filtrar_watchlist(items: list[dict], desde: datetime) -> list[dict]:
    """Activas siempre; terminales solo si cambiaron hoy. Activas primero, luego lo más reciente."""
    def activo(w):
        return str(w.get("estado") or "").lower() in ESTADOS_ACTIVOS

    visibles = [w for w in items if activo(w) or (w.get("actualizado") and w["actualizado"] >= desde)]
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(visibles, key=lambda w: (not activo(w), -(w.get("actualizado") or minimo).timestamp()))


def construir(ahora: datetime, cfg: dict, get=alpaca_get) -> dict:
    desde = inicio_dia_ny(ahora)
    en_sesion = sesion_abierta(ahora)
    problemas = []

    eventos, malas, err = leer_eventos(cfg["eventos"], desde)
    # Sin log de eventos no se sabe cuántas decisiones o bloqueos hubo: eso
    # es "—", no 0. Un archivo que existe pero no tiene eventos hoy sí es 0.
    hay_eventos = err is None
    if err:
        problemas.append(err)
    if malas:
        problemas.append(f"{malas} líneas del log de eventos no se pudieron leer.")

    watch, wl_momento, err = leer_watchlist(cfg["watchlist"], cfg.get("watchlist_estado"))
    if err:
        problemas.append(err)
    watch = filtrar_watchlist(watch, desde)

    cuenta, err_c = get("/v2/account")
    posiciones, err_p = get("/v2/positions")
    ordenes, err_o = get("/v2/orders", {
        "status": "all", "limit": 500, "direction": "desc",
        "after": desde.astimezone(timezone.utc).isoformat(),
    })
    for e in (err_c, err_p, err_o):
        if e and e not in problemas:
            problemas.append(e)
    alpaca_ok = not (err_c or err_p or err_o)

    equity = num(cuenta.get("equity")) if isinstance(cuenta, dict) else None
    last_equity = num(cuenta.get("last_equity")) if isinstance(cuenta, dict) else None
    pnl = equity - last_equity if equity is not None and last_equity is not None else None
    pnl_pct = pnl / last_equity * 100 if pnl is not None and last_equity else None

    n_pos = len(posiciones) if isinstance(posiciones, list) else None
    lista_ordenes = ordenes if isinstance(ordenes, list) else None
    n_ord = len(lista_ordenes) if lista_ordenes is not None else None
    n_rech = sum(1 for o in lista_ordenes if o.get("status") == "rejected") if lista_ordenes is not None else None

    lat = latencias(eventos)
    valores = [v for _, v in lat]
    presupuesto = cfg["presupuesto_velas"]

    rechequeos = [e for e in eventos if e.get("tipo") == "rechequeo"]
    decisiones = [e for e in eventos if e.get("tipo") == "decision"]
    bloqueos = [e for e in eventos if e.get("tipo") == "bloqueo_riesgo"]

    ult_rechequeo = rechequeos[-1]["_ts"] if rechequeos else None
    ult_decision = decisiones[-1]["_ts"] if decisiones else None

    por_limite: dict[str, int] = {}
    for b in bloqueos:
        nombre = str(b.get("limite") or "sin nombre")
        por_limite[nombre] = por_limite.get(nombre, 0) + 1

    estado_rechequeo = _estado_frescura(_edad_min(ahora, ult_rechequeo), cfg["rechequeo_max_min"], en_sesion)
    # Un 0 solo es un dato si hay log Y el bot corrió hace poco. Sin rechequeo
    # reciente, "0 decisiones" o "0 bloqueos" no significa que no haya pasado nada.
    conteos_validos = hay_eventos and estado_rechequeo == "ok"
    motivo_sin_datos = ("no hay log de eventos" if not hay_eventos
                        else "no hay un rechequeo reciente")

    etapas = [
        {
            "nombre": "Hunter", "donde": "GitHub Actions",
            "rol": "Busca candidatos. Determinista, sin IA ni bróker.",
            "estado": _estado_frescura(_edad_min(ahora, wl_momento), cfg["hunter_max_min"], en_sesion),
            "detalle": f"watchlist de las {_hora(wl_momento, cfg['tz'])}",
        },
        {
            "nombre": "Rechequeo", "donde": "VPS",
            "rol": "Revisa la watchlist con --solo-watchlist.",
            "estado": estado_rechequeo,
            "detalle": f"última corrida {_hora(ult_rechequeo, cfg['tz'])}",
        },
        {
            "nombre": "Ejecutor", "donde": "VPS",
            "rol": "Consulta al LLM y decide si entra.",
            "estado": "ok" if decisiones and conteos_validos else "sin-datos",
            # Un conteo > 0 es real aunque el rechequeo esté viejo; un 0 no.
            "detalle": (f"{len(decisiones)} decisiones hoy · última {_hora(ult_decision, cfg['tz'])}"
                        if hay_eventos and (decisiones or conteos_validos)
                        else "— decisiones hoy · última —"),
        },
        {
            "nombre": "Riesgo", "donde": "Código",
            "rol": "Límites deterministas. Sin margen. Fail-closed.",
            # Un bloqueo registrado siempre es alerta; "OK" exige conteos válidos.
            "estado": "alerta" if bloqueos else ("ok" if conteos_validos else "sin-datos"),
            "detalle": (f"{len(bloqueos)} bloqueos hoy"
                        if hay_eventos and (bloqueos or conteos_validos) else "— bloqueos hoy"),
        },
    ]

    stream = []
    for o in (lista_ordenes or [])[:12]:
        stream.append({
            "hora": _hora(parse_ts(o.get("submitted_at")), cfg["tz"], segundos=True),
            "ticker": o.get("symbol") or "—",
            "lado": {"buy": "compra", "sell": "venta"}.get(o.get("side"), o.get("side") or "—"),
            "estado": ESTADOS_ORDEN.get(o.get("status"), o.get("status") or "—"),
            "precio": num(o.get("filled_avg_price")),
        })

    dudas = [
        {"ticker": d.get("ticker") or "—", "hora": _hora(d["_ts"], cfg["tz"]),
         "motivo": str(d.get("motivo") or "sin motivo registrado")}
        for d in reversed(decisiones) if d.get("entra") is False
    ][:6]

    return {
        "ahora": ahora, "tz": cfg["tz"], "en_sesion": en_sesion, "alpaca_ok": alpaca_ok,
        "problemas": problemas, "etapas": etapas,
        "equity": equity, "pnl": pnl, "pnl_pct": pnl_pct,
        "n_pos": n_pos, "n_ord": n_ord, "n_rech": n_rech,
        "watch": watch, "wl_momento": wl_momento,
        "lat": lat, "lat_mediana": statistics.median(valores) if valores else None,
        "lat_p90": percentil(valores, 90),
        "lat_fuera": sum(1 for v in valores if v > presupuesto) if valores else None,
        "presupuesto": presupuesto,
        "stream": stream, "dudas": dudas,
        "bloqueos": sorted(por_limite.items(), key=lambda kv: -kv[1]),
        "hay_eventos": hay_eventos,
        "conteos_validos": conteos_validos, "motivo_sin_datos": motivo_sin_datos,
        "ult_bloqueo": bloqueos[-1] if bloqueos else None,
    }


# ───────────────────────── render ─────────────────────────

def esc(v) -> str:
    return html.escape("—" if v is None else str(v))


def _hora(d: datetime | None, tz, segundos: bool = False) -> str:
    if d is None:
        return "—"
    return d.astimezone(tz).strftime("%H:%M:%S" if segundos else "%H:%M")


def fmt_dinero(v: float | None, signo: bool = False) -> str:
    if v is None:
        return "—"
    cuerpo = f"${abs(v):,.2f}"
    if signo:
        return ("+" if v >= 0 else "−") + cuerpo
    return ("−" if v < 0 else "") + cuerpo


def fmt_num(v, sufijo: str = "") -> str:
    if v is None:
        return "—"
    texto = f"{v:.1f}" if isinstance(v, float) and not v.is_integer() else f"{int(v)}"
    return texto + sufijo


def _grafico_latencia(ctx: dict) -> str:
    ancho, alto, x0, y0, y1 = 480, 230, 36, 200, 10
    valores = [v for _, v in ctx["lat"]][-40:]
    tope = max([12.0, ctx["presupuesto"] + 4, *valores])
    y = lambda v: y0 - (v / tope) * (y0 - y1)
    partes = [
        f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="Latencia ruptura a orden por operación, en velas de 1 minuto">',
        f'<rect x="{x0+1}" y="{y1}" width="{ancho-x0-10}" height="{y(ctx["presupuesto"])-y1:.1f}" fill="#fbeceb"/>',
        f'<line x1="{x0}" y1="{y1}" x2="{x0}" y2="{y0}" stroke="#bdb9ad"/>',
        f'<line x1="{x0}" y1="{y0}" x2="{ancho-10}" y2="{y0}" stroke="#bdb9ad"/>',
    ]
    for marca in range(0, int(tope) + 1, 4):
        partes.append(f'<text x="{x0-8}" y="{y(marca)+4:.1f}" text-anchor="end" class="eje">{marca}</text>')
    yp = y(ctx["presupuesto"])
    partes.append(f'<line x1="{x0}" y1="{yp:.1f}" x2="{ancho-10}" y2="{yp:.1f}" stroke="#b3261e" stroke-width="1.5" stroke-dasharray="6 4"/>')
    partes.append(f'<text x="{ancho-14}" y="{yp-8:.1f}" text-anchor="end" class="eje rojo">presupuesto {fmt_num(ctx["presupuesto"])} velas</text>')
    if valores:
        paso = (ancho - x0 - 20) / len(valores)
        barra = max(3.0, paso * 0.7)
        for i, v in enumerate(valores):
            color = "#b3261e" if v > ctx["presupuesto"] else "#2451b8"
            partes.append(f'<rect x="{x0 + 6 + i*paso:.1f}" y="{y(v):.1f}" width="{barra:.1f}" height="{y0-y(v):.1f}" fill="{color}"/>')
    else:
        mensaje = "Sin órdenes con latencia completa (ruptura → orden) hoy."
        partes.append(f'<text x="{(ancho+x0)/2}" y="120" text-anchor="middle" class="eje">{esc(mensaje)}</text>')
    partes.append("</svg>")
    return "".join(partes)


CSS = """
:root{--fondo:#f3f1ea;--papel:#fff;--tinta:#16171a;--gris:#5c5b55;--gris2:#45443f;--linea:#d6d3c8;--linea2:#ebe8df;
--acento:#2451b8;--verde:#1f7a4d;--rojo:#b3261e;--mono:'JetBrains Mono',ui-monospace,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--tinta);font-family:'Space Grotesk','Helvetica Neue',sans-serif}
main{max-width:1440px;margin:0 auto;padding:32px 40px;display:flex;flex-direction:column;gap:20px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;padding-bottom:20px;border-bottom:1px solid var(--linea)}
.marca{display:flex;align-items:center;gap:16px}
.logo{width:48px;height:48px;background:var(--acento);border-radius:6px;display:grid;place-items:center}
h1{margin:0;font-size:28px;letter-spacing:.04em}
.sub,.mono{font-family:var(--mono);font-size:12px;color:var(--gris)}
.pildoras{display:flex;gap:10px;flex-wrap:wrap;font-family:var(--mono);font-size:12px}
.pildora{padding:8px 12px;border-radius:4px;background:var(--papel);border:1px solid var(--linea)}
.pildora.paper{border:1.5px solid var(--acento);color:var(--acento);font-weight:700}
.pildora.ok{background:#e6f1ea;color:#1b6a43;border-color:transparent}
.pildora.mal{background:#fbeceb;color:#8f1d17;border-color:transparent}
.problemas{background:#fbeceb;color:#8f1d17;border-radius:6px;padding:14px 18px;font-size:14px}
.problemas ul{margin:6px 0 0;padding-left:18px}
.fila{display:grid;gap:12px}
.c4{grid-template-columns:repeat(4,minmax(0,1fr))}.c5{grid-template-columns:repeat(5,minmax(0,1fr))}
.c3{grid-template-columns:repeat(3,minmax(0,1fr))}.c2{grid-template-columns:1.55fr 1fr}
.panel{background:var(--papel);border:1px solid var(--linea);border-radius:6px;padding:18px;display:flex;flex-direction:column;gap:10px;min-width:0}
.panel.oscuro{background:var(--tinta);color:#e9e7df;border-color:var(--tinta)}
.titulo{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.titulo h2{margin:0;font-size:16px;letter-spacing:.04em}
.etapa .nombre{font-size:20px;font-weight:700}.etapa .rol{font-size:13px;color:var(--gris2)}
.etapa .pie{display:flex;justify-content:space-between;padding-top:10px;border-top:1px dashed var(--linea)}
.punto{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px}
.punto::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.ok{color:var(--verde)}.alerta{color:var(--rojo)}.sin-datos{color:var(--gris)}
.kpi .valor{font-family:var(--mono);font-size:30px;font-weight:500}
.pos{color:var(--verde)}.neg{color:var(--rojo)}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:13px}
th{text-align:left;font-weight:400;font-size:11px;color:var(--gris);padding:8px 8px 8px 0;border-bottom:1px solid var(--linea)}
td{padding:8px 8px 8px 0;border-bottom:1px solid var(--linea2);color:var(--gris2)}
td.tk{color:var(--tinta);font-weight:700}
.oscuro td{border-color:#2c2d31;color:#c9c6bb}.oscuro td.tk{color:#fff}.oscuro .sub{color:#a9a69b}
.vacio{font-size:13px;color:var(--gris);padding:12px 0}
.oscuro .vacio{color:#a9a69b}
.duda{background:#f6f4ee;border-radius:4px;padding:10px 12px;display:flex;flex-direction:column;gap:4px}
.duda .cab{display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px}
.duda p{margin:0;font-size:13px;color:var(--gris2)}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;font-family:var(--mono)}
.stats b{display:block;font-size:18px;font-weight:500}
svg{width:100%;height:auto}.eje{font-family:var(--mono);font-size:10px;fill:var(--gris)}.eje.rojo{fill:var(--rojo)}
.nota{margin-top:auto;padding:10px 12px;background:#fbeceb;border-radius:4px;font-family:var(--mono);font-size:12px;color:#8f1d17}
.scroll{overflow-x:auto}
@media (max-width:1100px){.c5{grid-template-columns:repeat(3,minmax(0,1fr))}.c4{grid-template-columns:repeat(2,minmax(0,1fr))}.c2,.c3{grid-template-columns:1fr}}
@media (max-width:640px){main{padding:20px 16px}.c4,.c5{grid-template-columns:1fr 1fr}.kpi .valor{font-size:22px}}
"""


def render(ctx: dict) -> str:
    tz = ctx["tz"]
    etiqueta_tz = "UTC" if str(tz) == "UTC" else str(tz)

    problemas = ""
    if ctx["problemas"]:
        items = "".join(f"<li>{esc(p)}</li>" for p in ctx["problemas"])
        problemas = f'<section class="problemas" role="alert"><b>Datos incompletos.</b> Lo que no se pudo leer aparece como “—”.<ul>{items}</ul></section>'

    etapas = "".join(f"""
<div class="panel etapa">
  <div class="titulo"><span class="mono">{esc(e['donde'])}</span><span class="punto {e['estado']}">{ {'ok':'OK','alerta':'Revisar','sin-datos':'Sin datos'}[e['estado']] }</span></div>
  <div class="nombre">{esc(e['nombre'])}</div>
  <div class="rol">{esc(e['rol'])}</div>
  <div class="pie mono"><span>{esc(e['detalle'])}</span></div>
</div>""" for e in ctx["etapas"])

    signo = "" if ctx["pnl"] is None else ("pos" if ctx["pnl"] >= 0 else "neg")
    pct = "" if ctx["pnl_pct"] is None else f" ({ctx['pnl_pct']:+.2f}%)"
    ordenes_sub = "—" if ctx["n_rech"] is None else f"{ctx['n_rech']} rechazadas"
    lat_sub = "sin órdenes hoy" if ctx["lat_mediana"] is None else f"mediana del día · presupuesto {fmt_num(ctx['presupuesto'])}"
    kpis = [
        ("Equity paper", fmt_dinero(ctx["equity"]), "", "cuenta de práctica Alpaca"),
        ("P&L del día", fmt_dinero(ctx["pnl"], signo=True), signo, f"vs cierre anterior{pct}"),
        ("Posiciones", fmt_num(ctx["n_pos"]), "", "abiertas ahora"),
        ("Órdenes hoy", fmt_num(ctx["n_ord"]), "", ordenes_sub),
        ("Latencia", fmt_num(ctx["lat_mediana"], " velas"), "", lat_sub),
    ]
    kpis_html = "".join(
        f'<div class="panel kpi"><span class="mono">{esc(a)}</span><span class="valor {c}">{esc(b)}</span><span class="mono">{esc(d)}</span></div>'
        for a, b, c, d in kpis)

    if ctx["watch"]:
        filas = "".join(
            f"<tr><td class='tk'>{esc(w['ticker'])}</td><td>{esc(w['cap'])}</td><td>{esc(w['catalizador'])}</td>"
            f"<td>{_hora(w['detectado'], tz)}</td><td>{esc(w['estado'])}</td></tr>" for w in ctx["watch"])
        watch = f"<div class='scroll'><table><thead><tr><th>Ticker</th><th>Cap</th><th>Catalizador</th><th>Detectado</th><th>Estado</th></tr></thead><tbody>{filas}</tbody></table></div>"
    else:
        watch = '<p class="vacio">Sin tickers en observación ni cambios de estado hoy.</p>'

    if ctx["stream"]:
        filas = "".join(
            f"<tr><td>{esc(s['hora'])}</td><td class='tk'>{esc(s['ticker'])}</td><td>{esc(s['lado'])}</td>"
            f"<td>{esc(s['estado'])}</td><td>{esc(fmt_dinero(s['precio']))}</td></tr>" for s in ctx["stream"])
        stream = f"<div class='scroll'><table><tbody>{filas}</tbody></table></div>"
    else:
        stream = '<p class="vacio">Sin órdenes hoy.</p>'

    if ctx["dudas"]:
        dudas = "".join(
            f'<div class="duda"><div class="cab"><b>{esc(d["ticker"])}</b><span>{esc(d["hora"])}</span></div><p>{esc(d["motivo"])}</p></div>'
            for d in ctx["dudas"])
    else:
        dudas = ('<p class="vacio">El ejecutor no ha rechazado entradas hoy.</p>' if ctx["conteos_validos"]
                 else f'<p class="vacio">Sin datos: {esc(ctx["motivo_sin_datos"])}.</p>')

    if ctx["bloqueos"]:
        filas = "".join(f"<tr><td>{esc(n)}</td><td>{c}</td></tr>" for n, c in ctx["bloqueos"])
        riesgo = f"<table><thead><tr><th>Límite</th><th>Bloqueos</th></tr></thead><tbody>{filas}</tbody></table>"
        ub = ctx["ult_bloqueo"]
        riesgo += f'<div class="nota">Último: {esc(ub.get("ticker"))} a las {_hora(ub["_ts"], tz)}, {esc(ub.get("motivo") or ub.get("limite"))}</div>'
    else:
        riesgo = ('<p class="vacio">Ningún límite ha bloqueado operaciones hoy.</p>' if ctx["conteos_validos"]
                  else f'<p class="vacio">Sin datos: {esc(ctx["motivo_sin_datos"])}.</p>')

    sesion = '<span class="pildora ok">Sesión US abierta</span>' if ctx["en_sesion"] else '<span class="pildora">Sesión US cerrada</span>'
    alpaca = "" if ctx["alpaca_ok"] else '<span class="pildora mal">Alpaca sin conexión</span>'

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Momentum · panel paper</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Space+Grotesk:wght@400;500;700&display=swap">
<style>{CSS}</style>
</head>
<body>
<main>
<header>
  <div class="marca">
    <div class="logo"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="15 7 21 7 21 13"/></svg></div>
    <div><h1>MOMENTUM</h1><div class="sub">hernan-portafolio · hunter → watchlist.json → ejecutor</div></div>
  </div>
  <div class="pildoras">
    <span class="pildora paper">PAPER · ALPACA</span>{sesion}{alpaca}
    <span class="pildora">Actualizado {_hora(ctx['ahora'], tz, segundos=True)} {esc(etiqueta_tz)}</span>
    <span class="pildora">Solo lectura</span>
  </div>
</header>
{problemas}
<section class="fila c4" aria-label="Etapas del sistema">{etapas}</section>
<section class="fila c5" aria-label="Cifras clave">{kpis_html}</section>
<section class="fila c2">
  <div class="panel"><div class="titulo"><h2>Watchlist actual</h2><span class="mono">generada {_hora(ctx['wl_momento'], tz)}</span></div>{watch}</div>
  <div class="panel"><div class="titulo"><h2>Latencia</h2><span class="mono">ruptura → orden, velas de 1 min</span></div>
    {_grafico_latencia(ctx)}
    <div class="stats"><div><span class="mono">Mediana</span><b>{fmt_num(ctx['lat_mediana'])}</b></div><div><span class="mono">P90</span><b>{fmt_num(ctx['lat_p90'])}</b></div><div><span class="mono">Fuera de presupuesto</span><b class="neg">{fmt_num(ctx['lat_fuera'])}</b></div></div>
  </div>
</section>
<section class="fila c3">
  <div class="panel oscuro"><div class="titulo"><h2>Stream de ejecución</h2><span class="sub">órdenes paper de hoy</span></div>{stream}</div>
  <div class="panel"><div class="titulo"><h2>Dudas del ejecutor</h2><span class="mono">entradas que el LLM rechazó</span></div>{dudas}</div>
  <div class="panel"><div class="titulo"><h2>Límites de riesgo</h2><span class="mono">fail-closed</span></div>{riesgo}</div>
</section>
</main>
</body>
</html>"""


def escribir(html_texto: str, salida: Path) -> Path:
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / "index.html"
    temporal = salida / ".index.html.tmp"
    temporal.write_text(html_texto, encoding="utf-8")
    os.replace(temporal, destino)  # atómico: el navegador nunca ve un archivo a medias
    return destino


def main() -> int:
    cfg = cargar_config()
    ctx = construir(datetime.now(timezone.utc), cfg)
    try:
        destino = escribir(render(ctx), cfg["salida"])
    except OSError as exc:
        print(f"No se pudo escribir el panel: {exc}", file=sys.stderr)
        return 1
    for p in ctx["problemas"]:
        print(f"aviso: {p}", file=sys.stderr)
    print(f"Panel escrito en {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
