"""Reporte semanal -- qué pasó en la semana y qué conviene mejorar.

POR QUÉ (pedido 2026-08-24): "cada viernes cuando cierre el mercado
mandas un reporte de todo lo que pasó para ver en qué podemos mejorar,
tenemos que hacer este sistema escalable".

La diferencia con lo que ya existe: `heartbeat.py` avisa al cierre de
CADA día si no hubo nada; `diario.py` escribe una página por operación
resuelta. Ninguno responde "¿cómo va el sistema?". Este sí, y es la
pieza que lo vuelve escalable: sin una vista semanal de dónde mueren las
candidatas y qué se está rompiendo, cada mejora es una corazonada.

QUÉ RESPONDE, en este orden:

  1. ¿Corrió? Cuántas corridas, cuántos errores y de qué tipo -- el
     `except Exception` deliberado de todo el pipeline hace que un fallo
     sistemático se vea exactamente igual que "no había nada".
  2. ¿Qué encontró? El embudo de la semana, separado por banda.
  3. ¿Las small-caps mueren por falta de COBERTURA de noticias? La
     pregunta abierta del 2026-08-24, que no se pudo contestar entonces.
     Compara "tenía alguna noticia" contra "tenía catalizador" en cada
     banda: si la cobertura es baja en small-caps, el cuello de botella
     es la fuente de datos, no el criterio.
  4. ¿Qué condición está matando a las candidatas? Las cuatro
     obligatorias por separado.
  5. ¿Hubo operaciones? Y si no, qué faltó exactamente.
  6. Señales de alarma concretas, no genéricas -- cada una con el número
     que la dispara.

SOLO LEE. No decide, no opera, no toca ningún estado. Si algo falta o
está corrupto, lo dice en el reporte en vez de fallar: un reporte
incompleto sigue siendo útil, uno que no llega no."""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter import telemetria
from momentum_hunter.audit import DIR_AUDITORIA
from momentum_hunter.config import CONFIG

log = logging.getLogger("momentum_hunter.reporte_semanal")

PATH_REVISIONES = Path(__file__).resolve().parent.parent / "momentum_paper_trader" / "revisiones.json"


def rango_semana(hasta: datetime) -> tuple[str, str]:
    """(lunes, viernes) de la semana de `hasta`, en ISO. Se ancla al
    lunes aunque el reporte corra otro día, para que dos ejecuciones de
    la misma semana cubran exactamente el mismo periodo."""
    lunes = hasta.date() - timedelta(days=hasta.weekday())
    return lunes.isoformat(), hasta.date().isoformat()


def _pct(parte: int, total: int) -> str:
    return f"{parte / total * 100:.0f}%" if total else "n/d"


def _suma_banda(corridas: list[dict], clave: str, banda: str) -> int:
    return sum((c.get("embudo", {}).get(clave, {}) or {}).get(banda, 0) for c in corridas)


def _seccion_operacion(corridas: list[dict]) -> list[str]:
    errores: Counter = Counter()
    for c in corridas:
        for k, v in (c.get("errores") or {}).items():
            errores[k] += v
    total_err = sum(errores.values())

    lineas = [f"Corridas registradas: {len(corridas)}"]
    if not errores:
        lineas.append("Errores: ninguno.")
        return lineas
    lineas.append(f"Errores: {total_err} en total")
    for k, v in errores.most_common(5):
        origen, _, tipo = k.partition(":")
        lineas.append(f"  · {v}x {tipo} en {origen}")
    return lineas


def _seccion_embudo(corridas: list[dict]) -> list[str]:
    lineas = []
    for banda, etiqueta in (("small", "Small-caps"), ("large", "Large-caps")):
        op = _suma_banda(corridas, "operables", banda)
        if not op:
            continue
        noti = _suma_banda(corridas, "con_alguna_noticia", banda)
        cat = _suma_banda(corridas, "con_catalizador", banda)
        ev = _suma_banda(corridas, "evaluadas", banda)
        acc = _suma_banda(corridas, "accionables", banda)
        lineas += [
            f"{etiqueta}:",
            f"  operables: {op:,}",
            f"  con alguna noticia: {noti:,} ({_pct(noti, op)})",
            f"  con catalizador: {cat:,} ({_pct(cat, op)})",
            f"  evaluadas a fondo: {ev:,}   accionables: {acc:,}",
        ]
    return lineas or ["Sin datos de embudo esta semana."]


def _seccion_cobertura(corridas: list[dict]) -> list[str]:
    """La pregunta abierta del 2026-08-24, contestada con datos."""
    s_op = _suma_banda(corridas, "operables", "small")
    l_op = _suma_banda(corridas, "operables", "large")
    if not s_op or not l_op:
        return ["Todavía no hay suficientes acciones de ambas bandas para comparar cobertura."]

    s_pct = _suma_banda(corridas, "con_alguna_noticia", "small") / s_op * 100
    l_pct = _suma_banda(corridas, "con_alguna_noticia", "large") / l_op * 100
    lineas = [
        f"Cobertura de noticias -- small-caps: {s_pct:.0f}% | large-caps: {l_pct:.0f}%",
    ]
    if l_pct >= 2 * max(s_pct, 0.5):
        lineas.append(
            "Las small-caps tienen bastante menos cobertura. Como el catalizador es "
            "obligatorio, el cuello de botella sería la FUENTE DE DATOS, no el criterio "
            "del bot -- valdría la pena evaluar otra fuente de noticias.")
    else:
        lineas.append(
            "La cobertura es parecida en ambas bandas: la fuente de noticias NO parece "
            "ser lo que frena a las small-caps.")
    return lineas


