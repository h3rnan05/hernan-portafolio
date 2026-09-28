#!/usr/bin/env python3
"""Genera el panel del bot como una página HTML estática. Solo lectura.

Fuentes:
  - watchlist.json            salida del hunter (solo la marca de ruptura)
  - logs/events.jsonl         eventos escritos con dashboard.events.log_event
  - API de Alpaca PAPER       solo peticiones GET, endpoint fijo.
                              Posiciones, pendientes y cerradas hoy salen de aquí.
  - revisiones.json           solo el aviso de reconciliación, no qué se grafica
  - velas de 1 min            misma fuente que el hunter, con caché (dashboard/velas.py)
  - telemetría del hunter     la píldora de fuente de datos, si el campo existe

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
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard import gha as dg
from dashboard import velas as dv

# Catálogo de códigos de bloqueo del ejecutor (2026-09-23). Es un módulo
# de constantes sin dependencias; si no se puede importar (panel instalado
# sin el paper trader), el panel lo dice y trata todo código como nuevo:
# mejor un "Revisar" de más que un límite invisible.
try:
    from momentum_paper_trader import bloqueos as _catalogo_bloqueos
except Exception:  # pragma: no cover - sin paper trader instalado
    _catalogo_bloqueos = None

ALPACA_PAPER = "https://paper-api.alpaca.markets"  # fijo: el panel nunca habla con la cuenta real
REPO = Path(__file__).resolve().parents[1]
# Sin DASH_CACHE_VELAS la caché va al directorio temporal del sistema,
# NUNCA al árbol del repo: un archivo suelto dentro de /opt/hernan-portafolio
# rompe el `git pull --rebase` del wrapper (medido 2026-09-18).
CACHE_VELAS_DEFECTO = Path(tempfile.gettempdir()) / "momentum-dashboard-cache"
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
    revisiones = os.environ.get("DASH_REVISIONES", "").strip()
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
        # GitHub Actions (momentum_hunter.yml) sin correr en sesión más de
        # esto es alerta, aunque el VPS esté sano: el respaldo se cayó.
        "gha_max_min": _env_float("DASH_GHA_MAX_MIN", 45.0),
        "tz": ZoneInfo(os.environ.get("DASH_TZ", "UTC")),
        # Velas de los tickers en operación: caché fuera de git (en el VPS,
        # junto al HTML) y tope de tickers por corrida para no saturar a Yahoo.
        "cache_velas": Path(os.environ.get("DASH_CACHE_VELAS") or CACHE_VELAS_DEFECTO),
        "velas_ttl_seg": _env_float("DASH_VELAS_TTL_SEG", 120.0),
        "velas_max_tickers": int(_env_float("DASH_VELAS_MAX_TICKERS", 6.0) or 6),
        "velas_pausa_seg": _env_float("DASH_VELAS_PAUSA_SEG", 900.0),
        # Última corrida exitosa del hunter en GitHub Actions (dashboard/gha.py):
        # API pública, sin token, con caché en la misma carpeta que las velas.
        # Sin repo configurado el panel no pregunta y el Hunter queda "Sin datos".
        "gha_repo": os.environ.get("DASH_GHA_REPO", "h3rnan05/hernan-portafolio").strip(),
        "gha_workflow": os.environ.get("DASH_GHA_WORKFLOW", "momentum_hunter.yml").strip(),
        "gha_ttl_seg": _env_float("DASH_GHA_TTL_SEG", 300.0),
        # Escaneos del hunter en el VPS: telemetría JSONL por fuente
        # (momentum_hunter/telemetria/{fecha}/vps/events.jsonl).
        "telem_hunter": Path(os.environ.get("DASH_TELEM_HUNTER", "momentum_hunter/telemetria")),
        # Archivo de pausa del BOT ante un 429 de Yahoo (solo lectura).
        "pausa_bot": (Path(os.environ["DASH_YAHOO_PAUSA_BOT"]) if os.environ.get("DASH_YAHOO_PAUSA_BOT") else None),
        # Libro del ejecutor, SOLO para el aviso de reconciliación (¿el
        # broker tiene algo que nadie sigue?). No decide qué está abierto:
        # eso lo dice Alpaca. Ruta del paquete, no del cwd: el servicio y
        # un `python -m` lanzado desde otro directorio leen el mismo archivo
        # que escribe `estado.guardar`.
        "revisiones": Path(revisiones) if revisiones else REPO / "momentum_paper_trader" / "revisiones.json",
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


def _estados_fusionados(lista: list, ruta_estado: Path | None):
    """Estado y `actualizado_en` de cada entrada tras aplicar el overlay VPS
    con las MISMAS reglas que usa el ejecutor (`watchlist.aplicar_overlay`,
    vía `cargar_con_overlay`). Devuelve
    ({(ticker, creado_en): (estado, actualizado_en)}, error).

    Antes el panel ponía el estado del overlay encima del canónico sin más
    reglas: con el rechequeo del VPS apagado, el overlay se quedaba viejo y
    mostraba "watching" para una entrada que GHA ya había expirado (SUNB,
    2026-09-11). Con las reglas de `watchlist.py` el canónico terminal gana
    y un overlay más viejo que el canónico no pisa nada.

    Clave (ticker, creado_en): un ticker puede aparecer dos veces (la
    EXPIRED vieja y el intento nuevo). Lo que no se pueda fusionar (formato
    ajeno, entrada incompleta) se queda con el canónico."""
    if ruta_estado is None:
        return {}, None
    _, err = leer_estado_vps(ruta_estado)
    if err:
        return {}, err
    try:
        from momentum_hunter import watchlist as wl
    except ImportError as exc:
        return {}, (f"No se pudo cargar momentum_hunter.watchlist ({type(exc).__name__}); "
                    "se muestra el canónico sin el estado VPS.")
    crudas = [{"nombre": None, **x} for x in lista if isinstance(x, dict)]
    entradas = wl.aplicar_overlay(wl.parsear({"entradas": crudas}), ruta_estado)
    return {(e.ticker, e.creado_en): (e.estado, e.actualizado_en) for e in entradas}, None


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

    fusionados, err_estado = _estados_fusionados(lista, ruta_estado)

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
        # Estado y fecha ya fusionados con las reglas de `watchlist.py`; sin
        # fusión (formato ajeno, sin overlay) manda el canónico.
        estado, actualizado = fusionados.get(
            (ticker, x.get("creado_en")),
            (_primero(x, "estado", "status", "state"), _primero(x, "actualizado_en", "updated_at")))
        items.append({
            "ticker": ticker,
            "cap": _cap(x),
            "catalizador": catalizador,
            "detectado": parse_ts(_primero(x, "detectado", "detected_at", "creado_en", "timestamp", "added_at", "ts")),
            "estado": estado,
            "actualizado": parse_ts(actualizado),
            # Nivel de ruptura que calculó el hunter ("la entrada que se
            # esperaba"). Ausente = None: el panel dice "sin dato".
            "ruptura": num(x.get("ultima_zona_entrada_baja")),
            "creado_en": parse_ts(x.get("creado_en")),
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


def _codigo_de(evento: dict) -> str:
    if _catalogo_bloqueos is not None:
        return _catalogo_bloqueos.codigo_de_evento(evento)
    codigo = evento.get("codigo") or evento.get("limite") or "SIN_CODIGO"
    return str(codigo).upper()


def resumir_bloqueos(bloqueos: list[dict], capacidad: list[dict], tz, ahora: datetime) -> dict:
    """Bloqueos únicos por (ticker, código) con veces y última hora, la
    capacidad llena por código con primera/última hora y corridas, y el
    veredicto: `revisar` solo con un DATO_FALTANTE o un código nuevo.

    Por qué (2026-09-23): 942 eventos crudos contra 7 decisiones eran 8
    señales × 120 ticks contra UN tope de posiciones lleno. El panel
    contaba repeticiones y pedía revisar un sistema que funcionaba."""
    conocidos = set(_catalogo_bloqueos.CODIGOS_CONOCIDOS) if _catalogo_bloqueos is not None else set()

    def _es_dato_faltante(codigo: str) -> bool:
        if _catalogo_bloqueos is not None:
            return _catalogo_bloqueos.es_dato_faltante(codigo)
        return codigo.startswith("DATO_FALTANTE:")

    unicos: dict[tuple[str, str], dict] = {}
    for b in bloqueos:
        codigo = _codigo_de(b)
        clave = (str(b.get("ticker") or "—"), codigo)
        fila = unicos.setdefault(clave, {"ticker": clave[0], "codigo": codigo, "veces": 0, "ultimo": None,
                                         "motivo": str(b.get("motivo") or "")})
        fila["veces"] += 1
        if fila["ultimo"] is None or b["_ts"] > fila["ultimo"]:
            fila["ultimo"] = b["_ts"]
    filas = sorted(unicos.values(), key=lambda f: (-f["veces"], f["ticker"]))
    for f in filas:
        f["hora"] = _hora(f["ultimo"], tz, ahora=ahora)

    por_codigo_cap: dict[str, dict] = {}
    for c in capacidad:
        codigo = _codigo_de(c)
        fila = por_codigo_cap.setdefault(codigo, {"codigo": codigo, "corridas": 0, "primero": None, "ultimo": None,
                                                  "motivo": str(c.get("motivo") or "")})
        fila["corridas"] += 1
        if fila["primero"] is None or c["_ts"] < fila["primero"]:
            fila["primero"] = c["_ts"]
        if fila["ultimo"] is None or c["_ts"] > fila["ultimo"]:
            fila["ultimo"] = c["_ts"]
    capacidad_filas = sorted(por_codigo_cap.values(), key=lambda f: -f["corridas"])
    for f in capacidad_filas:
        f["desde"] = _hora(f["primero"], tz, ahora=ahora)
        f["hasta"] = _hora(f["ultimo"], tz, ahora=ahora)

    codigos = {f["codigo"] for f in filas} | {f["codigo"] for f in capacidad_filas}
    dato_faltante = sorted(c for c in codigos if _es_dato_faltante(c))
    nuevos = sorted(c for c in codigos if c not in conocidos and not _es_dato_faltante(c))
    return {
        "eventos": len(bloqueos), "unicos": filas, "capacidad": capacidad_filas,
        "dato_faltante": dato_faltante, "codigos_nuevos": nuevos,
        "revisar": bool(dato_faltante or nuevos),
        "sin_catalogo": _catalogo_bloqueos is None,
    }


def _detalle_riesgo(riesgo: dict, hay_eventos: bool, conteos_validos: bool) -> str:
    """Una línea: únicos (eventos), capacidad llena, y por qué "Revisar"."""
    if not hay_eventos or not (riesgo["unicos"] or riesgo["capacidad"] or conteos_validos):
        return "— bloqueos hoy"
    partes = [f"{len(riesgo['unicos'])} bloqueos únicos ({riesgo['eventos']} eventos) hoy"]
    for c in riesgo["capacidad"]:
        partes.append(f"capacidad llena: {c['codigo']} desde {c['desde']} ({c['corridas']} corridas)")
    if riesgo["dato_faltante"]:
        partes.append("revisar: " + ", ".join(riesgo["dato_faltante"]))
    if riesgo["codigos_nuevos"]:
        partes.append("motivo nuevo: " + ", ".join(riesgo["codigos_nuevos"]))
    return " · ".join(partes)


ESTADOS_ORDEN = {
    "filled": "ejecutada", "partially_filled": "parcial", "rejected": "rechazada",
    "canceled": "cancelada", "expired": "expirada", "new": "abierta", "accepted": "abierta",
    "pending_new": "abierta", "held": "en espera",
}


ESTADOS_ACTIVOS = ("watching", "triggered")


def filtrar_watchlist(items: list[dict], desde: datetime) -> list[dict]:
    """Activas siempre; terminales solo si cambiaron hoy. Activas primero, luego lo más reciente."""
    def activo(w):
        return str(w.get("estado") or "").lower() in ESTADOS_ACTIVOS

    visibles = [w for w in items if activo(w) or (w.get("actualizado") and w["actualizado"] >= desde)]
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(visibles, key=lambda w: (not activo(w), -(w.get("actualizado") or minimo).timestamp()))


def construir(ahora: datetime, cfg: dict, get=alpaca_get, velas=None, gha=None) -> dict:
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

    watch_todas, wl_momento, err = leer_watchlist(cfg["watchlist"], cfg.get("watchlist_estado"))
    if err:
        problemas.append(err)
    watch = filtrar_watchlist(watch_todas, desde)

    cuenta, err_c = get("/v2/account")
    posiciones, err_p = get("/v2/positions")
    ordenes, err_o = get("/v2/orders", {
        "status": "all", "limit": 500, "direction": "desc", "nested": "true",
        "after": desde.astimezone(timezone.utc).isoformat(),
    })
    # Órdenes abiertas sin filtro de fecha: el stop de una posición abierta
    # ayer no aparece entre las órdenes de hoy. `nested=true` es obligatorio:
    # con el bracket ya lleno Alpaca deja el stop en `held` dentro de `legs`
    # del take-profit y no lo manda como fila de `status=open` (MNST, 28/9).
    abiertas, err_a = get("/v2/orders", {"status": "open", "limit": 500, "nested": "true"})
    # Cerradas sin filtro de fecha: el fill de entrada de lo que se cerró
    # hoy puede ser de otro día. Sin ese precio el P&L realizado queda "—".
    # 500 es el tope de Alpaca; si la entrada queda más atrás, no se inventa.
    historicas, err_h = get("/v2/orders", {
        "status": "closed", "limit": 500, "direction": "desc", "nested": "true",
    })
    for e in (err_c, err_p, err_o, err_a, err_h):
        if e and e not in problemas:
            problemas.append(e)
    alpaca_ok = not (err_c or err_p or err_o)

    # Curva de equity: dos vistas del mismo endpoint (solo GET). Un error
    # de Alpaca va a "problemas"; un historial vacío o relleno con ceros
    # es "Sin datos" en el gráfico, nunca una línea en cero.
    equity_dia = _historial_equity(get, {"period": "1D", "timeframe": "5Min"}, problemas, ahora)
    equity_mes = _historial_equity(get, {"period": "1M", "timeframe": "1D"}, problemas, ahora)

    equity = num(cuenta.get("equity")) if isinstance(cuenta, dict) else None
    last_equity = num(cuenta.get("last_equity")) if isinstance(cuenta, dict) else None
    # Número de cuenta paper (no es una credencial: es el identificador que
    # Alpaca muestra en su propio panel). Se enseña para poder comprobar a
    # simple vista que el panel mira LA MISMA cuenta que el ejecutor.
    cuenta_numero = str(cuenta.get("account_number")).strip() if isinstance(cuenta, dict) and cuenta.get("account_number") else None
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
    # El VPS registra `persist_fallido` cuando no consigue subir su estado
    # (revisiones, archivo, telemetría paper) a main. Mientras eso pase,
    # GitHub y el panel de GHA miran datos viejos: se pinta en rojo.
    persist_fallidos = [
        {"hora": _hora(e["_ts"], cfg["tz"], ahora=ahora),
         "motivo": str(e.get("motivo") or "sin motivo registrado"),
         "intentos": e.get("intentos")}
        for e in eventos if e.get("tipo") == "persist_fallido"
    ]
    # La IA no pudo decidir (saldo, clave, API, veredicto ilegible) y el
    # ejecutor dejó la señal TRIGGERED. No es un persist fallido: el git
    # puede estar sano y aun así nadie se entera de que no se opera.
    ia_fallos = [
        {"hora": _hora(e["_ts"], cfg["tz"], ahora=ahora),
         "codigo": str(e.get("codigo") or "api"),
         "motivo": str(e.get("motivo") or "fallo técnico de la IA"),
         "consecutivos": e.get("consecutivos")}
        for e in eventos if e.get("tipo") == "ia_fallo_tecnico"
    ]

    ult_rechequeo = rechequeos[-1]["_ts"] if rechequeos else None
    ult_decision = decisiones[-1]["_ts"] if decisiones else None

    por_limite: dict[str, int] = {}
    for b in bloqueos:
        nombre = str(b.get("limite") or "sin nombre")
        por_limite[nombre] = por_limite.get(nombre, 0) + 1

    # Bloqueos ÚNICOS (2026-09-23): el mismo ticker bloqueado por el mismo
    # motivo en 120 ticks es UN hecho repetido, no 120. Se cuenta por
    # (ticker, código) con veces y última hora. "Revisar" solo si hay un
    # bloqueo por DATO faltante/nulo/viejo (`DATO_FALTANTE:<campo>`) o un
    # código que el catálogo no conoce; un límite conocido haciendo su
    # trabajo es "OK", por muchas veces que se repita.
    riesgo = resumir_bloqueos(bloqueos, [e for e in eventos if e.get("tipo") == "capacidad_llena"],
                              cfg["tz"], ahora)
    if riesgo["sin_catalogo"]:
        problemas.append("No se pudo cargar el catálogo de códigos de bloqueo (momentum_paper_trader.bloqueos): "
                         "todo código se trata como nuevo.")

    estado_rechequeo = _estado_frescura(_edad_min(ahora, ult_rechequeo), cfg["rechequeo_max_min"], en_sesion)
    # Un 0 solo es un dato si hay log Y el bot corrió hace poco. Sin rechequeo
    # reciente, "0 decisiones" o "0 bloqueos" no significa que no haya pasado nada.
    conteos_validos = hay_eventos and estado_rechequeo == "ok"
    motivo_sin_datos = ("no hay log de eventos" if not hay_eventos
                        else "no hay un rechequeo reciente")

    # Última corrida de GitHub Actions, SOLO para mostrarla como dato al
    # lado (respaldo del escaneo desde el 21/9): ya no decide el estado del
    # Hunter -- eso lo hace su escaneo del VPS, más abajo. Sin respuesta de
    # Actions y sin copia en caché, simplemente no se muestra ese dato.
    cache_dir = cache_velas_segura(cfg.get("cache_velas"), problemas)
    if gha is None:
        def gha():
            if not cfg.get("gha_repo"):
                return {"corrida": None, "obtenido": None, "origen": None,
                        "error": "GitHub Actions no configurado (DASH_GHA_REPO)"}
            return dg.obtener(cfg["gha_repo"], cfg.get("gha_workflow", "momentum_hunter.yml"),
                              ahora, cache_dir, cfg.get("gha_ttl_seg", 300.0))
    hunter_gha = gha()
    corrida = hunter_gha.get("corrida")
    gha_momento = parse_ts(corrida["terminada"]) if corrida else None

    # El escaneo corre en el VPS (2026-09-21): el estado del Hunter sale de
    # su telemetría de hoy. GitHub solo es respaldo; su última corrida se
    # muestra al lado como dato, sin decidir el estado. Sin escaneo del VPS
    # hoy: "Sin datos", aunque la watchlist o GitHub sean frescos.
    escaneo = ultimo_escaneo_vps(cfg.get("telem_hunter"), ahora)
    hunter_momento = escaneo["fin"] if escaneo else None
    if escaneo:
        slot = f" · slot {escaneo['slot']}/{escaneo['n_slots']}" if escaneo.get("slot") is not None else ""
        detalle_hunter = (f"escaneo VPS {_cuando(hunter_momento, cfg['tz'], ahora)}{slot}"
                          f" · {escaneo['evaluadas']} evaluadas · watchlist {_cuando(wl_momento, cfg['tz'], ahora)}")
    else:
        detalle_hunter = f"sin escaneo del VPS hoy · watchlist {_cuando(wl_momento, cfg['tz'], ahora)}"
    if corrida:
        detalle_hunter += f" · GitHub #{corrida['numero']} {_cuando(gha_momento, cfg['tz'], ahora)}"
    # GitHub Actions (momentum_hunter.yml) dejó de ser el escáner el 21/9:
    # ahora escanea el VPS y GitHub quedó SOLO como respaldo manual, que
    # legítimamente puede pasar horas sin correr. Por eso su atraso ya NO
    # pinta el Hunter en rojo ni entra a "problemas" -- antes daba una
    # alerta permanente falsa ("GitHub lleva N min sin correr") aunque el
    # escaneo del VPS estuviera fresco (2026-09-24). La salud del Hunter la
    # decide su escaneo del VPS; la última corrida de GitHub sigue arriba
    # como dato informativo.
    estado_hunter = _estado_frescura(_edad_min(ahora, hunter_momento), cfg["hunter_max_min"], en_sesion)
    etapas = [
        {
            "nombre": "Hunter", "donde": "VPS",
            "rol": "Busca candidatos. Determinista, sin IA ni bróker.",
            "estado": estado_hunter,
            "detalle": detalle_hunter,
        },
        {
            "nombre": "Rechequeo", "donde": "VPS",
            "rol": "Revisa la watchlist con --solo-watchlist.",
            # Un persist fallido es alerta aunque la corrida sea fresca: el
            # bot corrió, pero su estado no llegó a main.
            "estado": "alerta" if persist_fallidos else estado_rechequeo,
            "detalle": (f"última corrida {_hora(ult_rechequeo, cfg['tz'], ahora=ahora)}"
                        + (f" · {len(persist_fallidos)} persist fallidos, último {persist_fallidos[-1]['hora']}"
                           if persist_fallidos else "")),
        },
        {
            "nombre": "Ejecutor", "donde": "VPS",
            "rol": "Consulta al LLM y decide si entra.",
            # Mismo criterio que Riesgo: con log y rechequeo reciente, 0
            # decisiones es un dato ("OK", "0 decisiones"). "Sin datos" solo
            # si falta el log o no hay un rechequeo reciente. Un fallo
            # sostenido de la IA es alerta aunque el rechequeo sea fresco:
            # el bot corrió y no pudo decidir.
            "estado": "alerta" if ia_fallos else ("ok" if conteos_validos else "sin-datos"),
            # Un conteo > 0 es real aunque el rechequeo esté viejo; un 0 no.
            "detalle": (
                (f"{len(decisiones)} decisiones hoy · última {_hora(ult_decision, cfg['tz'], ahora=ahora)}"
                 if hay_eventos and (decisiones or conteos_validos)
                 else "— decisiones hoy · última —")
                + (f" · {len(ia_fallos)} fallos de IA, último {ia_fallos[-1]['hora']}"
                   if ia_fallos else "")
            ),
        },
        {
            "nombre": "Riesgo", "donde": "Código",
            "rol": "Límites deterministas. Sin margen. Fail-closed.",
            # "Revisar" solo con un DATO_FALTANTE o un código nuevo (2026-09-23).
            # Un límite conocido bloqueando es el sistema funcionando: "OK"
            # si los conteos son válidos. Un bloqueo registrado sigue siendo
            # un hecho aunque el rechequeo esté viejo: se muestra igual.
            "estado": ("alerta" if riesgo["revisar"]
                       else ("ok" if conteos_validos or bloqueos or riesgo["capacidad"] else "sin-datos")),
            "detalle": _detalle_riesgo(riesgo, hay_eventos, conteos_validos),
        },
    ]

    stream = []
    for o in (lista_ordenes or [])[:12]:
        stream.append({
            "hora": _hora(parse_ts(o.get("submitted_at")), cfg["tz"], segundos=True, ahora=ahora),
            "ticker": o.get("symbol") or "—",
            "lado": {"buy": "compra", "sell": "venta"}.get(o.get("side"), o.get("side") or "—"),
            "estado": ESTADOS_ORDEN.get(o.get("status"), o.get("status") or "—"),
            "precio": num(o.get("filled_avg_price")),
        })

    dudas = [
        {"ticker": d.get("ticker") or "—", "hora": _hora(d["_ts"], cfg["tz"], ahora=ahora),
         "motivo": str(d.get("motivo") or "sin motivo registrado")}
        for d in reversed(decisiones) if d.get("entra") is False
    ][:6]

    lista_posiciones = posiciones if isinstance(posiciones, list) else []
    abiertas_lista = abiertas if isinstance(abiertas, list) else []
    # Marcas (fill, stop): hoy + abiertas. El historial cerrado NO entra
    # aquí: un stop ya filled de un trade viejo no es el stop de ahora.
    todas_ordenes = _unir_ordenes(lista_ordenes or [], abiertas_lista)
    if velas is None:
        def velas(ticker):
            return dv.obtener(ticker, ahora, cache_dir, cfg.get("velas_ttl_seg", 120.0),
                              pausa_seg=cfg.get("velas_pausa_seg", 900.0),
                              pausa_bot=cfg.get("pausa_bot"))
    # Gráficos solo de lo que sigue vivo. El 28/9 DLB, NBIS y TWST se
    # pintaban "en operación" porque tuvieron orden hoy, ya cerradas.
    if err_p is None:
        tickers_op = tickers_en_operacion(
            lista_posiciones, abiertas_lista if err_a is None else [])
    else:
        tickers_op = []
    tope = int(cfg.get("velas_max_tickers", 6))
    operaciones = [{
        "ticker": t,
        "rol": rol,
        "velas": velas(t),
        "marcas": marcas_de(t, lista_posiciones, todas_ordenes, watch_todas),
    } for t, rol in tickers_op[:tope]]
    omitidos = [t for t, _rol in tickers_op[tope:]]

    vivas = None if err_a is not None else ordenes_para_detectar(abiertas_lista)
    filas_pos = None if err_p is not None else [
        fila_posicion(p, vivas or [], ordenes_conocidas=vivas is not None)
        for p in lista_posiciones if isinstance(p, dict) and p.get("symbol")]
    filas_pend = None if err_a is not None else filas_pendientes(abiertas_lista, _simbolos(lista_posiciones))
    if err_p is not None or (err_o is not None and err_h is not None):
        filas_cerradas = None
    else:
        fuentes_cierre = []
        if isinstance(ordenes, list):
            fuentes_cierre.append(ordenes)
        if isinstance(historicas, list):
            fuentes_cierre.append(historicas)
        filas_cerradas = cierres_de_hoy(
            _unir_aplanadas(*fuentes_cierre), _simbolos(lista_posiciones), desde)
    avisos, nota_seguimiento = contrastar_broker(
        lista_posiciones if err_p is None else None,
        vivas,
        cfg.get("revisiones"),
    )

    return {
        "ahora": ahora, "tz": cfg["tz"], "en_sesion": en_sesion, "alpaca_ok": alpaca_ok,
        "operaciones": operaciones, "operaciones_omitidas": omitidos,
        "hay_alpaca_operaciones": err_p is None,
        "posiciones_broker": filas_pos,
        "pendientes_broker": filas_pend,
        "cerradas_hoy": filas_cerradas,
        "avisos_broker": avisos,
        "nota_seguimiento": nota_seguimiento,
        "fuente_datos": fuente_datos_activa(cfg.get("telem_hunter"), ahora),
        "problemas": problemas, "etapas": etapas,
        "equity": equity, "pnl": pnl, "pnl_pct": pnl_pct, "cuenta_numero": cuenta_numero,
        "desajuste_equity": _desajuste_equity(equity_mes, equity, last_equity),
        "n_pos": n_pos, "n_ord": n_ord, "n_rech": n_rech,
        "watch": watch, "wl_momento": wl_momento,
        "hunter_gha": hunter_gha, "hunter_momento": hunter_momento, "hunter_escaneo": escaneo,
        "lat": lat, "lat_mediana": statistics.median(valores) if valores else None,
        "lat_p90": percentil(valores, 90),
        "lat_fuera": sum(1 for v in valores if v > presupuesto) if valores else None,
        "presupuesto": presupuesto,
        "stream": stream, "dudas": dudas,
        "equity_dia": equity_dia, "equity_mes": equity_mes,
        "bloqueos": sorted(por_limite.items(), key=lambda kv: -kv[1]),
        "riesgo": riesgo,
        "persist_fallidos": persist_fallidos,
        "ia_fallos": ia_fallos,
        "hay_eventos": hay_eventos,
        "conteos_validos": conteos_validos, "motivo_sin_datos": motivo_sin_datos,
        "ult_bloqueo": bloqueos[-1] if bloqueos else None,
    }


# ───────────────────────── curva de equity ─────────────────────────

RUTA_HISTORIAL = "/v2/account/portfolio/history"


def serie_equity(datos, ahora: datetime | None = None) -> tuple[list[tuple[datetime, float]], float | None]:
    """(puntos [(momento, equity)], equity inicial) a partir de la respuesta
    de `GET /v2/account/portfolio/history`.

    Un punto sin marca de tiempo o sin equity se salta. Un equity en 0 o
    negativo también: Alpaca rellena con 0 los tramos donde la cuenta no
    tenía valor (antes de fondearla, fuera de sesión), y una cuenta paper
    de verdad nunca vale 0. Dibujar esos ceros sería inventar una caída a
    cero que no ocurrió. Un punto posterior a `ahora` tampoco se dibuja:
    una equity "futura" no puede ser un dato medido. `base_value` es la
    equity al inicio del periodo, tal cual la manda Alpaca; si no viene,
    no se inventa."""
    if not isinstance(datos, dict):
        return [], None
    marcas, valores = datos.get("timestamp"), datos.get("equity")
    if not isinstance(marcas, list) or not isinstance(valores, list):
        return [], None
    puntos = []
    for marca, valor in zip(marcas, valores):
        momento, equity = parse_ts(marca), num(valor)
        if momento is None or equity is None or equity <= 0:
            continue
        if ahora is not None and momento > ahora:
            continue
        puntos.append((momento, equity))
    puntos.sort(key=lambda p: p[0])
    base = num(datos.get("base_value"))
    return puntos, (base if base is not None and base > 0 else None)


def _historial_equity(get, params: dict, problemas: list, ahora: datetime) -> dict:
    """`sesion` es la fecha (de Nueva York, que es la del mercado) del
    último punto real, y `es_hoy` si esa fecha es la de `ahora`. Hace
    falta porque `period=1D` NO significa "hoy": Alpaca devuelve el
    último día de mercado, y antes de la apertura ese día es el anterior
    (una madrugada de lunes trae la sesión completa del viernes). El
    panel tiene que decir de qué día son los puntos, no suponerlo."""
    datos, err = get(RUTA_HISTORIAL, params)
    if err:
        if err not in problemas:
            problemas.append(err)
        return {"puntos": [], "base": None, "error": err, "sesion": None, "es_hoy": False}
    puntos, base = serie_equity(datos, ahora)
    sesion = puntos[-1][0].astimezone(NY).date() if puntos else None
    return {"puntos": puntos, "base": base, "error": None, "sesion": sesion,
            "es_hoy": sesion is not None and sesion == ahora.astimezone(NY).date()}


def ultimo_escaneo_vps(dir_telemetria: Path | None, ahora: datetime) -> dict | None:
    """Último escaneo completo del VPS de HOY (fecha UTC, como escribe
    momentum_hunter.telemetria): {"fin", "inicio", "slot", "n_slots",
    "evaluadas"}. Sin archivo, sin registros de modo "escaneo" o con un
    timestamp ilegible: None. Nunca se estima nada."""
    if dir_telemetria is None:
        return None
    ruta = Path(dir_telemetria) / ahora.astimezone(timezone.utc).date().isoformat() / "vps" / "events.jsonl"
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    ultimo = None
    for linea in lineas:
        try:
            r = json.loads(linea)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("modo") != "escaneo":
            continue
        fin = parse_ts(r.get("timestamp"))
        if fin is None:
            continue
        embudo = r.get("embudo") if isinstance(r.get("embudo"), dict) else {}
        evaluadas = embudo.get("evaluadas") if isinstance(embudo.get("evaluadas"), dict) else {}
        registro = {"fin": fin, "inicio": parse_ts(r.get("inicio_ts")),
                    "slot": r.get("slot") if isinstance(r.get("slot"), int) else None,
                    "n_slots": r.get("n_slots") if isinstance(r.get("n_slots"), int) else None,
                    "evaluadas": sum(v for v in evaluadas.values() if isinstance(v, (int, float)))}
        if ultimo is None or registro["fin"] >= ultimo["fin"]:
            ultimo = registro
    return ultimo


def _fecha_corta(fecha) -> str:
    """"vie 18 sep": el día con nombre para que una sesión vieja nunca se
    lea como la de hoy."""
    return f"{DIAS_ES[fecha.weekday()]} {fecha.day} {MESES_ES[fecha.month - 1]}"


def _etiqueta_x(momento: datetime, tz, modo: str) -> str:
    local = momento.astimezone(tz)
    if modo == "dia":
        return local.strftime("%H:%M")
    return f"{local.day} {MESES_ES[local.month - 1]}"


def _desajuste_equity(hist: dict, equity: float | None, last_equity: float | None) -> str | None:
    """Aviso cuando el historial y la cuenta no cuentan la misma historia.

    El último punto del historial diario es, por construcción, o el valor de
    hoy (≈ `equity`) o el cierre anterior (= `last_equity`). Si no se parece
    a ninguno de los dos (0,5 % de tolerancia por el desfase de segundos
    entre las dos consultas), algo está mal: lo más probable es que las
    credenciales del panel apunten a otra cuenta paper que las del
    ejecutor. No se corrige ni se oculta nada: se avisa con los dos números
    para que la persona lo compruebe."""
    if not hist.get("puntos"):
        return None
    ultimo = hist["puntos"][-1][1]
    referencias = [r for r in (equity, last_equity) if r is not None and r > 0]
    if not referencias or any(abs(ultimo - r) / r <= 0.005 for r in referencias):
        return None
    return (f"El historial no cuadra con la cuenta: último cierre del historial {fmt_dinero(ultimo)} "
            f"vs equity {fmt_dinero(equity)} / cierre anterior {fmt_dinero(last_equity)}. "
            "Confirmar que el panel y el ejecutor usan la misma cuenta paper.")


def _grafico_equity(hist: dict, tz, modo: str) -> str:
    """Línea de equity en SVG, sin librerías. `modo` es "dia" (velas de 5
    min) o "mes" (cierre diario) y solo cambia las etiquetas del eje X.

    Sin puntos: "Sin datos" en el área del gráfico y ninguna línea. Con
    puntos: la línea va de min a max REALES (una serie plana se dibuja
    plana, con un margen fijo para que no quede pegada al borde), y la
    equity inicial se marca con una línea punteada."""
    ancho, alto, x0, x1, y0, y1 = 480, 230, 62, 470, 196, 14
    etiqueta = {"dia": "Equity de hoy, velas de 5 minutos", "mes": "Equity del último mes, cierre diario"}[modo]
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="{esc(etiqueta)}">',
              f'<line x1="{x0}" y1="{y1}" x2="{x0}" y2="{y0}" class="rejilla"/>',
              f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" class="rejilla"/>']
    puntos, base = hist["puntos"], hist["base"]
    if not puntos:
        motivo = "Alpaca no respondió" if hist.get("error") else "sin historial de equity"
        partes.append(f'<text x="{(x0+x1)/2}" y="110" text-anchor="middle" class="eje">Sin datos</text>')
        partes.append(f'<text x="{(x0+x1)/2}" y="128" text-anchor="middle" class="eje">{esc(motivo)}</text>')
        partes.append("</svg>")
        return "".join(partes)

    valores = [v for _, v in puntos]
    candidatos = valores + ([base] if base is not None else [])
    minimo, maximo = min(candidatos), max(candidatos)
    if maximo - minimo < 1e-9:
        # Serie plana de verdad: margen del 0,5 % (mínimo $1) a cada lado
        # para que la línea se vea, sin cambiar su forma.
        margen = max(1.0, minimo * 0.005)
    else:
        margen = (maximo - minimo) * 0.08
    lo, hi = minimo - margen, maximo + margen
    t_ini, t_fin = puntos[0][0].timestamp(), puntos[-1][0].timestamp()

    def y(v: float) -> float:
        return y0 - (v - lo) / (hi - lo) * (y0 - y1)

    def x(t: float) -> float:
        # Un solo punto (o todos en el mismo instante): al centro.
        if t_fin - t_ini < 1:
            return (x0 + x1) / 2
        return x0 + (t - t_ini) / (t_fin - t_ini) * (x1 - x0)

    for valor in (minimo, maximo):
        partes.append(f'<text x="{x0-6}" y="{y(valor)+4:.1f}" text-anchor="end" class="eje">{esc(fmt_dinero(valor))}</text>')
    if base is not None:
        yb = y(base)
        partes.append(f'<line x1="{x0}" y1="{yb:.1f}" x2="{x1}" y2="{yb:.1f}" class="base-punteada" stroke-width="1" stroke-dasharray="5 4"/>')
        partes.append(f'<text x="{x1}" y="{yb-6:.1f}" text-anchor="end" class="eje">inicial {esc(fmt_dinero(base))}</text>')
    coords = " ".join(f"{x(t.timestamp()):.1f},{y(v):.1f}" for t, v in puntos)
    partes.append(f'<polyline points="{coords}" fill="none" class="serie" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    if len(puntos) == 1:
        partes.append(f'<circle cx="{x(t_ini):.1f}" cy="{y(valores[0]):.1f}" r="3" class="serie-punto"/>')
    partes.append(f'<text x="{x0}" y="{alto-6}" class="eje">{esc(_etiqueta_x(puntos[0][0], tz, modo))}</text>')
    if len(puntos) > 1:
        partes.append(f'<text x="{x1}" y="{alto-6}" text-anchor="end" class="eje">{esc(_etiqueta_x(puntos[-1][0], tz, modo))}</text>')
    partes.append("</svg>")
    return "".join(partes)


def _resumen_equity(hist: dict) -> str:
    """Inicial / último / variación bajo cada gráfico. Solo con datos
    reales: sin base o sin puntos, "—"."""
    puntos, base = hist["puntos"], hist["base"]
    ultimo = puntos[-1][1] if puntos else None
    variacion = ultimo - base if ultimo is not None and base is not None else None
    clase = "" if variacion is None else ("pos" if variacion >= 0 else "neg")
    pct = "" if variacion is None or not base else f" ({variacion / base * 100:+.2f}%)"
    return (f'<div class="stats"><div><span class="mono">Inicial</span><b>{esc(fmt_dinero(base))}</b></div>'
            f'<div><span class="mono">Último</span><b>{esc(fmt_dinero(ultimo))}</b></div>'
            f'<div><span class="mono">Variación</span><b class="{clase}">{esc(fmt_dinero(variacion, signo=True) + pct)}</b></div></div>')


# ───────────────────────── velas del ticker en operación ─────────────────────────

ESTADOS_ORDEN_MUERTA = ("canceled", "expired", "rejected", "replaced")
TIPOS_STOP = ("stop", "stop_limit", "trailing_stop")


def _unir_ordenes(*listas: list) -> list[dict]:
    vistas, out = set(), []
    for lista in listas:
        for o in lista:
            if not isinstance(o, dict):
                continue
            clave = o.get("id") or id(o)
            if clave in vistas:
                continue
            vistas.add(clave)
            out.append(o)
    return out


def cache_velas_segura(ruta: Path | None, problemas: list) -> Path:
    """La caché de velas jamás dentro del repo. Si la configuración apunta
    adentro (por error o por una ruta relativa con el cwd en el árbol), se
    usa el temporal del sistema y se avisa."""
    ruta = Path(ruta) if ruta else CACHE_VELAS_DEFECTO
    try:
        dentro = ruta.resolve().is_relative_to(REPO)
    except (OSError, RuntimeError):
        dentro = False
    if dentro:
        problemas.append(f"DASH_CACHE_VELAS apunta dentro del repo ({ruta}); "
                         f"la caché de velas se guarda en {CACHE_VELAS_DEFECTO}.")
        return CACHE_VELAS_DEFECTO
    return ruta


# Una pata `held` no está muerta: es el stop del bracket esperando
# (MNST, 2026-09-28). Estos estados sí: no protegen y no son un cierre.
_ESTADOS_TERMINALES = frozenset({
    "filled", "canceled", "cancelled", "expired", "rejected",
    "replaced", "done_for_day", "suspended",
})

# Claves que un proveedor de barras podría dejar en la telemetría del
# hunter. El PR que suma Alpaca SIP todavía no está en main: si ninguna
# está, no se afirma "Yahoo". `fuente` NO entra: en este repo es el
# escritor (vps/gha), no el feed de precios.
_CLAVES_FUENTE = (
    "proveedor", "proveedor_datos", "proveedor_barras", "proveedor_velas",
    "fuente_datos", "fuente_velas", "data_provider", "feed",
)
_AUSENTE = object()


def _terminal(orden: dict) -> bool:
    """Sin status no se da por muerta: un ausente no es evidencia."""
    status = orden.get("status")
    if status is None:
        return False
    return str(status).lower() in _ESTADOS_TERMINALES


def _aplanar(ordenes) -> list[dict]:
    """Fila de arriba y, un nivel más, sus `legs`. Copia: no muta el
    payload. La pata hereda el símbolo del padre si ella no lo trae
    (el stop `held` de MNST a veces no repite `symbol`)."""
    if not isinstance(ordenes, list):
        return []
    salida: list[dict] = []
    for orden in ordenes:
        if not isinstance(orden, dict):
            continue
        padre = orden.get("symbol") if isinstance(orden.get("symbol"), str) else None
        fila = dict(orden)
        if padre:
            fila["symbol"] = padre
        salida.append(fila)
        legs = orden.get("legs")
        if not isinstance(legs, list):
            continue
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            pata = dict(leg)
            simbolo = pata.get("symbol") if isinstance(pata.get("symbol"), str) and pata.get("symbol") else padre
            if simbolo:
                pata["symbol"] = simbolo
            salida.append(pata)
    return salida


def _unir_aplanadas(*listas: list) -> list[dict]:
    """Aplana y quita duplicados por id. La misma orden viene en 'hoy'
    y en 'closed'; contarla dos veces doblaría el P&L."""
    vistos: set[str] = set()
    out: list[dict] = []
    for lista in listas:
        for o in _aplanar(lista):
            oid = o.get("id")
            if oid:
                if oid in vistos:
                    continue
                vistos.add(str(oid))
            out.append(o)
    return out


def ordenes_para_detectar(abiertas: list) -> list[dict]:
    """Lo que `reconciliacion.detectar` sabe leer: filas planas.

    El detector de la PR #183 mira `symbol`/`side`/`type` de la fila de
    arriba y no entra a `legs`. El stop `held` vive ahí. Se aplana antes
    de llamarlo, y se tiran las patas ya terminales: si no, un stop
    `canceled` contaría como protección (el detector no filtra status)."""
    return [o for o in _aplanar(abiertas) if not _terminal(o)]


def _simbolos(posiciones: list) -> set[str]:
    return {
        p.get("symbol") for p in posiciones
        if isinstance(p, dict) and isinstance(p.get("symbol"), str) and p.get("symbol")
    }


def _mas_reciente(candidatas: list[dict]) -> dict | None:
    if not candidatas:
        return None
    minimo = datetime.min.replace(tzinfo=timezone.utc)

    def clave(o):
        return parse_ts(o.get("submitted_at")) or parse_ts(o.get("created_at")) or minimo

    return max(candidatas, key=clave)


def _nivel_de(orden: dict | None, precio_clave: str) -> dict | None:
    if orden is None:
        return None
    status = orden.get("status")
    return {
        "precio": num(orden.get(precio_clave)),
        "estado": str(status).lower() if status else None,
    }


def salidas_de(ticker: str, ordenes_vivas: list) -> dict:
    """Stop, take-profit y venta a mercado todavía vivos de `ticker`.

    El take-profit es un `limit` de venta: no es protección (igual que
    la reconciliación). El stop `held` anidado sí. Una venta a mercado
    viva es el cierre en curso, no un stop, pero tampoco es "sin salida"."""
    stops, limites, mercados = [], [], []
    for o in ordenes_vivas:
        if not isinstance(o, dict) or o.get("symbol") != ticker:
            continue
        if str(o.get("side") or "").lower() != "sell":
            continue
        tipo = str(o.get("type") or "").lower()
        if tipo in TIPOS_STOP:
            stops.append(o)
        elif tipo == "limit":
            limites.append(o)
        elif tipo == "market":
            mercados.append(o)
    return {
        "stop": _nivel_de(_mas_reciente(stops), "stop_price"),
        "tp": _nivel_de(_mas_reciente(limites), "limit_price"),
        "mercado": _mas_reciente(mercados) is not None,
    }


def fila_posicion(p: dict, ordenes_vivas: list, ordenes_conocidas: bool = True) -> dict:
    """Una posición tal como la manda Alpaca. El P&L abierto es el campo
    `unrealized_pl`; si falta, es None, no un cálculo con un precio que
    no vino. `unrealized_plpc` es fracción (0,0125 = 1,25 %), igual que
    lo lee `cierre.py`. Si las órdenes no se pudieron leer, stop y
    objetivo quedan desconocidos: un "—" ahí se leería como que no hay."""
    salidas = salidas_de(str(p.get("symbol")), ordenes_vivas) if ordenes_conocidas else {
        "stop": None, "tp": None, "mercado": False}
    plpc = num(p.get("unrealized_plpc"))
    return {
        "ticker": p.get("symbol"),
        "qty": num(p.get("qty")),
        "entrada": num(p.get("avg_entry_price")),
        "actual": num(p.get("current_price")),
        "pnl": num(p.get("unrealized_pl")),
        "pnl_pct": None if plpc is None else plpc * 100,
        "stop": salidas["stop"],
        "tp": salidas["tp"],
        "mercado": salidas["mercado"],
        "salidas_conocidas": ordenes_conocidas,
    }


def filas_pendientes(abiertas: list, simbolos_abiertos: set[str]) -> list[dict]:
    """Compras de entrada todavía vivas, y cualquier orden de arriba cuyo
    símbolo no esté abierto (no es la pata de salida de una posición).

    Las patas `held` no son filas propias: se muestran como stop y
    objetivo de la orden padre. El take-profit `new` de una posición
    abierta tampoco: va en la fila de esa posición."""
    filas = []
    for o in abiertas:
        if not isinstance(o, dict) or _terminal(o):
            continue
        simbolo = o.get("symbol")
        if not isinstance(simbolo, str) or not simbolo:
            continue
        lado = str(o.get("side") or "").lower()
        if lado != "buy" and simbolo in simbolos_abiertos:
            continue
        if lado not in ("buy", "sell"):
            continue
        salidas = salidas_de(simbolo, ordenes_para_detectar([o]))
        status = o.get("status")
        filas.append({
            "ticker": simbolo,
            "lado": lado,
            "qty": num(o.get("qty")),
            "limite": num(o.get("limit_price")),
            "stop": salidas["stop"],
            "tp": salidas["tp"],
            "mercado": salidas["mercado"],
            "estado": str(status).lower() if status else None,
        })
    return filas


def _qty_fill(orden: dict) -> float | None:
    """Cantidad que de verdad se ejecutó. `filled_qty` manda. Si falta y
    el estado es `filled`, `qty` es el tamaño de esa orden ya llena. Si
    faltan los dos, None: no se usa 0."""
    q = num(orden.get("filled_qty"))
    if q is not None:
        return q
    if str(orden.get("status") or "").lower() == "filled":
        return num(orden.get("qty"))
    return None


def cierres_de_hoy(ordenes: list, simbolos_abiertos: set[str], desde: datetime) -> list[dict]:
    """Posiciones que ya no están abiertas y tuvieron una venta llena
    hoy (día de Nueva York, el mismo corte que las órdenes de hoy).

    El P&L es FIFO sobre los fills que alcanzamos a ver: (salida −
    entrada) × cantidad, de un largo. Este bot no abre cortos. Si el
    historial no alcanza para emparejar toda la venta, entrada y P&L
    quedan en None; la salida sí se muestra si el fill de hoy la trae.
    Un símbolo que sigue en `posiciones` no entra aquí aunque haya
    vendido una parte: la posición abierta manda."""
    fills = [
        o for o in ordenes
        if isinstance(o, dict) and str(o.get("status") or "").lower() == "filled" and parse_ts(o.get("filled_at"))
    ]
    fills.sort(key=lambda o: parse_ts(o.get("filled_at")) or datetime.min.replace(tzinfo=timezone.utc))
    por: dict[str, list] = {}
    for o in fills:
        simbolo = o.get("symbol")
        if isinstance(simbolo, str) and simbolo:
            por.setdefault(simbolo, []).append(o)

    filas = []
    for simbolo, serie in por.items():
        if simbolo in simbolos_abiertos:
            continue
        lots: list = []
        qty_hoy = 0.0
        qty_hoy_ok = True
        notional_salida = 0.0
        qty_salida_preciada = 0.0
        notional_entrada = 0.0
        qty_emparejada = 0.0
        pnl_ok = True
        ultima = None
        ventas_hoy = 0
        for o in serie:
            ts = parse_ts(o.get("filled_at"))
            lado = str(o.get("side") or "").lower()
            qty = _qty_fill(o)
            precio = num(o.get("filled_avg_price"))
            hoy = ts is not None and ts >= desde
            usable = qty is not None and precio is not None and qty > 0
            if lado == "buy":
                lots.append([qty, precio] if usable else None)
                continue
            if lado != "sell":
                continue
            if hoy:
                ventas_hoy += 1
                ultima = ts
                if qty is None or qty <= 0:
                    qty_hoy_ok = False
                    pnl_ok = False
                else:
                    qty_hoy += qty
                if usable:
                    notional_salida += precio * qty
                    qty_salida_preciada += qty
                else:
                    pnl_ok = False
            if not usable:
                # Un hueco en el FIFO invalida lo que se empareje después:
                # no sabemos qué lote quedaba.
                lots.clear()
                lots.append(None)
                if hoy:
                    pnl_ok = False
                continue
            restante = qty
            while restante > 1e-8 and lots:
                lot = lots[0]
                if lot is None:
                    pnl_ok = False
                    lots.pop(0)
                    break
                tomar = min(restante, lot[0])
                if hoy:
                    notional_entrada += tomar * lot[1]
                    qty_emparejada += tomar
                lot[0] -= tomar
                restante -= tomar
                if lot[0] <= 1e-8:
                    lots.pop(0)
            if restante > 1e-8:
                pnl_ok = False
        if ventas_hoy == 0:
            continue
        salida = (notional_salida / qty_salida_preciada) if qty_salida_preciada > 0 and qty_hoy_ok else None
        if pnl_ok and qty_emparejada > 0 and qty_hoy_ok and abs(qty_emparejada - qty_hoy) <= 1e-6:
            entrada = notional_entrada / qty_emparejada
            pnl = round(notional_salida - notional_entrada, 2)
        else:
            entrada = None
            pnl = None
        filas.append({
            "ticker": simbolo,
            "qty": qty_hoy if qty_hoy_ok and qty_hoy > 0 else None,
            "entrada": entrada,
            "salida": salida,
            "pnl": pnl,
            "hora": ultima,
        })
    filas.sort(key=lambda f: f["hora"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return filas


def contrastar_broker(posiciones, ordenes_vivas, ruta_revisiones):
    """(avisos, nota). Reusa `reconciliacion.detectar` (PR #183).

    Los avisos son dicts {ticker, sin_seguimiento, sin_stop}. `None` en
    avisos significa que no se pudo contrastar (no es "todo en orden").
    Sin ruta de revisiones no se lee el libro del repo: las pruebas del
    panel no lo configuran y no deben alarmar con el archivo real.

    `ordenes_vivas is None` (no se pudieron leer) se le pasa tal cual al
    detector: no afirma que falte el stop. Una lista vacía sí lo afirma."""
    if ruta_revisiones is None or not isinstance(posiciones, list):
        return [], None
    try:
        from momentum_paper_trader.estado import cargar as cargar_revisiones
        from momentum_paper_trader.reconciliacion import detectar
    except Exception as ex:
        return None, (f"no se pudo cargar la reconciliación ({type(ex).__name__}); "
                      "no se contrastó el broker con el seguimiento")
    try:
        revisiones = cargar_revisiones(Path(ruta_revisiones))
    except Exception as ex:
        return None, (f"no se pudieron leer las revisiones ({type(ex).__name__}); "
                      "no se afirma que el seguimiento cubra lo que el broker tiene abierto")
    problemas = detectar(posiciones, ordenes_vivas, revisiones)
    return [
        {"ticker": p.ticker, "sin_seguimiento": p.sin_seguimiento, "sin_stop": p.sin_stop}
        for p in problemas
    ], None


def _etiqueta_fuente(valor) -> str | None:
    """Una clave suelta (`proveedor`, `feed`…) de una telemetría que no
    trae el bloque `datos` de la PR #185."""
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    if not texto or len(texto) > 40:
        return None
    clave = texto.lower().replace(" ", "_").replace("-", "_")
    conocidas = {
        "yahoo": "Yahoo",
        "yfinance": "Yahoo",
        "yahoo_chart": "Yahoo",
        "alpaca_sip": "Alpaca SIP",
        "sip": "Alpaca SIP",
        "alpaca_data_sip": "Alpaca SIP",
        "iex": "Alpaca IEX",
        "mixto": "Mixto",
    }
    if clave in conocidas:
        return conocidas[clave]
    if clave == "alpaca":
        return "Alpaca"
    return texto


def _etiqueta_medida(fuente, feed) -> str | None:
    """Lo que contestó de verdad (`datos.fuente` + `datos.feed`).

    `alpaca` + `sip` es Alpaca SIP. `mixto` es el feed más el respaldo
    Yahoo de esa corrida. `iex` no se disfraza de SIP. Sin `fuente`
    medible no se usa `configurada`: estar configurado no es haber
    contestado."""
    if not isinstance(fuente, str) or not fuente.strip():
        return None
    f = fuente.strip().lower()
    feed_s = feed.strip().lower() if isinstance(feed, str) and feed.strip() else None
    if f == "yahoo":
        return "Yahoo"
    if f == "alpaca":
        if feed_s == "sip":
            return "Alpaca SIP"
        if feed_s == "iex":
            return "Alpaca IEX"
        return "Alpaca"
    if f == "mixto":
        if feed_s == "sip":
            return "Alpaca SIP + Yahoo"
        if feed_s == "iex":
            return "Alpaca IEX + Yahoo"
        return "Mixto"
    return _etiqueta_fuente(fuente)


def _valor_fuente_suelto(registro: dict):
    """Claves de un PR que todavía no usa el bloque `datos`. `fuente` a
    secas no entra: en la raíz del JSONL es el escritor (vps/gha)."""
    def en(d):
        if not isinstance(d, dict):
            return _AUSENTE
        for clave in _CLAVES_FUENTE:
            if clave in d and isinstance(d.get(clave), str) and str(d.get(clave)).strip():
                return d[clave]
        return _AUSENTE

    hallado = en(registro)
    if hallado is not _AUSENTE:
        return hallado
    for caja in ("embudo", "mercado"):
        hallado = en(registro.get(caja))
        if hallado is not _AUSENTE:
            return hallado
    return _AUSENTE


def fuente_datos_activa(dir_telemetria, ahora: datetime) -> str | None:
    """Yahoo o Alpaca SIP, si la telemetría de hoy del VPS lo midió.

    El esquema que escribe el hunter es `datos.fuente` / `datos.feed`
    (`yahoo`, `alpaca`, `mixto`, feed `sip` o `iex`). Un evento que no
    trae el bloque, o lo trae con `fuente` vacía, no se midió: se sigue
    hacia el anterior del mismo día. Si ninguno midió, None — no se
    asume Yahoo. `fuente: vps` en la raíz no es el feed de precios."""
    if dir_telemetria is None:
        return None
    ruta = Path(dir_telemetria) / ahora.astimezone(timezone.utc).date().isoformat() / "vps" / "events.jsonl"
    try:
        lineas = Path(ruta).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    eventos = []
    for linea in lineas:
        try:
            r = json.loads(linea)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        ts = parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        eventos.append((ts, r))
    eventos.sort(key=lambda par: par[0], reverse=True)
    for _ts, r in eventos:
        datos = r.get("datos")
        if isinstance(datos, dict) and "fuente" in datos:
            etiqueta = _etiqueta_medida(datos.get("fuente"), datos.get("feed"))
            if etiqueta:
                return etiqueta
            continue
        suelto = _valor_fuente_suelto(r)
        if suelto is _AUSENTE:
            continue
        etiqueta = _etiqueta_fuente(suelto)
        if etiqueta:
            return etiqueta
    return None


def tickers_en_operacion(posiciones: list, ordenes_abiertas: list) -> list[tuple[str, str]]:
    """(ticker, rol) con rol 'abierta' o 'pendiente'.

    Abiertas primero. Una compra ya llena o cancelada no mete al ticker
    en el gráfico: el 28/9 las cerradas de la mañana seguían ahí porque
    cualquier orden de hoy contaba como "en operación"."""
    out: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for p in posiciones or []:
        if not isinstance(p, dict):
            continue
        simbolo = p.get("symbol")
        if isinstance(simbolo, str) and simbolo and simbolo not in vistos:
            vistos.add(simbolo)
            out.append((simbolo, "abierta"))
    for o in ordenes_abiertas or []:
        if not isinstance(o, dict) or _terminal(o):
            continue
        if str(o.get("side") or "").lower() != "buy":
            continue
        simbolo = o.get("symbol")
        if isinstance(simbolo, str) and simbolo and simbolo not in vistos:
            vistos.add(simbolo)
            out.append((simbolo, "pendiente"))
    return out


def _entrada_watchlist(ticker: str, watch: list[dict]) -> dict | None:
    """La entrada de la watchlist que corresponde a la operación: la más
    reciente del ticker, con preferencia por triggered y luego watching."""
    prioridad = {"triggered": 0, "watching": 1}
    candidatas = [w for w in watch if w.get("ticker") == ticker]
    if not candidatas:
        return None
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(candidatas, key=lambda w: (
        prioridad.get(str(w.get("estado") or "").lower(), 2),
        -(w.get("creado_en") or minimo).timestamp()))[0]


def marcas_de(ticker: str, posiciones: list, ordenes: list, watch: list[dict]) -> dict:
    """Niveles a dibujar. Cada uno sale de UNA fuente concreta y si falta es
    None, nunca un cálculo:
      ruptura       -> `ultima_zona_entrada_baja` de la entrada de la watchlist
      entrada       -> `filled_avg_price` / `filled_at` de la compra llenada
                       (fill real); si no hay compra de hoy, el precio medio de
                       la posición (`avg_entry_price`), sin hora
      stop          -> `stop_price` de la pata/orden de venta tipo stop que
                       no esté cancelada, la más reciente"""
    w = _entrada_watchlist(ticker, watch)
    ruptura = w.get("ruptura") if w else None

    compras = [o for o in ordenes if o.get("symbol") == ticker and o.get("side") == "buy"
               and num(o.get("filled_avg_price")) is not None and parse_ts(o.get("filled_at"))]
    compras.sort(key=lambda o: parse_ts(o.get("filled_at")))
    entrada_precio = entrada_hora = None
    if compras:
        entrada_precio = num(compras[-1].get("filled_avg_price"))
        entrada_hora = parse_ts(compras[-1].get("filled_at"))
    else:
        pos = next((p for p in posiciones if p.get("symbol") == ticker), None)
        if pos is not None:
            entrada_precio = num(pos.get("avg_entry_price"))

    stops = []
    for o in ordenes:
        for candidata in [o, *(o.get("legs") or [])]:
            if not isinstance(candidata, dict) or candidata.get("symbol", ticker) != ticker:
                continue
            if candidata.get("side") != "sell" or candidata.get("type") not in TIPOS_STOP:
                continue
            if candidata.get("status") in ESTADOS_ORDEN_MUERTA:
                continue
            precio = num(candidata.get("stop_price"))
            if precio is not None:
                stops.append((parse_ts(candidata.get("submitted_at")) or datetime.min.replace(tzinfo=timezone.utc), precio))
    stop = sorted(stops)[-1][1] if stops else None
    return {"ruptura": ruptura, "entrada_precio": entrada_precio, "entrada_hora": entrada_hora, "stop": stop}


def _grafico_velas(res: dict, marcas: dict, tz, ahora: datetime) -> str:
    """Velas de 1 min en SVG, sin librerías. Las marcas que faltan no se
    dibujan (el pie del panel dice "sin dato"). Una marca fuera del rango
    de precios de las velas se anota en el borde en vez de aplastar las
    velas para que quepa."""
    ancho, alto, x0, x1, y0, y1 = 480, 230, 56, 470, 196, 14
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="Velas de 1 minuto de hoy">',
              f'<line x1="{x0}" y1="{y1}" x2="{x0}" y2="{y0}" class="rejilla"/>',
              f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" class="rejilla"/>']
    velas = res.get("velas")
    if not velas:
        partes.append(f'<text x="{(x0+x1)/2}" y="110" text-anchor="middle" class="eje">Sin datos</text>')
        partes.append(f'<text x="{(x0+x1)/2}" y="128" text-anchor="middle" class="eje">{esc(res.get("error") or "sin velas")}</text>')
        partes.append("</svg>")
        return "".join(partes)

    n = len(velas["close"])
    marcas_ts = [parse_ts(t) for t in velas["timestamps"]]
    minimo, maximo = min(velas["low"]), max(velas["high"])
    rango = maximo - minimo
    if rango < 1e-9:
        rango = max(0.01, minimo * 0.002)
    # Una marca a menos de un rango completo de distancia entra al eje; más
    # lejos, se anota en el borde.
    dentro = [v for v in (marcas["ruptura"], marcas["entrada_precio"], marcas["stop"])
              if v is not None and minimo - rango <= v <= maximo + rango]
    lo = min([minimo, *dentro]) - rango * 0.08
    hi = max([maximo, *dentro]) + rango * 0.08

    def y(v: float) -> float:
        return y0 - (v - lo) / (hi - lo) * (y0 - y1)

    paso = (x1 - x0) / n
    cuerpo = max(1.0, min(6.0, paso * 0.7))

    def x(i: int) -> float:
        return x0 + paso * (i + 0.5)

    for valor in (minimo, maximo):
        partes.append(f'<text x="{x0-6}" y="{y(valor)+4:.1f}" text-anchor="end" class="eje">{esc(fmt_dinero(valor))}</text>')
    for i in range(n):
        o, c, h, lw = velas["open"][i], velas["close"][i], velas["high"][i], velas["low"][i]
        cls = "vela-sube" if c >= o else "vela-baja"
        partes.append(f'<line class="{cls}" x1="{x(i):.1f}" y1="{y(h):.1f}" x2="{x(i):.1f}" y2="{y(lw):.1f}" stroke-width="1"/>')
        top, base = max(o, c), min(o, c)
        partes.append(f'<rect class="vela {cls}" x="{x(i)-cuerpo/2:.1f}" y="{y(top):.1f}" width="{cuerpo:.1f}" '
                      f'height="{max(1.0, y(base)-y(top)):.1f}"/>')

    def marca_horizontal(valor, nombre, clase, dash):
        if valor is None:
            return
        if lo <= valor <= hi:
            yv = y(valor)
            partes.append(f'<line class="marca-{nombre} {clase}" x1="{x0}" y1="{yv:.1f}" x2="{x1}" y2="{yv:.1f}" stroke-width="1.2" stroke-dasharray="{dash}"/>')
            partes.append(f'<text x="{x1}" y="{yv-4:.1f}" text-anchor="end" class="eje {clase}-txt">{esc(nombre)} {esc(fmt_dinero(valor))}</text>')
        else:
            yv = y1 + 10 if valor > hi else y0 - 6
            partes.append(f'<text class="eje marca-{nombre}-fuera {clase}-txt" x="{x1}" y="{yv:.1f}" text-anchor="end">{esc(nombre)} {esc(fmt_dinero(valor))} (fuera del gráfico)</text>')

    marca_horizontal(marcas["ruptura"], "ruptura", "m-ruptura", "6 4")
    marca_horizontal(marcas["stop"], "stop", "m-stop", "3 3")
    marca_horizontal(marcas["entrada_precio"], "entrada", "m-entrada", "1 3")
    hora = marcas["entrada_hora"]
    if hora is not None and marcas_ts and marcas_ts[0] is not None:
        # Vela más cercana al fill real (sin interpolar entre velas).
        idx = min(range(n), key=lambda i: abs((marcas_ts[i] - hora).total_seconds()) if marcas_ts[i] else float("inf"))
        if marcas_ts[idx] is not None and abs((marcas_ts[idx] - hora).total_seconds()) <= 120:
            partes.append(f'<line class="marca-entrada-hora m-entrada" x1="{x(idx):.1f}" y1="{y1}" x2="{x(idx):.1f}" y2="{y0}" stroke-width="1" stroke-dasharray="2 3"/>')
            if marcas["entrada_precio"] is not None and lo <= marcas["entrada_precio"] <= hi:
                partes.append(f'<circle cx="{x(idx):.1f}" cy="{y(marcas["entrada_precio"]):.1f}" r="3.5" class="ink"/>')
    if marcas_ts[0] is not None:
        partes.append(f'<text x="{x0}" y="{alto-6}" class="eje">{esc(_hora(marcas_ts[0], tz, ahora=ahora))}</text>')
    if n > 1 and marcas_ts[-1] is not None:
        partes.append(f'<text x="{x1}" y="{alto-6}" text-anchor="end" class="eje">{esc(_hora(marcas_ts[-1], tz, ahora=ahora))}</text>')
    partes.append("</svg>")
    return "".join(partes)


def _pie_marcas(marcas: dict, tz, ahora: datetime) -> str:
    def dinero(v):
        return fmt_dinero(v) if v is not None else "sin dato"
    entrada = dinero(marcas["entrada_precio"])
    if marcas["entrada_precio"] is not None:
        entrada += f" a las {_hora(marcas['entrada_hora'], tz, ahora=ahora)}" if marcas["entrada_hora"] else " (hora sin dato)"
    return (f'<div class="stats"><div><span class="mono">Ruptura</span><b>{esc(dinero(marcas["ruptura"]))}</b></div>'
            f'<div><span class="mono">Entrada</span><b>{esc(entrada)}</b></div>'
            f'<div><span class="mono">Stop</span><b>{esc(dinero(marcas["stop"]))}</b></div></div>')


def _subtitulo_velas(res: dict, tz, ahora: datetime) -> str:
    velas = res.get("velas")
    if not velas:
        return "sin velas"
    origen = {"fuente": "Yahoo", "cache": "caché", "cache vencida": "caché vencida"}.get(res.get("origen"), "—")
    return f"{len(velas['close'])} velas · {origen} {_hora(res.get('obtenido'), tz, ahora=ahora)}"


# ───────────────────────── render ─────────────────────────

def esc(v) -> str:
    return html.escape("—" if v is None else str(v))


DIAS_ES = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MESES_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _dia(d_local: datetime, hoy_local: datetime) -> str | None:
    """None si es hoy; "vie" si es de los últimos 6 días; "18 sep" si no.
    Así una hora vieja nunca se confunde con una de hoy."""
    dias = (hoy_local.date() - d_local.date()).days
    if dias == 0:
        return None
    if 0 < dias < 7:
        return DIAS_ES[d_local.weekday()]
    return f"{d_local.day} {MESES_ES[d_local.month - 1]}"


def _hora(d: datetime | None, tz, segundos: bool = False, ahora: datetime | None = None) -> str:
    if d is None:
        return "—"
    local = d.astimezone(tz)
    hora = local.strftime("%H:%M:%S" if segundos else "%H:%M")
    dia = _dia(local, ahora.astimezone(tz)) if ahora is not None else None
    return f"{dia} {hora}" if dia else hora


def _cuando(d: datetime | None, tz, ahora: datetime) -> str:
    """"de las 14:40" si es de hoy, "del vie 22:33" si no, "—" sin dato."""
    if d is None:
        return "—"
    texto = _hora(d, tz, ahora=ahora)
    return f"del {texto}" if " " in texto else f"de las {texto}"


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
        f'<rect x="{x0+1}" y="{y1}" width="{ancho-x0-10}" height="{y(ctx["presupuesto"])-y1:.1f}" class="zona-riesgo"/>',
        f'<line x1="{x0}" y1="{y1}" x2="{x0}" y2="{y0}" class="rejilla"/>',
        f'<line x1="{x0}" y1="{y0}" x2="{ancho-10}" y2="{y0}" class="rejilla"/>',
    ]
    for marca in range(0, int(tope) + 1, 4):
        partes.append(f'<text x="{x0-8}" y="{y(marca)+4:.1f}" text-anchor="end" class="eje">{marca}</text>')
    yp = y(ctx["presupuesto"])
    partes.append(f'<line x1="{x0}" y1="{yp:.1f}" x2="{ancho-10}" y2="{yp:.1f}" class="limite" stroke-width="1.5" stroke-dasharray="6 4"/>')
    partes.append(f'<text x="{ancho-14}" y="{yp-8:.1f}" text-anchor="end" class="eje rojo">presupuesto {fmt_num(ctx["presupuesto"])} velas</text>')
    if valores:
        paso = (ancho - x0 - 20) / len(valores)
        barra = max(3.0, paso * 0.7)
        for i, v in enumerate(valores):
            cls = "barra-alta" if v > ctx["presupuesto"] else "barra-ok"
            partes.append(f'<rect class="{cls}" x="{x0 + 6 + i*paso:.1f}" y="{y(v):.1f}" width="{barra:.1f}" height="{y0-y(v):.1f}"/>')
    else:
        mensaje = "Sin órdenes con latencia completa (ruptura → orden) hoy."
        partes.append(f'<text x="{(ancho+x0)/2}" y="120" text-anchor="middle" class="eje">{esc(mensaje)}</text>')
    partes.append("</svg>")
    return "".join(partes)


