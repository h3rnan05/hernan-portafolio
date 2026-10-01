"""Memoria de trades: un registro por trade cerrado, con lo que se sabía
al decidir y cómo terminó (PR-A del diseño de aprendizaje, GO del dueño
2026-10-01 15:55 UTC).

POR QUÉ EXISTE. Para aprender de los errores hace falta cruzar la
decisión con el desenlace. Hoy eso está repartido: la confianza y los
niveles en `revisiones.json`; el patrón, el catalizador y el gap solo en
la watchlist viva (se pierden al archivar) o en la auditoría del hunter;
el fill real y la salida solo en Alpaca. Este módulo lo junta en
`<estado>/momentum_paper_trader/aprendizaje/memoria_trades.jsonl`.

SOLO DATOS. Nada de aquí decide, cambia un umbral, el sizing ni la
política de la IA. El único acceso al bróker es `estado_orden` (GET de
solo lectura sobre el host paper).

FAIL-CLOSED. Un campo que no se pudo medir queda `None` ("sin dato"),
nunca 0. Un trade sin fill de entrada o de salida no entra a la memoria
(todavía no es un trade cerrado medible). Un R con riesgo ≤ 0 o ausente
es `None`.

`cuenta_para_aprender` separa la muestra válida: falso para trades de
antes de #190 (cierre al final del día confirmado desde el 2026-09-28) y
para cualquier arrastre overnight, que no son el sistema de hoy."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("momentum_paper_trader.memoria_trades")

RELATIVO_DIR = "momentum_paper_trader/aprendizaje"
ARCHIVO = "memoria_trades.jsonl"
VERSION = 1

# Primer día con el cierre de fin de sesión confirmado (#190). Lo de
# antes tiene arrastres overnight que la regla actual ya no permite.
FECHA_MINIMA_VALIDA = "2026-09-28"

_RASGOS_WATCHLIST = (
    ("patron", "ultimo_patron"),
    ("catalizador_tipo", "catalizador_tipo"),
    ("catalizador_fuente", "catalizador_fuente"),
    ("catalizador_fecha", "catalizador_fecha"),
    ("gap_pct", "gap_pct_congelado"),
    ("velas_desde_ruptura", "velas_desde_ruptura"),
    ("score_base", "score_base"),
    ("clima_mercado", "clima_mercado"),
    ("shares_float", "shares_float"),
    ("short_pct_float", "short_pct_float"),
    ("atr_diario", "atr_diario"),
    ("es_large_cap", "es_large_cap"),
    ("signal_latency_ms", "signal_latency_ms"),
)


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _ts(v) -> datetime | None:
    if not v or not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


def _primer_disparo(e) -> str | None:
    for t in getattr(e, "transiciones", None) or []:
        estado = t.get("estado") if isinstance(t, dict) else getattr(t, "estado", None)
        ts = t.get("timestamp") if isinstance(t, dict) else getattr(t, "timestamp", None)
        if estado == "triggered" and ts:
            return ts
    return None


def rasgos_de_entrada(e, decision=None) -> dict | None:
    """Foto de los rasgos de la señal AL DECIDIR. Nunca lanza: si algo
    falla devuelve None (la revisión sigue igual que antes de este
    campo)."""
    try:
        r: dict = {"version": VERSION}
        for destino, origen in _RASGOS_WATCHLIST:
            v = getattr(e, origen, None)
            r[destino] = v if (v is None or isinstance(v, (str, bool, int, float))) else str(v)
        r["primer_disparo_ts"] = _primer_disparo(e)
        r["fraccion_ia"] = _num(getattr(decision, "fraccion", None)) if decision is not None else None
        return r
    except Exception:
        return None


# ───────────────────────── respaldo desde la auditoría ─────────────────────────

def rasgos_desde_auditoria(ticker: str, hasta: datetime, auditoria_dir: Path | None) -> dict | None:
    """Para revisiones de antes de `rasgos`: el último candidato
    `alertada` del hunter para ese ticker, ese día, antes de `hasta`."""
    if auditoria_dir is None:
        return None
    ruta = Path(auditoria_dir) / f"{hasta.date().isoformat()}.json"
    try:
        corridas = json.loads(ruta.read_text(encoding="utf-8")).get("corridas") or []
    except Exception:
        return None
    mejor = None
    primero = None
    for c in corridas:
        ts = _ts(c.get("timestamp"))
        if ts is None or ts > hasta:
            continue
        for cand in c.get("candidatos") or []:
            if cand.get("ticker") != ticker or cand.get("decision") != "alertada":
                continue
            if primero is None or ts < primero:
                primero = ts
            if mejor is None or ts >= mejor[0]:
                mejor = (ts, cand)
    if mejor is None:
        return None
    cand = mejor[1]
    fi = cand.get("factores_intradia") or {}
    ev = cand.get("evaluacion") or {}
    cat = cand.get("catalizador") or {}
    meta = cand.get("meta") or {}
    return {
        "version": VERSION, "origen": "auditoria",
        "patron": ev.get("patron"), "catalizador_tipo": cat.get("tipo"),
        "catalizador_fuente": cat.get("fuente"), "catalizador_fecha": cat.get("fecha"),
        "gap_pct": _num(fi.get("gap_pct")), "velas_desde_ruptura": fi.get("velas_desde_ruptura"),
        "score_base": _num(ev.get("score_base")), "clima_mercado": None,
        "shares_float": _num(meta.get("float_acciones")), "short_pct_float": _num(meta.get("short_pct_float")),
        "atr_diario": None, "es_large_cap": cand.get("es_large_cap"), "signal_latency_ms": None,
        "primer_disparo_ts": primero.isoformat() if primero else None, "fraccion_ia": None,
    }


# ───────────────────────── trade cerrado ─────────────────────────

def _fill(orden: dict | None) -> tuple[float | None, datetime | None, float | None]:
    if not isinstance(orden, dict) or orden.get("status") not in ("filled", "partially_filled"):
        return None, None, None
    return _num(orden.get("filled_avg_price")), _ts(orden.get("filled_at")), _num(orden.get("filled_qty"))


def banda_precio(p: float | None) -> str | None:
    if p is None:
        return None
    return "<5" if p < 5 else "5-20" if p < 20 else "20-100" if p < 100 else ">=100"


def franja_et(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    from zoneinfo import ZoneInfo
    t = ts.astimezone(ZoneInfo("America/New_York"))
    m = t.hour * 60 + t.minute
    return "apertura" if m < 630 else "media" if m < 840 else "tarde"


def construir_trade(rev, orden: dict | None, orden_cierre: dict | None = None,
                    rasgos_respaldo: dict | None = None) -> dict | None:
    """Registro del trade, o None si todavía no está cerrado y medido.
    `orden` = la del bracket con `nested=true`; `orden_cierre` = la
    liquidación de `cierre.py` si la hubo."""
    p_in, t_in, q_in = _fill(orden)
    if p_in is None or t_in is None or not q_in:
        return None
    p_out = t_out = None
    motivo = None
    for leg in (orden or {}).get("legs") or []:
        p, t, _q = _fill(leg)
        if p is not None and t is not None:
            p_out, t_out = p, t
            motivo = "stop" if leg.get("type") in ("stop", "stop_limit", "trailing_stop") else "objetivo"
    if p_out is None:
        p, t, _q = _fill(orden_cierre)
        if p is not None and t is not None:
            p_out, t_out, motivo = p, t, "cierre"
    if p_out is None:
        return None
    if t_out.date() > t_in.date():
        motivo = "arrastre_overnight"
    entrada_plan = _num(getattr(rev, "precio_entrada", None))
    stop = _num(getattr(rev, "stop", None))
    riesgo = (entrada_plan - stop) if (entrada_plan is not None and stop is not None) else None
    r_mult = round((p_out - p_in) / riesgo, 4) if riesgo and riesgo > 0 else None
    rasgos = getattr(rev, "rasgos", None) or rasgos_respaldo or {}
    disparo = _ts(rasgos.get("primer_disparo_ts"))
    espera = round((t_in - disparo).total_seconds() / 60, 1) if disparo else None
    es_large = getattr(rev, "es_large_cap", None)
    if es_large is None:
        es_large = rasgos.get("es_large_cap")
    valido = t_in.date().isoformat() >= FECHA_MINIMA_VALIDA and motivo != "arrastre_overnight"
    return {
        "version": VERSION, "order_id": getattr(rev, "order_id", None), "ticker": rev.ticker,
        "creado_en": rev.creado_en, "fecha": t_in.date().isoformat(),
        "entrada_ts": t_in.isoformat(), "salida_ts": t_out.isoformat(),
        "cantidad": q_in, "precio_fill": p_in, "precio_salida": p_out,
        "precio_entrada_plan": entrada_plan, "stop": stop, "objetivo": _num(getattr(rev, "objetivo", None)),
        "slippage_entrada": round(p_in - entrada_plan, 4) if entrada_plan is not None else None,
        "pnl": round((p_out - p_in) * q_in, 2), "r": r_mult, "motivo_salida": motivo,
        "confianza": getattr(rev, "confianza", None), "es_large_cap": es_large,
        "latencia_e2e_ms": _num(getattr(rev, "latencia_e2e_ms", None)),
        "espera_desde_disparo_min": espera,
        "banda_precio": banda_precio(p_in), "franja_et": franja_et(t_in),
        "cuenta_para_aprender": valido,
        **{k: rasgos.get(k) for k in ("patron", "catalizador_tipo", "catalizador_fuente",
                                      "gap_pct", "velas_desde_ruptura", "score_base",
                                      "clima_mercado", "shares_float", "fraccion_ia")},
        "rasgos_origen": "revision" if getattr(rev, "rasgos", None) else (
            "auditoria" if rasgos_respaldo else None),
    }


# ───────────────────────── persistencia ─────────────────────────

def ruta_memoria(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base) / ARCHIVO
    from momentum_hunter.rutas_estado import resolver
    return resolver(RELATIVO_DIR, es_dir=True) / ARCHIVO


def cargar(ruta: Path) -> list[dict]:
    """Líneas ilegibles se saltan (se loguean); el archivo ausente = []."""
    if not ruta.exists():
        return []
    out = []
    for i, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines()):
        if not linea.strip():
            continue
        try:
            d = json.loads(linea)
        except json.JSONDecodeError:
            log.warning("memoria: línea %d ilegible, se salta", i + 1)
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def guardar(ruta: Path, trades: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n" for t in trades),
                   encoding="utf-8")
    os.replace(tmp, ruta)


def actualizar(ruta: Path, revisiones: list, obtener_orden, auditoria_dir: Path | None = None) -> dict:
    """Idempotente por `order_id`. `obtener_orden(id) -> dict` (GET). Un
    error del bróker en un trade no frena a los demás."""
    existentes = cargar(ruta)
    vistos = {t.get("order_id") for t in existentes}
    nuevos, errores, abiertos = [], 0, 0
    for rev in revisiones:
        oid = getattr(rev, "order_id", None)
        if not getattr(rev, "entro", False) or not oid or oid in vistos:
            continue
        try:
            orden = obtener_orden(oid)
            cierre_id = getattr(rev, "cierre_order_id", None)
            orden_cierre = obtener_orden(cierre_id) if cierre_id else None
        except Exception as ex:
            log.warning("memoria: no se pudo leer la orden de %s (%s)", rev.ticker, type(ex).__name__)
            errores += 1
            continue
        respaldo = None
        if not getattr(rev, "rasgos", None):
            hasta = _ts(getattr(rev, "timestamp", None))
            if hasta is not None:
                respaldo = rasgos_desde_auditoria(rev.ticker, hasta + timedelta(seconds=5), auditoria_dir)
        trade = construir_trade(rev, orden, orden_cierre, respaldo)
        if trade is None:
            abiertos += 1
            continue
        nuevos.append(trade)
        vistos.add(oid)
    if nuevos:
        todos = sorted(existentes + nuevos, key=lambda t: t.get("entrada_ts") or "")
        guardar(ruta, todos)
    return {"nuevos": len(nuevos), "total": len(existentes) + len(nuevos),
            "sin_cerrar": abiertos, "errores": errores}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Actualiza la memoria de trades (solo lectura del bróker).")
    ap.add_argument("--salida", type=Path, default=None, help="carpeta de salida (default: estado)")
    args = ap.parse_args(argv)
    from momentum_paper_trader import estado
    from momentum_paper_trader.alpaca_client import AlpacaPaperClient
    key, secret = os.getenv("ALPACA_PAPER_API_KEY"), os.getenv("ALPACA_PAPER_API_SECRET")
    if not key or not secret:
        log.error("sin credenciales paper -- no se actualiza la memoria")
        return 1
    from momentum_hunter.rutas_estado import destino
    res = actualizar(ruta_memoria(args.salida), estado.cargar(estado.PATH),
                     AlpacaPaperClient(key, secret).estado_orden,
                     destino("momentum_hunter/auditoria"))
    log.info("memoria de trades: %s", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
