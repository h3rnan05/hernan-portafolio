"""Régimen de mercado + racha → nivel defensivo, SOLO EN SOMBRA (PR-E).

GO del dueño 2026-10-01 15:55 UTC: "arranca la sombra". Este módulo
calcula un nivel (NORMAL / CAUTELA / DEFENSIVO) y las acciones que
TOMARÍA, pero nada lo lee para bloquear: el ejecutor solo registra
`gate_sombra` (PR-F). Encenderlo es el PR-H y exige otro GO.

Señales (cada una suma 1):
  - racha: 3 trades perdedores seguidos, o 2 sesiones seguidas con P&L
    realizado < 0, o los últimos 10 trades suman ≤ −4R;
  - spy_bajo_sma20: cierre previo de SPY bajo su media de 20 cierres;
  - clima_debil: SPY no está sobre su VWAP y su EMA9 (misma prueba que
    `momentum_hunter/mercado.py`);
  - vixy_sube: VIXY sube más de 5 % contra su cierre previo (proxy del
    VIX: el ^VIX no está en Alpaca);
  - dato_faltante: falta cualquiera de los datos de arriba. FAIL-CLOSED:
    no saber el régimen nunca cuenta como NORMAL.

Niveles: 0 señales = NORMAL; 1 = CAUTELA (máx 3 posiciones, sin small
caps); ≥2 = DEFENSIVO (máx 2, solo large cap, sin entradas los primeros
30 min ni la última hora; además, sin entradas nuevas si hay 2 sesiones
rojas seguidas y SPY bajo SMA20). Nunca más laxo que la base.

Histéresis: subir de nivel es inmediato; bajar exige 2 lecturas limpias
seguidas, y baja de a un nivel."""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("momentum_paper_trader.regimen")

NORMAL, CAUTELA, DEFENSIVO = "NORMAL", "CAUTELA", "DEFENSIVO"
_ORDEN = {NORMAL: 0, CAUTELA: 1, DEFENSIVO: 2}
_POR_ORDEN = {v: k for k, v in _ORDEN.items()}

RACHA_TRADES = 3
RACHA_SESIONES = 2
RACHA_R_10 = -4.0
VIXY_SUBE_PCT = 0.05
LECTURAS_PARA_BAJAR = 2
TTL_SEGUNDOS = 300

ACCIONES = {
    NORMAL: {"max_posiciones": None, "sin_small_caps": False, "sin_apertura_ni_ultima_hora": False,
             "sin_entradas": False},
    CAUTELA: {"max_posiciones": 3, "sin_small_caps": True, "sin_apertura_ni_ultima_hora": False,
              "sin_entradas": False},
    DEFENSIVO: {"max_posiciones": 2, "sin_small_caps": True, "sin_apertura_ni_ultima_hora": True,
                "sin_entradas": False},
}

RELATIVO_DIR = "momentum_paper_trader/aprendizaje"
ARCHIVO = "regimen.json"


def _senal(nombre: str, activa: bool | None, valor=None, detalle: str = "") -> dict:
    return {"nombre": nombre, "activa": activa, "valor": valor, "detalle": detalle}


# ───────────────────────── racha ─────────────────────────

def racha(trades: list[dict], hoy: str | None = None) -> dict:
    """Sobre trades CERRADOS de la memoria (cualquier `cuenta_para_aprender`:
    la racha es riesgo, no estadística). `hoy` excluye la sesión en curso
    del conteo de sesiones rojas."""
    cerrados = sorted((t for t in trades if t.get("salida_ts") and t.get("pnl") is not None),
                      key=lambda t: t["salida_ts"])
    perdedores = 0
    for t in reversed(cerrados):
        if t["pnl"] < 0:
            perdedores += 1
        else:
            break
    por_dia: dict[str, float] = defaultdict(float)
    for t in cerrados:
        por_dia[t["salida_ts"][:10]] += t["pnl"]
    rojas = 0
    for d in sorted((d for d in por_dia if hoy is None or d < hoy), reverse=True):
        if por_dia[d] < 0:
            rojas += 1
        else:
            break
    ult = [t.get("r") for t in cerrados[-10:]]
    r10 = round(sum(ult), 3) if len(ult) == 10 and all(r is not None for r in ult) else None
    motivos = []
    if perdedores >= RACHA_TRADES:
        motivos.append(f"{perdedores} trades perdedores seguidos")
    if rojas >= RACHA_SESIONES:
        motivos.append(f"{rojas} sesiones rojas seguidas")
    if r10 is not None and r10 <= RACHA_R_10:
        motivos.append(f"últimos 10 trades {r10:+.2f}R")
    return {"perdedores_seguidos": perdedores, "sesiones_rojas_seguidas": rojas, "r_ultimos_10": r10,
            "disparada": bool(motivos), "motivos": motivos}