CSS = """
:root{--fondo:#f3f1ea;--papel:#fff;--tinta:#16171a;--gris:#5c5b55;--gris2:#45443f;--linea:#d6d3c8;--linea2:#ebe8df;
--acento:#2451b8;--verde:#1f7a4d;--rojo:#b3261e;--mono:'JetBrains Mono',ui-monospace,monospace;
--rejilla:#bdb9ad;--zona-riesgo:#fbeceb;--ok-bg:#e6f1ea;--ok-fg:#1b6a43;--mal-bg:#fbeceb;--mal-fg:#8f1d17;--duda-bg:#f6f4ee;
--oscuro-bg:var(--tinta);--oscuro-fg:#e9e7df;--oscuro-linea:#2c2d31;--oscuro-tk:#fff;--oscuro-sub:#a9a69b;--oscuro-td:#c9c6bb}
/* Tema oscuro: por preferencia del sistema (salvo que el usuario haya
   elegido claro a mano) y por elección manual guardada en el navegador.
   Se redefinen las MISMAS variables; el resto del CSS ya las usa, así que
   el modo claro queda idéntico. */
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--fondo:#000000;--papel:#1b1c20;--tinta:#e9e7df;--gris:#9a978d;--gris2:#c7c4ba;--linea:#2f3034;--linea2:#26272b;--acento:#7a9bff;--verde:#46b37e;--rojo:#f2685e;--rejilla:#3a3b40;--zona-riesgo:#37211f;--ok-bg:#15352a;--ok-fg:#7fd0a4;--mal-bg:#37211f;--mal-fg:#f0a49d;--duda-bg:#24252a;--oscuro-bg:#16171b;--oscuro-fg:#e9e7df;--oscuro-linea:#2c2d31;--oscuro-tk:#fff;--oscuro-sub:#a9a69b;--oscuro-td:#c9c6bb}}
:root[data-theme="dark"]{--fondo:#000000;--papel:#1b1c20;--tinta:#e9e7df;--gris:#9a978d;--gris2:#c7c4ba;--linea:#2f3034;--linea2:#26272b;--acento:#7a9bff;--verde:#46b37e;--rojo:#f2685e;--rejilla:#3a3b40;--zona-riesgo:#37211f;--ok-bg:#15352a;--ok-fg:#7fd0a4;--mal-bg:#37211f;--mal-fg:#f0a49d;--duda-bg:#24252a;--oscuro-bg:#16171b;--oscuro-fg:#e9e7df;--oscuro-linea:#2c2d31;--oscuro-tk:#fff;--oscuro-sub:#a9a69b;--oscuro-td:#c9c6bb}
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
.pildora.ok{background:var(--ok-bg);color:var(--ok-fg);border-color:transparent}
.pildora.mal{background:var(--mal-bg);color:var(--mal-fg);border-color:transparent}
button.pildora{cursor:pointer;font-family:var(--mono);font-size:12px;line-height:1.2;color:inherit}
button.pildora:hover{border-color:var(--acento)}
.problemas{background:var(--mal-bg);color:var(--mal-fg);border-radius:6px;padding:14px 18px;font-size:14px}
.problemas ul{margin:6px 0 0;padding-left:18px}
.fila{display:grid;gap:12px}
.c4{grid-template-columns:repeat(4,minmax(0,1fr))}.c5{grid-template-columns:repeat(5,minmax(0,1fr))}
.c3{grid-template-columns:repeat(3,minmax(0,1fr))}.c2{grid-template-columns:1.55fr 1fr}
.c2i{grid-template-columns:repeat(2,minmax(0,1fr))}
.panel{background:var(--papel);border:1px solid var(--linea);border-radius:6px;padding:18px;display:flex;flex-direction:column;gap:10px;min-width:0}
.panel.oscuro{background:var(--oscuro-bg);color:var(--oscuro-fg);border-color:var(--oscuro-bg)}
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
.oscuro td{border-color:var(--oscuro-linea);color:var(--oscuro-td)}.oscuro td.tk{color:var(--oscuro-tk)}.oscuro .sub{color:var(--oscuro-sub)}
.vacio{font-size:13px;color:var(--gris);padding:12px 0}
.oscuro .vacio{color:var(--oscuro-sub)}
.duda{background:var(--duda-bg);border-radius:4px;padding:10px 12px;display:flex;flex-direction:column;gap:4px}
.duda .cab{display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px}
.duda p{margin:0;font-size:13px;color:var(--gris2)}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;font-family:var(--mono)}
.stats b{display:block;font-size:18px;font-weight:500}
svg{width:100%;height:auto}.eje{font-family:var(--mono);font-size:10px;fill:var(--gris)}.eje.rojo{fill:var(--rojo)}
.rejilla{stroke:var(--rejilla)}.ink{fill:var(--tinta)}.zona-riesgo{fill:var(--zona-riesgo)}
.serie{stroke:var(--acento)}.serie-punto{fill:var(--acento)}.base-punteada{stroke:var(--gris)}
.vela-sube{fill:var(--verde);stroke:var(--verde)}.vela-baja{fill:var(--rojo);stroke:var(--rojo)}
.m-ruptura{stroke:var(--acento)}.m-ruptura-txt{fill:var(--acento)}
.m-stop{stroke:var(--rojo)}.m-stop-txt{fill:var(--rojo)}
.m-entrada{stroke:var(--gris)}.m-entrada-txt{fill:var(--gris)}
.barra-ok{fill:var(--acento)}.barra-alta{fill:var(--rojo)}.limite{stroke:var(--rojo)}
.nota{margin-top:auto;padding:10px 12px;background:var(--mal-bg);border-radius:4px;font-family:var(--mono);font-size:12px;color:var(--mal-fg)}
.nota-info{margin-top:auto;padding:10px 12px;background:var(--duda-bg);border-radius:4px;font-family:var(--mono);font-size:12px;color:var(--gris2)}
h3{margin:12px 0 0;font-size:13px;letter-spacing:.04em;font-weight:500}
.badge{font-family:var(--mono);font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid var(--acento);color:var(--acento)}
.scroll{overflow-x:auto}
.operaciones{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media (max-width:900px){.operaciones{grid-template-columns:1fr}}
@media (max-width:1100px){.c5{grid-template-columns:repeat(3,minmax(0,1fr))}.c4{grid-template-columns:repeat(2,minmax(0,1fr))}.c2,.c3{grid-template-columns:1fr}}
@media (max-width:900px){.c2i{grid-template-columns:1fr}}
@media (max-width:640px){main{padding:20px 16px}.c4,.c5{grid-template-columns:1fr 1fr}.kpi .valor{font-size:22px}}
"""


