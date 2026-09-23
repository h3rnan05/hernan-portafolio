"""Reporte de embudo -- cuántas candidatas entran en cada etapa y por qué
se van, de punta a punta: universo → catalizador → evaluador → watchlist
→ disparo → guardarraíles del ejecutor → IA → orden.

POR QUÉ EXISTE (2026-09-23, fase 0 de la estrategia aprobada por el
dueño). La regla de oro es no relajar un filtro sin medir primero cuál
filtro elimina qué. Cada pieza de ese dato ya se guardaba en algún lado
(la telemetría del escaneo, la auditoría minuto a minuto, las
transiciones de la watchlist, `revisiones.json`, la telemetría paper y
la sombra de movers), pero repartida en seis archivos con seis formatos:
responder "¿dónde se muere el embudo?" exigía scripts sueltos cada vez.
Este módulo junta esas fuentes en UNA tabla por rango de días, con los
motivos exactos, para que relajar sea una decisión con números.

QUÉ HACE Y QUÉ NO. Solo LEE y CUENTA: nunca decide, nunca cambia
umbrales, nunca pide datos de mercado ni toca el bróker. Si una fuente
falta o no parsea, esa sección sale vacía y el reporte lo dice; nunca
se rellena con ceros que parezcan medidos (regla 6). Vive en el paper
trader porque cruza datos de las dos fases; `momentum_hunter` no lo
importa.

LIMITACIÓN HONESTA sobre la IA: `revisiones.json` guarda la confianza y
el veredicto YA re-validado en código (un 6 con "entrar": true del
modelo quedó como `ia_entraria=False` mientras el umbral fue 7). Por
eso "aprobarían con umbral k" cuenta revisiones con confianza >= k, que
es una cota superior, no el veredicto del modelo a ese umbral.

USO
  python -m momentum_paper_trader.embudo                       # últimos 7 días
  python -m momentum_paper_trader.embudo --desde 2026-09-22 --hasta 2026-09-22
  python -m momentum_paper_trader.embudo --json                # además, JSON en stdout
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter import telemetria as telem_hunter
from momentum_hunter import watchlist
from momentum_hunter.audit import DIR_AUDITORIA
from momentum_hunter.config import CONFIG as CFG_HUNTER
from momentum_paper_trader import estado
from momentum_paper_trader import telemetria as telem_paper

log = logging.getLogger("momentum_paper_trader.embudo")

ARCHIVO_MOVERS = "movers.jsonl"
UMBRALES_IA = (5, 6, 7)
UMBRALES_SCORE = (45.0, 50.0, 55.0)
_RE_VELAS = re.compile(r"hace (\d+) velas")


@dataclass
class Embudo:
    desde: str
    hasta: str
    # -- Etapa 1 (telemetría del escaneo; sumas sobre todas las corridas) --
    escaneos: int = 0
    universo_escaneado: int = 0
    etapa1: dict[str, int] = field(default_factory=dict)          # operables, con_alguna_noticia, ...
    etapa1_por_banda: dict[str, dict[str, int]] = field(default_factory=dict)
    rechazos_universo: dict[str, int] = field(default_factory=dict)
    keyword_rechazos: dict[str, int] = field(default_factory=dict)
    condiciones: dict[str, int] = field(default_factory=dict)      # patron, temprano, riesgo, dinero, umbral
    # -- Etapa 2 (auditoría, por ticker-día único) --
    evaluadas_unicas: int = 0
    con_patron_alguna_vez: int = 0
    con_tres_condiciones_alguna_vez: int = 0    # patrón + temprano + riesgo en la misma lectura
    accionables_unicas: int = 0
    casi: list[dict] = field(default_factory=list)   # tres condiciones sí, score nunca llegó
    casi_pasarian_con_score: dict[str, int] = field(default_factory=dict)
    penalizaciones: dict[str, int] = field(default_factory=dict)   # por ticker-día, en su mejor lectura
    # -- Watchlist (entradas creadas en el rango) --
    watchlist_creadas: int = 0
    watchlist_por_estado: dict[str, int] = field(default_factory=dict)
    watchlist_dispararon: int = 0
    watchlist_por_banda: dict[str, int] = field(default_factory=dict)
    missed_velas: list[int] = field(default_factory=list)
    invalidated_motivos: dict[str, int] = field(default_factory=dict)
    # -- Ejecutor / IA (revisiones con timestamp en el rango) --
    revisiones: int = 0
    ia_consultada: int = 0
    ia_confianza: dict[str, int] = field(default_factory=dict)
    ia_aprobarian_con_umbral: dict[str, int] = field(default_factory=dict)
    entraron: int = 0
    no_operadas_por_motivo: dict[str, int] = field(default_factory=dict)
    # -- Paper (telemetría de sesión) --
    ticks_paper: int = 0
    ordenes_colocadas: int = 0
    # -- Sombra de movers --
    sombra_corridas: int = 0
    sombra_clase_a: int = 0
    sombra_clase_b: int = 0
    sombra_rechazos: dict[str, int] = field(default_factory=dict)
    # -- Fuentes que faltaron (se dice, no se rellena) --
    fuentes_vacias: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Etapa 1: telemetría del escaneo
# --------------------------------------------------------------------------

def _etapa1(emb: Embudo, corridas: list[dict]) -> None:
    escaneos = [c for c in corridas if c.get("modo", "escaneo") == "escaneo"]
    if not escaneos:
        emb.fuentes_vacias.append("telemetria_hunter")
        return
    emb.escaneos = len(escaneos)
    totales: dict[str, Counter] = {}
    rech, kw, cond = Counter(), Counter(), Counter()
    for c in escaneos:
        emb.universo_escaneado += int(c.get("universo_escaneado") or 0)
        e = c.get("embudo") if isinstance(c.get("embudo"), dict) else {}
        for etapa in ("operables", "con_alguna_noticia", "con_catalizador", "evaluadas", "accionables"):
            por_banda = e.get(etapa)
            if isinstance(por_banda, dict):
                totales.setdefault(etapa, Counter()).update(
                    {k: int(v) for k, v in por_banda.items() if isinstance(v, (int, float))})
        for k, v in (e.get("rechazos_universo") or {}).items():
            rech[k] += int(v)
        for k, v in (e.get("keyword_rechazos") or {}).items():
            kw[k] += int(v)
        for k, v in (c.get("condiciones") or {}).items():
            cond[k] += int(v)
    emb.etapa1 = {k: sum(v.values()) for k, v in totales.items()}
    emb.etapa1_por_banda = {k: dict(v) for k, v in totales.items()}
    emb.rechazos_universo = dict(rech.most_common())
    emb.keyword_rechazos = dict(kw.most_common())
    emb.condiciones = dict(cond)


# --------------------------------------------------------------------------
# Etapa 2: auditoría, por ticker-día
# --------------------------------------------------------------------------

def _cargar_auditoria(desde: str, hasta: str, dir_auditoria: Path) -> list[tuple[str, dict]]:
    """(día, snapshot) de cada candidata en cada corrida del rango."""
    halladas: list[tuple[str, dict]] = []
    if not dir_auditoria.exists():
        return halladas
    for path in sorted(dir_auditoria.glob("*.json")):
        dia = path.stem
        if not (desde <= dia <= hasta):
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("auditoría ilegible, se omite: %s", path.name)
            continue
        for corrida in data.get("corridas", []):
            for c in corrida.get("candidatos", []):
                if isinstance(c, dict):
                    halladas.append((dia, c))
    return halladas


def _etapa2(emb: Embudo, snapshots: list[tuple[str, dict]], umbral_score: float) -> None:
    if not snapshots:
        emb.fuentes_vacias.append("auditoria")
        return
    por_ticker_dia: dict[tuple[str, str], list[dict]] = {}
    for dia, c in snapshots:
        por_ticker_dia.setdefault((dia, str(c.get("ticker", "?"))), []).append(c)

    penal = Counter()
    casi_score: Counter = Counter()
    for (dia, ticker), lecturas in sorted(por_ticker_dia.items()):
        emb.evaluadas_unicas += 1
        evs = [c.get("evaluacion") for c in lecturas if isinstance(c.get("evaluacion"), dict)]
        if not evs:
            continue
        con_patron = [e for e in evs if e.get("patron")]
        if con_patron:
            emb.con_patron_alguna_vez += 1
        tres = [e for e in evs
                if e.get("patron") and e.get("temprano") and e.get("riesgo_definido") is True]
        if tres:
            emb.con_tres_condiciones_alguna_vez += 1
        if any(e.get("accionable") for e in evs):
            emb.accionables_unicas += 1
        elif tres:
            scores = [e.get("score_ajustado") for e in tres if isinstance(e.get("score_ajustado"), (int, float))]
            if scores:
                mejor = max(scores)
                emb.casi.append({"dia": dia, "ticker": ticker, "score_max": round(mejor, 1)})
                for u in UMBRALES_SCORE:
                    if mejor >= u:
                        casi_score[f"{u:.0f}"] += 1
        # La penalización que se repite en la MEJOR lectura (mayor score)
        # de cada ticker-día: qué le faltó cuando más cerca estuvo.
        mejor_lectura = max(
            evs, key=lambda e: e.get("score_ajustado") if isinstance(e.get("score_ajustado"), (int, float)) else -1)
        for p in mejor_lectura.get("penalizaciones") or []:
            penal[str(p)[:80]] += 1
    emb.casi_pasarian_con_score = {k: casi_score.get(k, 0) for k in (f"{u:.0f}" for u in UMBRALES_SCORE)}
    emb.penalizaciones = dict(penal.most_common())


# --------------------------------------------------------------------------
# Watchlist: entradas creadas en el rango
# --------------------------------------------------------------------------

def _watchlist(emb: Embudo, entradas: list, desde: str, hasta: str) -> None:
    en_rango = [e for e in entradas if desde <= str(e.creado_en or "")[:10] <= hasta]
    if not en_rango:
        emb.fuentes_vacias.append("watchlist")
        return
    estados, bandas, inval = Counter(), Counter(), Counter()
    for e in en_rango:
        emb.watchlist_creadas += 1
        estados[str(e.estado)] += 1
        banda = "large" if e.es_large_cap is True else "small" if e.es_large_cap is False else "desconocida"
        bandas[banda] += 1
        transiciones = list(e.transiciones or [])
        if any(t.estado == watchlist.ESTADO_TRIGGERED for t in transiciones):
            emb.watchlist_dispararon += 1
        for t in transiciones:
            if t.estado == watchlist.ESTADO_MISSED:
                m = _RE_VELAS.search(str(t.motivo or ""))
                if m:
                    emb.missed_velas.append(int(m.group(1)))
            elif t.estado == watchlist.ESTADO_INVALIDATED:
                motivo = str(t.motivo or "")
                inval["precio bajo el stop" if "stop" in motivo else
                      "catalizador vencido" if "catalizador" in motivo else "otro"] += 1
    emb.watchlist_por_estado = dict(estados.most_common())
    emb.watchlist_por_banda = dict(bandas)
    emb.invalidated_motivos = dict(inval.most_common())


# --------------------------------------------------------------------------
# Ejecutor / IA: revisiones
# --------------------------------------------------------------------------

def _revisiones(emb: Embudo, revisiones: list[estado.RevisionIA], desde: str, hasta: str) -> None:
    en_rango = [r for r in revisiones if desde <= str(r.timestamp or "")[:10] <= hasta]
    if not en_rango:
        emb.fuentes_vacias.append("revisiones")
        return
    conf, motivos, aprob = Counter(), Counter(), Counter()
    for r in en_rango:
        emb.revisiones += 1
        if r.entro:
            emb.entraron += 1
        if r.motivo_no_operada:
            motivos[str(r.motivo_no_operada)] += 1
        # `ia_entraria is None` = no se consultó (p. ej. precio fuera de
        # alcance); no cuenta como veredicto ni como confianza.
        if r.ia_entraria is None:
            continue
        emb.ia_consultada += 1
        conf[str(int(r.confianza))] += 1
        for u in UMBRALES_IA:
            if int(r.confianza) >= u:
                aprob[str(u)] += 1
    emb.ia_confianza = {k: conf[k] for k in sorted(conf, key=int)}
    emb.ia_aprobarian_con_umbral = {str(u): aprob.get(str(u), 0) for u in UMBRALES_IA}
    emb.no_operadas_por_motivo = dict(motivos.most_common())


# --------------------------------------------------------------------------
# Paper y sombra: telemetrías
# --------------------------------------------------------------------------

def _paper(emb: Embudo, corridas: list[dict]) -> None:
    if not corridas:
        emb.fuentes_vacias.append("telemetria_paper")
        return
    emb.ticks_paper = len(corridas)
    emb.ordenes_colocadas = sum(int(c.get("ordenes_colocadas") or 0) for c in corridas)


def _cargar_movers(desde: str, hasta: str, dir_telemetria: Path) -> list[dict]:
    corridas: list[dict] = []
    if not dir_telemetria.exists():
        return corridas
    for path in sorted(dir_telemetria.glob(f"*/*/{ARCHIVO_MOVERS}")):
        dia = path.parent.parent.name
        if not (desde <= dia <= hasta):
            continue
        try:
            for linea in path.read_text(encoding="utf-8").splitlines():
                if linea.strip():
                    c = json.loads(linea)
                    if isinstance(c, dict):
                        corridas.append(c)
        except (json.JSONDecodeError, OSError):
            log.warning("telemetría de movers ilegible, se omite: %s", path)
    return corridas


def _sombra(emb: Embudo, corridas: list[dict]) -> None:
    if not corridas:
        emb.fuentes_vacias.append("sombra_movers")
        return
    rech = Counter()
    for c in corridas:
        emb.sombra_corridas += 1
        e = c.get("embudo") if isinstance(c.get("embudo"), dict) else {}
        emb.sombra_clase_a += int(e.get("clase_a") or 0)
        emb.sombra_clase_b += int(e.get("clase_b") or 0)
        for k, v in (c.get("rechazos") or {}).items():
            rech[k] += int(v)
    emb.sombra_rechazos = dict(rech.most_common())


# --------------------------------------------------------------------------
# Armado y formato
# --------------------------------------------------------------------------

def construir(
    desde: str, hasta: str, *,
    dir_telemetria_hunter: Path = telem_hunter.DIR_TELEMETRIA,
    dir_auditoria: Path = DIR_AUDITORIA,
    path_watchlist: Path = watchlist.PATH,
    path_revisiones: Path = estado.PATH,
    dir_telemetria_paper: Path = telem_paper.DIR_TELEMETRIA,
    umbral_score: float = CFG_HUNTER.score_minimo_alerta,
) -> Embudo:
    """Junta las seis fuentes para el rango [desde, hasta] (ISO, ambas
    inclusive). Cada fuente es independiente: una que falte deja su
    sección vacía y su nombre en `fuentes_vacias`."""
    emb = Embudo(desde=desde, hasta=hasta)
    _etapa1(emb, telem_hunter.cargar_dias(desde, hasta, dir_telemetria_hunter))
    _etapa2(emb, _cargar_auditoria(desde, hasta, dir_auditoria), umbral_score)
    _watchlist(emb, watchlist.cargar(path_watchlist) if path_watchlist.exists() else [], desde, hasta)
    _revisiones(emb, estado.cargar(path_revisiones) if path_revisiones.exists() else [], desde, hasta)
    _paper(emb, telem_paper.cargar_dias(desde, hasta, dir_telemetria_paper))
    _sombra(emb, _cargar_movers(desde, hasta, dir_telemetria_hunter))
    return emb


def _linea_contador(titulo: str, d: dict, n: int = 6) -> str:
    if not d:
        return f"  {titulo}: (sin datos)"
    partes = [f"{k} {v}" for k, v in list(d.items())[:n]]
    return f"  {titulo}: " + ", ".join(partes)


def formatear(emb: Embudo) -> str:
    l: list[str] = [f"EMBUDO {emb.desde} → {emb.hasta}"]
    l.append("")
    l.append("1) Escaneo (etapa 1, suma de corridas)")
    l.append(f"  corridas {emb.escaneos} · símbolos pedidos {emb.universo_escaneado}")
    for etapa in ("operables", "con_alguna_noticia", "con_catalizador", "evaluadas", "accionables"):
        por_banda = emb.etapa1_por_banda.get(etapa, {})
        detalle = ", ".join(f"{b} {n}" for b, n in por_banda.items()) if por_banda else "-"
        l.append(f"  {etapa:<20} {emb.etapa1.get(etapa, 0):>6}   ({detalle})")
    l.append(_linea_contador("rechazos de universo", emb.rechazos_universo))
    l.append(_linea_contador("rechazos de noticia", emb.keyword_rechazos))
    l.append(_linea_contador("condiciones que pasaron", emb.condiciones))
    l.append("")
    l.append("2) Evaluador (auditoría, ticker-días únicos)")
    l.append(f"  evaluadas {emb.evaluadas_unicas} · con patrón {emb.con_patron_alguna_vez} · "
             f"con patrón+temprano+riesgo {emb.con_tres_condiciones_alguna_vez} · "
             f"accionables {emb.accionables_unicas}")
    l.append(f"  casi (tres condiciones sí, score no): {len(emb.casi)}; pasarían con score "
             + ", ".join(f"≥{k}: {v}" for k, v in emb.casi_pasarian_con_score.items()))
    for c in emb.casi[:8]:
        l.append(f"    {c['dia']} {c['ticker']} score máx {c['score_max']}")
    l.append(_linea_contador("qué faltó en la mejor lectura", emb.penalizaciones, 5))
    l.append("")
    l.append("3) Watchlist (entradas creadas en el rango)")
    l.append(f"  creadas {emb.watchlist_creadas} · dispararon {emb.watchlist_dispararon}")
    l.append(_linea_contador("estado final", emb.watchlist_por_estado))
    l.append(_linea_contador("por banda", emb.watchlist_por_banda))
    if emb.missed_velas:
        v = sorted(emb.missed_velas)
        l.append(f"  MISSED: velas al marcar → mín {v[0]}, mediana {v[len(v) // 2]}, máx {v[-1]}")
    l.append(_linea_contador("INVALIDATED por", emb.invalidated_motivos))
    l.append("")
    l.append("4) Ejecutor e IA (revisiones en el rango)")
    l.append(f"  revisiones {emb.revisiones} · consultadas a la IA {emb.ia_consultada} · "
             f"entraron {emb.entraron}")
    l.append(_linea_contador("confianza", emb.ia_confianza, 10))
    l.append(_linea_contador("aprobarían con umbral (cota superior)", emb.ia_aprobarian_con_umbral))
    l.append(_linea_contador("no operadas por", emb.no_operadas_por_motivo))
    l.append(f"  ticks paper {emb.ticks_paper} · órdenes colocadas {emb.ordenes_colocadas}")
    l.append("")
    l.append("5) Sombra de movers (sin catalizador)")
    l.append(f"  corridas {emb.sombra_corridas} · clase A {emb.sombra_clase_a} · clase B {emb.sombra_clase_b}")
    l.append(_linea_contador("rechazos", emb.sombra_rechazos))
    if emb.fuentes_vacias:
        l.append("")
        l.append("Fuentes sin datos en el rango: " + ", ".join(emb.fuentes_vacias))
    return "\n".join(l)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reporte de embudo de punta a punta (solo lectura).")
    hoy = datetime.now(UTC).date()
    p.add_argument("--desde", default=(hoy - timedelta(days=6)).isoformat())
    p.add_argument("--hasta", default=hoy.isoformat())
    p.add_argument("--json", action="store_true", help="además del texto, el embudo como JSON")
    a = p.parse_args(argv)
    emb = construir(a.desde, a.hasta)
    print(formatear(emb))
    if a.json:
        print(json.dumps(asdict(emb), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
