"""Job nocturno de aprendizaje (PR-C). Corre después del cierre.

GO del dueño 2026-10-01 15:55 UTC: "arranca la sombra y el panel".

Pasos (todo de solo lectura salvo sus propias salidas en
`<estado>/momentum_paper_trader/aprendizaje/`):
  1. Actualiza la memoria de trades (`memoria_trades.py`; GET paper).
  2. Estadísticas por segmento con guardas (`aprendizaje_stats.py`).
  3. Propuestas de ajuste dentro de la allowlist (`autoajuste.py`).
     TODAS en sombra: nada de esto bloquea una orden real.
  4. Sombra "qué habría hecho":
       - en vivo: eventos `gate_sombra` del ejecutor (PR-F) cruzados con
         los trades reales del día. P&L sombra = real − P&L de los trades
         que un knob habría bloqueado (exacto, porque solo se aprieta);
       - retro (in-sample, solo orientativo): la regla de racha K3
         aplicada a la historia de la memoria.
  5. Reporte JSON + texto en `reportes/AAAA-MM-DD.*` y una línea por día
     en `sombra_diaria.jsonl`. Telegram SOLO con `--telegram` (apagado
     por defecto; el wrapper lo enciende con
     MOMENTUM_APRENDIZAJE_TELEGRAM=1).

Fail-closed: un paso que falla queda anotado en `problemas` y el reporte
sale igual; un número que no se pudo medir es "sin dato", nunca 0."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from momentum_paper_trader import aprendizaje_stats as stats_mod
from momentum_paper_trader import autoajuste, memoria_trades, regimen

log = logging.getLogger("momentum_paper_trader.aprendizaje")

ARCHIVO_SOMBRA = "sombra_diaria.jsonl"


def directorio(base: Path | None = None) -> Path:
    if base is not None:
        Path(base).mkdir(parents=True, exist_ok=True)
        return Path(base)
    from momentum_hunter.rutas_estado import resolver
    return resolver(memoria_trades.RELATIVO_DIR, es_dir=True)


# ───────────────────────── sombra ─────────────────────────

def leer_gates(ruta_eventos: Path | None, fecha: str) -> list[dict]:
    if ruta_eventos is None or not Path(ruta_eventos).exists():
        return []
    out = []
    try:
        with Path(ruta_eventos).open(encoding="utf-8") as f:
            for linea in f:
                if '"gate_sombra"' not in linea:
                    continue
                try:
                    e = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                if e.get("tipo") == "gate_sombra" and str(e.get("ts", ""))[:10] == fecha:
                    out.append(e)
    except OSError:
        return []
    return out


def sombra_en_vivo(trades_dia: list[dict], gates: list[dict]) -> dict:
    """Trades reales que algún knob habría bloqueado, por knob y combinado."""
    bloqueos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for g in gates:
        knobs = {b.get("knob") for b in (g.get("bloquearia") or []) if b.get("knob")}
        if knobs:
            bloqueos[(g.get("ticker"), g.get("creado_en"))] |= knobs
    real = round(sum(t["pnl"] for t in trades_dia if t.get("pnl") is not None), 2) if trades_dia else 0.0
    por_knob: dict[str, dict] = defaultdict(lambda: {"bloqueados": 0, "pnl_bloqueado": 0.0,
                                                     "ganadores_bloqueados": 0})
    comb = {"bloqueados": 0, "pnl_bloqueado": 0.0, "ganadores_bloqueados": 0, "tickers": []}
    for t in trades_dia:
        knobs = bloqueos.get((t.get("ticker"), t.get("creado_en")))
        if not knobs or t.get("pnl") is None:
            continue
        for k in knobs:
            pk = por_knob[k]
            pk["bloqueados"] += 1
            pk["pnl_bloqueado"] = round(pk["pnl_bloqueado"] + t["pnl"], 2)
            pk["ganadores_bloqueados"] += int(t["pnl"] > 0)
        comb["bloqueados"] += 1
        comb["pnl_bloqueado"] = round(comb["pnl_bloqueado"] + t["pnl"], 2)
        comb["ganadores_bloqueados"] += int(t["pnl"] > 0)
        comb["tickers"].append(t.get("ticker"))
    return {"pnl_real": real, "pnl_sombra": round(real - comb["pnl_bloqueado"], 2),
            "delta": round(-comb["pnl_bloqueado"], 2), "combinado": comb, "por_knob": dict(por_knob),
            "gates_registrados": len(gates)}


def _abiertas_al_entrar(t: dict, kept: list[dict]) -> int:
    return sum(1 for k in kept if k["entrada_ts"] <= t["entrada_ts"] < k["salida_ts"])


def sombra_retro_k3(trades: list[dict], cotas: dict | None) -> list[dict]:
    """Retro in-sample: K3 (máx posiciones tras racha) aplicado trade a
    trade con lo que se sabía al entrar. Solo orientativo."""
    if not cotas or autoajuste.K3 not in cotas.get("knobs", {}):
        return []
    tope = cotas["knobs"][autoajuste.K3]["valor"]
    orden = sorted((t for t in trades if t.get("entrada_ts") and t.get("salida_ts") and t.get("pnl") is not None),
                   key=lambda t: t["entrada_ts"])
    kept: list[dict] = []
    por_dia: dict[str, dict] = {}
    for t in orden:
        previos = [x for x in orden if x["salida_ts"] <= t["entrada_ts"]]
        r = regimen.racha(previos, hoy=t["fecha"])
        bloquea = r["disparada"] and _abiertas_al_entrar(t, kept) >= tope
        d = por_dia.setdefault(t["fecha"], {"fecha": t["fecha"], "pnl_real": 0.0, "pnl_bloqueado": 0.0,
                                            "bloqueados": 0, "ganadores_bloqueados": 0, "tickers": []})
        d["pnl_real"] = round(d["pnl_real"] + t["pnl"], 2)
        if bloquea:
            d["pnl_bloqueado"] = round(d["pnl_bloqueado"] + t["pnl"], 2)
            d["bloqueados"] += 1
            d["ganadores_bloqueados"] += int(t["pnl"] > 0)
            d["tickers"].append(t["ticker"])
        else:
            kept.append(t)
    out = []
    for d in por_dia.values():
        d["pnl_sombra"] = round(d["pnl_real"] - d["pnl_bloqueado"], 2)
        d["delta"] = round(-d["pnl_bloqueado"], 2)
        out.append(d)
    return sorted(out, key=lambda d: d["fecha"])


def registrar_sombra_diaria(ruta: Path, fila: dict) -> None:
    filas = []
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("fecha") != fila["fecha"]:
                filas.append(d)
    filas.append(fila)
    filas.sort(key=lambda d: d.get("fecha") or "")
    tmp = ruta.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n" for d in filas), encoding="utf-8")
    os.replace(tmp, ruta)


# ───────────────────────── reporte ─────────────────────────

def _fmt(v, f="{:+.2f}") -> str:
    return "sin dato" if v is None else f.format(v)


def texto_reporte(r: dict) -> str:
    g = r["estadisticas"]["global"]
    lin = [f"📚 Aprendizaje {r['fecha']} (SOMBRA: nada se aplica)",
           f"Muestra válida: n={g['n']}, sesiones={g['sesiones']}, R medio {_fmt(g['r_medio'])}, "
           f"P&L {_fmt(g['pnl'])} USD"]
    if g["n"] < stats_mod.N_MINIMO or g["sesiones"] < stats_mod.SESIONES_MINIMAS:
        lin.append(f"Muestra insuficiente (mínimo n={stats_mod.N_MINIMO} y {stats_mod.SESIONES_MINIMAS} sesiones por segmento).")
    obs = [s for s in r["estadisticas"]["segmentos"] if s["estado"] in ("observacion", "propuesta") and s["n"] >= 3]
    for s in sorted(obs, key=lambda s: (s["r_shr"] if s["r_shr"] is not None else 0))[:5]:
        lin.append(f"· {s['dimension']}={s['valor']}: n={s['n']} R {_fmt(s['r_medio'])} (shr {_fmt(s['r_shr'])}) → {s['estado']}")
    if r["ajustes_vigentes"]:
        lin.append("Ajustes en sombra:")
        for a in r["ajustes_vigentes"]:
            lin.append(f"· {a['knob']} = {a['valor']} ({a['motivo']}), vence {a['vence']}")
    else:
        lin.append("Ajustes propuestos: ninguno.")
    sv = r["sombra_en_vivo"]
    lin.append(f"Sombra hoy: real {_fmt(sv['pnl_real'])} · sombra {_fmt(sv['pnl_sombra'])} · Δ {_fmt(sv['delta'])} "
               f"({sv['combinado']['bloqueados']} bloqueados, {sv['gates_registrados']} gates)")
    for p in r["problemas"]:
        lin.append(f"⚠️ {p}")
    return "\n".join(lin)


def correr(fecha: str, base: Path | None, ruta_eventos: Path | None, obtener_orden=None,
           revisiones=None, auditoria_dir: Path | None = None, ruta_cotas: Path = autoajuste.RUTA_COTAS,
           ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(UTC)
    d = directorio(base)
    problemas: list[str] = []
    ruta_mem = d / memoria_trades.ARCHIVO
    if obtener_orden is not None and revisiones is not None:
        try:
            res = memoria_trades.actualizar(ruta_mem, revisiones, obtener_orden, auditoria_dir)
            if res["errores"]:
                problemas.append(f"memoria: {res['errores']} orden(es) no se pudieron leer")
        except Exception as ex:
            problemas.append(f"memoria no actualizada ({type(ex).__name__})")
    else:
        problemas.append("memoria no actualizada (sin acceso al bróker)")
    trades = memoria_trades.cargar(ruta_mem)
    est = stats_mod.por_segmento(trades)
    cotas, prob = autoajuste.cotas_o_none(ruta_cotas)
    if prob:
        problemas.append(prob)
    rch = regimen.racha(trades)
    reg_noche = {"racha": rch, "acciones_sombra": {}}
    previos = (autoajuste.cargar(autoajuste.ruta(d)) or {}).get("ajustes") or []
    if cotas:
        nuevos = autoajuste.proponer(est, trades, reg_noche, cotas, fecha)
        vigentes, vencidos = autoajuste.combinar(previos, nuevos, fecha)
    else:
        nuevos, vigentes, vencidos = [], [], []
    try:
        autoajuste.guardar(autoajuste.ruta(d), vigentes, vencidos, ahora, prob)
    except Exception as ex:
        problemas.append(f"ajustes no guardados ({type(ex).__name__})")
    trades_dia = [t for t in trades if t.get("fecha") == fecha]
    sv = sombra_en_vivo(trades_dia, leer_gates(ruta_eventos, fecha))
    retro = sombra_retro_k3(trades, cotas)
    fila = {"fecha": fecha, "modo": "en_vivo", **{k: sv[k] for k in ("pnl_real", "pnl_sombra", "delta")},
            "bloqueados": sv["combinado"]["bloqueados"], "ganadores_bloqueados": sv["combinado"]["ganadores_bloqueados"],
            "gates_registrados": sv["gates_registrados"], "por_knob": sv["por_knob"], "n_trades": len(trades_dia)}
    try:
        registrar_sombra_diaria(d / ARCHIVO_SOMBRA, fila)
    except Exception as ex:
        problemas.append(f"sombra diaria no guardada ({type(ex).__name__})")
    rep = {"version": 1, "fecha": fecha, "generado_en": ahora.isoformat(timespec="seconds"), "modo": "sombra",
           "estadisticas": est, "racha": rch, "propuestas_hoy": nuevos, "ajustes_vigentes": vigentes,
           "ajustes_vencidos_hoy": vencidos, "sombra_en_vivo": sv, "sombra_retro_k3": retro,
           "problemas": problemas}
    rep["texto"] = texto_reporte(rep)
    carpeta = d / "reportes"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"{fecha}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (carpeta / f"{fecha}.txt").write_text(rep["texto"] + "\n", encoding="utf-8")
    (d / "ultimo_reporte.json").write_text(json.dumps(rep, ensure_ascii=False, default=str), encoding="utf-8")
    return rep


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Aprendizaje nocturno (solo sombra).")
    ap.add_argument("--fecha", default=None, help="AAAA-MM-DD (default: hoy en Nueva York)")
    ap.add_argument("--salida", type=Path, default=None)
    ap.add_argument("--telegram", action="store_true", help="además manda el resumen por Telegram (apagado por defecto)")
    args = ap.parse_args(argv)
    from zoneinfo import ZoneInfo
    fecha = args.fecha or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    obtener = revs = aud = None
    key, secret = os.getenv("ALPACA_PAPER_API_KEY"), os.getenv("ALPACA_PAPER_API_SECRET")
    try:
        from momentum_hunter.rutas_estado import destino
        from momentum_paper_trader import estado
        from momentum_paper_trader.alpaca_client import AlpacaPaperClient
        revs = estado.cargar(estado.PATH)
        aud = destino("momentum_hunter/auditoria")
        if key and secret:
            obtener = AlpacaPaperClient(key, secret).estado_orden
    except Exception as ex:
        log.warning("aprendizaje: sin revisiones o sin bróker (%s)", type(ex).__name__)
    from dashboard.events import ruta_eventos
    rep = correr(fecha, args.salida, ruta_eventos(), obtener, revs, aud)
    print(rep["texto"])
    if args.telegram:
        from momentum_paper_trader import notify
        notify.enviar(notify.escapar(rep["texto"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