def fmt_qty(v) -> str:
    n = num(v) if not isinstance(v, (int, float)) else (v if math.isfinite(v) else None)
    if n is None:
        return "—"
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.4f}".rstrip("0").rstrip(".")


def _html_pnl(pnl, pct=None) -> str:
    if pnl is None:
        return "—"
    clase = "pos" if pnl >= 0 else "neg"
    extra = "" if pct is None else f" ({pct:+.2f}%)"
    return f'<span class="{clase}">{esc(fmt_dinero(pnl, signo=True) + extra)}</span>'


def _html_nivel(nivel, mercado: bool = False, conocido: bool = True) -> str:
    if not conocido:
        return "sin datos"
    if mercado and (not nivel or nivel.get("precio") is None):
        return "venta a mercado"
    if not nivel or nivel.get("precio") is None:
        return "—"
    texto = fmt_dinero(nivel["precio"])
    if nivel.get("estado") == "held":
        texto += " · held"
    return esc(texto)


def _frase_aviso(a: dict) -> str:
    """Las mismas frases que `reconciliacion._frase`, para que el panel
    y el Telegram digan lo mismo."""
    partes = []
    if a.get("sin_seguimiento"):
        partes.append("no hay una revisión viva que la siga")
    if a.get("sin_stop"):
        partes.append("no tiene stop de venta abierto")
    return f"{a.get('ticker')}: " + " y ".join(partes) + "."