def _seccion_condiciones(corridas: list[dict]) -> list[str]:
    tot = sum(sum((c.get("embudo", {}).get("evaluadas", {}) or {}).values()) for c in corridas)
    if not tot:
        return ["Ninguna candidata llegó a evaluación a fondo esta semana."]
    cond = Counter()
    for c in corridas:
        for k, v in (c.get("condiciones") or {}).items():
            cond[k] += v
    nombres = {
        "patron": "tenía un patrón claro",
        "temprano": "llegamos a tiempo",
        "riesgo_definido": "riesgo/recompensa definido",
        "dinero_entrando": "había dinero entrando",
        "sobre_umbral": f"superó el umbral ({CONFIG.score_minimo_alerta:.0f})",
    }
    lineas = [f"De {tot:,} candidatas evaluadas a fondo:"]
    for k, etiqueta in nombres.items():
        lineas.append(f"  {etiqueta}: {cond[k]:,} ({_pct(cond[k], tot)})")
    peor = min(nombres, key=lambda k: cond[k])
    lineas.append(f"La que más descarta: «{nombres[peor]}».")
    return lineas


def _seccion_operaciones(desde: str, hasta: str) -> list[str]:
    if not PATH_REVISIONES.exists():
        return ["Operaciones: ninguna todavía (el archivo de revisiones aún no existe)."]
    try:
        data = json.loads(PATH_REVISIONES.read_text())
    except (json.JSONDecodeError, OSError):
        return ["Operaciones: no se pudo leer el archivo de revisiones."]
    revs = [r for r in data.get("revisiones", [])
            if isinstance(r, dict) and desde <= str(r.get("timestamp", ""))[:10] <= hasta]
    if not revs:
        return ["Operaciones esta semana: ninguna."]

    entraron = [r for r in revs if r.get("entro")]
    lineas = [f"La IA revisó {len(revs)} señal(es); entró en {len(entraron)}."]
    pnl = [r["pnl"] for r in revs if isinstance(r.get("pnl"), (int, float))]
    if pnl:
        total = sum(pnl)
        ganadoras = sum(1 for p in pnl if p > 0)
        lineas.append(
            f"Cerradas: {len(pnl)} | ganadoras: {ganadoras} | "
            f"resultado: {'+' if total >= 0 else '-'}${abs(total):,.2f}")
    for r in revs[-3:]:
        marca = "entró" if r.get("entro") else "no entró"
        lineas.append(f"  · {r.get('ticker', '?')} — {marca}: {str(r.get('razonamiento', ''))[:90]}")
    return lineas


def _seccion_alarmas(corridas: list[dict]) -> list[str]:
    """Señales concretas, cada una con el número que la dispara. Nada de
    consejos genéricos: si no hay nada que reportar, se dice."""
    alarmas = []

    score_max = max((c.get("score_maximo", 0) or 0 for c in corridas), default=0)
    if corridas and score_max < CONFIG.score_minimo_alerta:
        alarmas.append(
            f"⚠️ Ninguna candidata llegó al umbral en toda la semana (máximo {score_max:.1f} "
            f"contra {CONFIG.score_minimo_alerta:.0f}). Si se repite, el umbral está por "
            f"encima de lo alcanzable — el mismo error que ya costó semanas.")

    errores = sum(sum((c.get("errores") or {}).values()) for c in corridas)
    if corridas and errores > len(corridas) * 20:
        alarmas.append(
            f"⚠️ {errores} errores en {len(corridas)} corridas. Suficientes para que "
            f"«no había oportunidades» pueda ser en realidad «la fuente falló».")

    evaluadas = sum(sum((c.get("embudo", {}).get("evaluadas", {}) or {}).values()) for c in corridas)
    if corridas and evaluadas == 0:
        alarmas.append(
            "⚠️ Ninguna candidata llegó a evaluación a fondo. El embudo se corta antes: "
            "revisar catalizadores y filtros de la etapa 1.")

    return alarmas or ["Sin señales de alarma esta semana."]


def construir(desde: str, hasta: str, dir_telemetria: Path = telemetria.DIR_TELEMETRIA) -> str:
    corridas = telemetria.cargar_dias(desde, hasta, dir_telemetria)
    bloques = [
        f"📊 REPORTE SEMANAL — {desde} a {hasta}",
        "",
        "── CÓMO CORRIÓ ──",
        *_seccion_operacion(corridas),
        "",
        "── QUÉ ENCONTRÓ ──",
        *_seccion_embudo(corridas),
        "",
        "── COBERTURA DE NOTICIAS ──",
        *_seccion_cobertura(corridas),
        "",
        "── DÓNDE MUEREN LAS CANDIDATAS ──",
        *_seccion_condiciones(corridas),
        "",
        "── OPERACIONES ──",
        *_seccion_operaciones(desde, hasta),
        "",
        "── QUÉ CONVIENE REVISAR ──",
        *_seccion_alarmas(corridas),
        "",
        "Cuenta de práctica — ningún dinero real se movió.",
    ]
    return "\n".join(bloques)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="imprime el reporte, no lo manda")
    ap.add_argument("--hasta", help="fecha final ISO (por defecto, hoy)")
    args = ap.parse_args()

    hasta = (datetime.fromisoformat(args.hasta).replace(tzinfo=UTC)
             if args.hasta else datetime.now(UTC))
    desde_iso, hasta_iso = rango_semana(hasta)
    texto = construir(desde_iso, hasta_iso)
    print(texto)

    if not args.dry_run:
        from momentum_hunter.run import enviar_telegram
        enviar_telegram(texto)


if __name__ == "__main__":
    main()