# ───────────────────────── régimen ─────────────────────────

def spy_bajo_sma20(barras_diarias, hoy: str) -> dict:
    if barras_diarias is None or not getattr(barras_diarias, "close", None):
        return _senal("spy_bajo_sma20", None, detalle="sin barras diarias de SPY")
    pares = [(f, c) for f, c in zip(barras_diarias.fechas, barras_diarias.close, strict=False)
             if f and str(f)[:10] < hoy and c is not None]
    if len(pares) < 21:
        return _senal("spy_bajo_sma20", None, detalle=f"solo {len(pares)} cierres previos (hacen falta 21)")
    previo = pares[-1][1]
    sma = sum(c for _, c in pares[-21:-1]) / 20
    dif = previo / sma - 1
    return _senal("spy_bajo_sma20", previo < sma, round(dif, 4),
                  f"cierre previo {previo:.2f} vs SMA20 {sma:.2f} ({dif:+.2%})")


def clima_debil(intradia) -> dict:
    from momentum_hunter.factors import intradia as fi
    if intradia is None or not getattr(intradia, "close", None):
        return _senal("clima_debil", None, detalle="sin velas intradía de SPY")
    hoy = fi.barras_de_hoy(intradia)
    if not hoy.close:
        return _senal("clima_debil", None, detalle="sin velas de hoy de SPY")
    precio = hoy.close[-1]
    vwap, ema = fi.vwap_real(hoy), fi.ema9_intradia(hoy)
    if vwap is None or ema is None:
        return _senal("clima_debil", None, detalle="SPY sin VWAP/EMA9 todavía")
    debil = not (precio > vwap and precio > ema)
    return _senal("clima_debil", debil, round(precio / vwap - 1, 4),
                  f"SPY {precio:.2f} vs VWAP {vwap:.2f} / EMA9 {ema:.2f}")


def vixy_sube(vixy_diarias, vixy_intradia, hoy: str) -> dict:
    if vixy_diarias is None or vixy_intradia is None or not getattr(vixy_intradia, "close", None):
        return _senal("vixy_sube", None, detalle="sin datos de VIXY")
    previos = [c for f, c in zip(vixy_diarias.fechas, vixy_diarias.close, strict=False)
               if f and str(f)[:10] < hoy and c]
    if not previos:
        return _senal("vixy_sube", None, detalle="sin cierre previo de VIXY")
    ahora = vixy_intradia.close[-1]
    chg = ahora / previos[-1] - 1
    return _senal("vixy_sube", chg > VIXY_SUBE_PCT, round(chg, 4),
                  f"VIXY {ahora:.2f} vs cierre previo {previos[-1]:.2f} ({chg:+.2%})")


def nivel_crudo(senales: list[dict], rch: dict) -> tuple[str, list[str], dict]:
    activas = [s["nombre"] for s in senales if s["activa"] is True]
    faltan = [s["nombre"] for s in senales if s["activa"] is None]
    cuenta = len(activas) + (1 if faltan else 0) + (1 if rch.get("disparada") else 0)
    motivos = ([f"racha: {', '.join(rch['motivos'])}"] if rch.get("disparada") else []) + \
              [s["detalle"] for s in senales if s["activa"] is True] + \
              ([f"dato faltante: {', '.join(faltan)} (fail-closed)"] if faltan else [])
    nivel = NORMAL if cuenta == 0 else CAUTELA if cuenta == 1 else DEFENSIVO
    acciones = dict(ACCIONES[nivel])
    spy_sma = next((s for s in senales if s["nombre"] == "spy_bajo_sma20"), None)
    if (rch.get("sesiones_rojas_seguidas", 0) >= RACHA_SESIONES and spy_sma and spy_sma["activa"] is True):
        acciones["sin_entradas"] = True
    return nivel, motivos, acciones