def _html_avisos(ctx: dict) -> str:
    partes = []
    nota = ctx.get("nota_seguimiento")
    if nota:
        partes.append(f'<div class="nota" role="alert">{esc(nota)}</div>')
    avisos = ctx.get("avisos_broker") or []
    if avisos:
        items = " ".join(esc(_frase_aviso(a)) for a in avisos)
        partes.append(
            '<div class="nota" role="alert"><b>El broker no cuadra con el seguimiento.</b> '
            f'{items} Sigue ocupando cupo y puede quedar desprotegida.</div>')
    return "".join(partes)


def _html_broker(ctx: dict) -> str:
    tz = ctx["tz"]

    def tabla(cabezas, filas):
        th = "".join(f"<th>{esc(h)}</th>" for h in cabezas)
        return f"<div class='scroll'><table><thead><tr>{th}</tr></thead><tbody>{filas}</tbody></table></div>"

    def bloque(titulo, conocido, vacio, filas_html):
        if not conocido:
            cuerpo = '<p class="vacio">Sin datos.</p>'
        elif not filas_html:
            cuerpo = f'<p class="vacio">{vacio}</p>'
        else:
            cuerpo = filas_html
        return f"<h3>{titulo}</h3>{cuerpo}"

    pos = ctx.get("posiciones_broker")
    if pos:
        filas = "".join(
            "<tr>"
            f"<td class='tk'>{esc(p['ticker'])}</td><td>{esc(fmt_qty(p['qty']))}</td>"
            f"<td>{esc(fmt_dinero(p['entrada']))}</td><td>{esc(fmt_dinero(p['actual']))}</td>"
            f"<td>{_html_pnl(p['pnl'], p.get('pnl_pct'))}</td>"
            f"<td>{_html_nivel(p.get('stop'), p.get('mercado'), p.get('salidas_conocidas', True))}</td>"
            f"<td>{_html_nivel(p.get('tp'), conocido=p.get('salidas_conocidas', True))}</td>"
            "</tr>" for p in pos)
        pos_html = tabla(("Ticker", "Cant.", "Entrada", "Actual", "P&L abierto", "Stop", "Objetivo"), filas)
    else:
        pos_html = ""

    pend = ctx.get("pendientes_broker")
    if pend:
        filas = "".join(
            "<tr>"
            f"<td class='tk'>{esc(p['ticker'])}</td>"
            f"<td>{esc({'buy': 'compra', 'sell': 'venta'}.get(p.get('lado'), p.get('lado') or '—'))}</td>"
            f"<td>{esc(fmt_qty(p['qty']))}</td><td>{esc(fmt_dinero(p.get('limite')))}</td>"
            f"<td>{_html_nivel(p.get('stop'), p.get('mercado'))}</td>"
            f"<td>{_html_nivel(p.get('tp'))}</td>"
            f"<td>{esc(ESTADOS_ORDEN.get(p.get('estado'), p.get('estado') or '—'))}</td>"
            "</tr>" for p in pend)
        pend_html = tabla(("Ticker", "Lado", "Cant.", "Límite", "Stop", "Objetivo", "Estado"), filas)
    else:
        pend_html = ""

    cerr = ctx.get("cerradas_hoy")
    if cerr:
        filas = "".join(
            "<tr>"
            f"<td class='tk'>{esc(c['ticker'])}</td><td>{esc(fmt_qty(c['qty']))}</td>"
            f"<td>{esc(fmt_dinero(c['entrada']))}</td><td>{esc(fmt_dinero(c['salida']))}</td>"
            f"<td>{_html_pnl(c['pnl'])}</td>"
            f"<td>{esc(_hora(c.get('hora'), tz, ahora=ctx['ahora']))}</td>"
            "</tr>" for c in cerr)
        cerr_html = tabla(("Ticker", "Cant.", "Entrada", "Salida", "P&L realizado", "Hora"), filas)
    else:
        cerr_html = ""

    return (
        '<section class="panel" aria-label="Cuenta en el broker">'
        '<div class="titulo"><h2>Cuenta en el broker</h2>'
        '<span class="mono">Alpaca paper · posiciones, pendientes y cerradas hoy</span></div>'
        f'{_html_avisos(ctx)}'
        f'{bloque("Posiciones abiertas", pos is not None, "Sin posiciones abiertas.", pos_html)}'
        f'{bloque("Órdenes pendientes", pend is not None, "Sin órdenes pendientes.", pend_html)}'
        f'{bloque("Cerradas hoy", cerr is not None, "Ninguna posición cerrada hoy.", cerr_html)}'
        '</section>'
    )


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
        ("Equity paper", fmt_dinero(ctx["equity"]),
         "", f"cuenta paper {ctx['cuenta_numero']}" if ctx["cuenta_numero"] else "cuenta de práctica Alpaca"),
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
            f"<td>{_hora(w['detectado'], tz, ahora=ctx['ahora'])}</td><td>{esc(w['estado'])}</td></tr>" for w in ctx["watch"])
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

    r = ctx.get("riesgo") or {}
    if r.get("unicos") or r.get("capacidad"):
        # Bloqueos únicos por (ticker, código) con veces y última hora; la
        # capacidad llena aparte, con desde/hasta y corridas (2026-09-23).
        riesgo = ""
        if r.get("capacidad"):
            # Capacidad llena (mercado cerrado, tope de posiciones) NO es un
            # error: es el control de riesgo funcionando. Va en estilo
            # informativo neutro, no en el rojo de alarma (2026-09-24).
            riesgo += "".join(
                f'<div class="nota-info">Capacidad llena: <b>{esc(c["codigo"])}</b> · desde {esc(c["desde"])} '
                f'hasta {esc(c["hasta"])} · {c["corridas"]} corridas · {esc(c["motivo"])}</div>'
                for c in r["capacidad"])
        if r.get("unicos"):
            filas = "".join(
                f"<tr><td class='tk'>{esc(f['ticker'])}</td><td>{esc(f['codigo'])}</td>"
                f"<td>{f['veces']}</td><td>{esc(f['hora'])}</td></tr>" for f in r["unicos"][:20])
            riesgo += (f"<div class='scroll'><table><thead><tr><th>Ticker</th><th>Código</th><th>Veces</th>"
                       f"<th>Último</th></tr></thead><tbody>{filas}</tbody></table></div>")
        # El resumen va en rojo SOLO si hay algo que revisar (dato faltante o
        # motivo nuevo); si no, es informativo y va en neutro.
        hay_revisar = bool(r.get("dato_faltante") or r.get("codigos_nuevos"))
        riesgo += (f'<div class="{"nota" if hay_revisar else "nota-info"}">{len(r["unicos"])} bloqueos únicos · {r["eventos"]} eventos'
                   + (f' · <b>revisar:</b> {esc(", ".join(r["dato_faltante"]))}' if r.get("dato_faltante") else "")
                   + (f' · <b>motivo nuevo:</b> {esc(", ".join(r["codigos_nuevos"]))}' if r.get("codigos_nuevos") else "")
                   + '</div>')
    else:
        riesgo = ('<p class="vacio">Ningún límite ha bloqueado operaciones hoy.</p>' if ctx["conteos_validos"]
                  else f'<p class="vacio">Sin datos: {esc(ctx["motivo_sin_datos"])}.</p>')

    # El título dice de qué sesión son los puntos. Antes de la apertura
    # Alpaca manda la sesión anterior completa: eso no es "hoy" y se avisa.
    dia = ctx["equity_dia"]
    if dia["puntos"] and not dia["es_hoy"]:
        titulo_dia = "Equity de la última sesión"
        sub_dia = f"{_fecha_corta(dia['sesion'])} · velas de 5 min"
        nota_dia = (f'<div class="nota-info">Sin sesión hoy todavía: Alpaca devuelve la última sesión '
                    f'que tiene, la del {esc(_fecha_corta(dia["sesion"]))}.</div>')
    else:
        titulo_dia = "Equity de hoy"
        sub_dia = f"{_fecha_corta(dia['sesion'])} · velas de 5 min" if dia["puntos"] else "velas de 5 min"
        nota_dia = ""
    equity_html = (
        f'<div class="panel"><div class="titulo"><h2>{titulo_dia}</h2><span class="mono">{esc(sub_dia)}</span></div>'
        f'{_grafico_equity(dia, tz, "dia")}{_resumen_equity(dia)}{nota_dia}</div>'
        f'<div class="panel"><div class="titulo"><h2>Equity del último mes</h2><span class="mono">cierre diario</span></div>'
        f'{_grafico_equity(ctx["equity_mes"], tz, "mes")}{_resumen_equity(ctx["equity_mes"])}'
        + (f'<div class="nota">{esc(ctx["desajuste_equity"])}</div>' if ctx["desajuste_equity"] else "")
        + '</div>')

    if ctx["operaciones"]:
        def _tarjeta(op):
            marca_pendiente = '<span class="badge">pendiente</span>' if op.get("rol") == "pendiente" else ""
            nota_velas = (f'<div class="nota">{esc(op["velas"]["error"])}</div>'
                          if op["velas"].get("error") and op["velas"].get("velas") else "")
            nota_pendiente = ('<p class="vacio">Orden de entrada sin llenar. La entrada se marca cuando hay fill.</p>'
                              if op.get("rol") == "pendiente" else "")
            return (
                f'<div class="panel"><div class="titulo"><h2>{esc(op["ticker"])}</h2>'
                f'<span class="mono">{esc(_subtitulo_velas(op["velas"], tz, ctx["ahora"]))} {marca_pendiente}</span></div>'
                f'{_grafico_velas(op["velas"], op["marcas"], tz, ctx["ahora"])}'
                f'{nota_velas}{nota_pendiente}{_pie_marcas(op["marcas"], tz, ctx["ahora"])}</div>'
            )

        tarjetas = "".join(_tarjeta(op) for op in ctx["operaciones"])
        if ctx["operaciones_omitidas"]:
            tarjetas += (f'<p class="vacio">Sin graficar por el tope de tickers por corrida: '
                         f'{esc(", ".join(ctx["operaciones_omitidas"]))}.</p>')
        operaciones = f'<div class="operaciones">{tarjetas}</div>'
    elif ctx["hay_alpaca_operaciones"]:
        if ctx.get("pendientes_broker") is None:
            operaciones = '<p class="vacio">Sin posiciones abiertas. Las órdenes pendientes no se pudieron leer.</p>'
        else:
            operaciones = '<p class="vacio">Sin posiciones abiertas ni órdenes pendientes.</p>'
    else:
        operaciones = '<p class="vacio">Sin datos: Alpaca no respondió posiciones u órdenes.</p>'

    fuente = ctx.get("fuente_datos")
    if fuente and fuente != "Yahoo":
        sub_velas = (f"1 min · el gráfico pide a Yahoo · el hunter reporta {fuente} · pendiente va marcado")
    else:
        sub_velas = "1 min · misma fuente que el hunter · ruptura, entrada (fill), stop · pendiente va marcado"

    fuente_datos = (f'<span class="pildora">Fuente de datos: {esc(ctx["fuente_datos"])}</span>'
                    if ctx.get("fuente_datos") else "")
    sesion = '<span class="pildora ok">Sesión US abierta</span>' if ctx["en_sesion"] else '<span class="pildora">Sesión US cerrada</span>'
    alpaca = "" if ctx["alpaca_ok"] else '<span class="pildora mal">Alpaca sin conexión</span>'
    persist = ""
    if ctx["persist_fallidos"]:
        ultimo = ctx["persist_fallidos"][-1]
        persist = (f'<span class="pildora mal">Persist fallido ×{len(ctx["persist_fallidos"])} · '
                   f'último {esc(ultimo["hora"])} ({esc(ultimo["motivo"])})</span>')
    ia = ""
    if ctx.get("ia_fallos"):
        ultimo = ctx["ia_fallos"][-1]
        # El saldo es el caso que se fue en silencio el 2026-09-21. Si
        # hoy hubo al menos uno, la píldora lo dice; si no, es el fallo
        # técnico genérico. El motivo viene de fuera y se escapa.
        hay_credito = any(f.get("codigo") == "credito" for f in ctx["ia_fallos"])
        etiqueta = "IA sin crédito" if hay_credito else "IA fallo técnico"
        ia = (f'<span class="pildora mal">{etiqueta} ×{len(ctx["ia_fallos"])} · '
              f'último {esc(ultimo["hora"])} ({esc(ultimo["motivo"])})</span>')

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Momentum · panel paper</title>
<script>/* Aplica el tema elegido ANTES de pintar, para que el refresco de
cada 60 s no parpadee. Sin elección guardada manda el sistema. */
try{{var _t=localStorage.getItem("tema");if(_t==="dark"||_t==="light")document.documentElement.dataset.theme=_t;}}catch(_e){{}}</script>
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
    <span class="pildora paper">PAPER · ALPACA</span>{fuente_datos}{sesion}{alpaca}{persist}{ia}
    <span class="pildora">Actualizado {_hora(ctx['ahora'], tz, segundos=True)} {esc(etiqueta_tz)}</span>
    <span class="pildora">Solo lectura</span>
    <button class="pildora" id="tema-toggle" type="button" aria-label="Cambiar entre tema claro y oscuro" title="Cambiar tema claro/oscuro">Tema</button>
  </div>