def con_histeresis(nuevo: str, previo: dict | None) -> tuple[str, int]:
    """(nivel efectivo, lecturas limpias acumuladas)."""
    if not isinstance(previo, dict) or previo.get("nivel") not in _ORDEN:
        return nuevo, 0
    p = previo["nivel"]
    if _ORDEN[nuevo] >= _ORDEN[p]:
        return nuevo, 0
    limpias = int(previo.get("lecturas_para_bajar") or 0) + 1
    if limpias >= LECTURAS_PARA_BAJAR:
        return _POR_ORDEN[_ORDEN[p] - 1], 0
    return p, limpias


def evaluar(provider, trades: list[dict], ahora: datetime, previo: dict | None = None) -> dict:
    """Nunca lanza: un fallo de datos deja las señales en None (fail-closed)."""
    hoy = ahora.astimezone(UTC).date().isoformat()
    d = intr = {}
    try:
        d = provider.barras(["SPY", "VIXY"], dias=45) or {}
    except Exception as ex:
        log.warning("régimen: no se pudieron leer barras diarias (%s)", type(ex).__name__)
    try:
        intr = provider.barras_intradia(["SPY", "VIXY"], intervalo="1m", periodo="1d") or {}
    except Exception as ex:
        log.warning("régimen: no se pudieron leer velas intradía (%s)", type(ex).__name__)
    senales = [spy_bajo_sma20(d.get("SPY"), hoy), clima_debil(intr.get("SPY")),
               vixy_sube(d.get("VIXY"), intr.get("VIXY"), hoy)]
    rch = racha(trades, hoy)
    crudo, motivos, acc_crudo = nivel_crudo(senales, rch)
    efectivo, limpias = con_histeresis(crudo, previo)
    acciones = dict(ACCIONES[efectivo], sin_entradas=acc_crudo["sin_entradas"])
    desde = previo.get("desde") if isinstance(previo, dict) and previo.get("nivel") == efectivo else None
    return {"version": 1, "modo": "sombra", "calculado_en": ahora.astimezone(UTC).isoformat(timespec="seconds"),
            "nivel": efectivo, "nivel_crudo": crudo, "lecturas_para_bajar": limpias,
            "desde": desde or ahora.astimezone(UTC).isoformat(timespec="seconds"),
            "senales": senales, "racha": rch, "motivos": motivos, "acciones_sombra": acciones}


# ───────────────────────── persistencia ─────────────────────────

def ruta(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base) / ARCHIVO
    from momentum_hunter.rutas_estado import resolver
    return resolver(RELATIVO_DIR, es_dir=True) / ARCHIVO


def cargar(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return d if isinstance(d, dict) and d.get("nivel") in _ORDEN else None


def guardar(p: Path, d: dict) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as ex:
        log.warning("régimen: no se pudo guardar (%s)", type(ex).__name__)


def vigente(ahora: datetime, calcular, p: Path, ttl: int = TTL_SEGUNDOS) -> dict | None:
    """Lee el último cálculo; si tiene más de `ttl` s, recalcula con
    `calcular(previo)` y lo guarda. Nunca lanza: None si todo falla."""
    previo = cargar(p)
    try:
        if previo and previo.get("calculado_en"):
            edad = (ahora - datetime.fromisoformat(previo["calculado_en"])).total_seconds()
            if 0 <= edad < ttl:
                return previo
        nuevo = calcular(previo)
        guardar(p, nuevo)
        return nuevo
    except Exception as ex:
        log.warning("régimen: no se pudo calcular (%s)", type(ex).__name__)
        return previo