</header>
{problemas}
<section class="fila c4" aria-label="Etapas del sistema">{etapas}</section>
<section class="fila c5" aria-label="Cifras clave">{kpis_html}</section>
<section class="fila c2i" aria-label="Curva de equity">{equity_html}</section>
{_html_broker(ctx)}
<section class="panel" aria-label="Velas de posiciones abiertas">
  <div class="titulo"><h2>Velas de posiciones abiertas</h2><span class="mono">{sub_velas}</span></div>
  {operaciones}
</section>
<section class="fila c2">
  <div class="panel"><div class="titulo"><h2>Watchlist actual</h2><span class="mono">generada {_hora(ctx['wl_momento'], tz, ahora=ctx['ahora'])}</span></div>{watch}</div>
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
<script>/* Botón de tema: alterna claro/oscuro y guarda la elección en el
navegador (por dispositivo). Sin elección previa parte de lo que pide el
sistema. Todo entre try por si el navegador bloquea el almacenamiento. */
(function(){{
  var b=document.getElementById("tema-toggle");if(!b)return;
  var root=document.documentElement;
  function actual(){{
    var t=root.dataset.theme;
    if(t==="dark"||t==="light")return t;
    try{{return window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}}catch(e){{return "light";}}
  }}
  function pinta(){{b.textContent="Tema: "+(actual()==="dark"?"Oscuro":"Claro");}}
  pinta();
  b.addEventListener("click",function(){{
    var nuevo=actual()==="dark"?"light":"dark";
    root.dataset.theme=nuevo;
    try{{localStorage.setItem("tema",nuevo);}}catch(e){{}}
    pinta();
  }});
}})();</script>
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
