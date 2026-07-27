"""Auditoría completa -- Principios 6, 7 y 9 del pedido de 2026-07-27:
"cada alerta debe poder reconstruirse meses después... ¿por qué
apareció? ¿qué datos existían exactamente en ese momento? ... cuando
descarte algo debe explicar exactamente qué lo descartó. No quiero
respuestas del tipo 'el score fue bajo'."

Cada corrida de la etapa 2 escribe un snapshot COMPLETO de CADA
candidato evaluado -- alertado, vetado por el abogado del diablo,
perdedor de la competencia relativa o descartado por el evaluador --
con los datos exactos que existían en ese momento (precio, volumen,
factores intradía, catalizador con titular y fuente, resultado de cada
pregunta del evaluador, objeciones) más la decisión final, sus motivos
en texto plano, y qué tendría que cambiar para que la decisión fuera
otra.

Un archivo JSON por día en `momentum_hunter/auditoria/` (el workflow lo
committea igual que `alertas_enviadas.json`) -- cada corrida del día se
APPENDEA, nunca se sobreescribe: la trazabilidad no se pierde porque el
bot corrió dos veces.

Solo persistencia -- ningún cálculo nuevo, ninguna llamada de red. Los
snapshots se arman con lo que el pipeline YA calculó."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter.alerts import CandidatoIntradia

DIR_AUDITORIA = Path(__file__).resolve().parent / "auditoria"

# Decisiones posibles -- claves estables para poder consultar la
# auditoría meses después sin adivinar strings.
DECISION_ALERTADA = "alertada"
DECISION_VETADA = "vetada_por_abogado_del_diablo"
DECISION_PERDIO_COMPETENCIA = "perdio_la_competencia_relativa"
DECISION_DESCARTADA = "descartada_por_evaluador"
DECISION_SIN_CONVICCION = "no_paso_la_ultima_pregunta"   # sobrevivió todo, pero con
                                                          # demasiadas dudas acumuladas
                                                          # para un "sí claro"


def snapshot_candidato(
    c: CandidatoIntradia, decision: str, motivos: list[str], que_cambiaria: list[str],
) -> dict:
    """Todo lo que existía sobre este candidato en el momento de la
    decisión -- suficiente para responder, meses después, cada pregunta
    del Principio 9 (por qué apareció, qué datos había, qué patrón, qué
    noticias, qué precio, qué volumen, qué esperaba el sistema). El "qué
    ocurrió realmente" lo agrega `outcomes.py` al tracker -- ambos
    archivos se cruzan por ticker+fecha."""
    r = c.resultado
    return {
        "ticker": c.ticker,
        "nombre": c.nombre,
        "precio_actual": c.factores.precio_actual,
        "factores_intradia": asdict(c.factores),
        "meta": {
            "float_acciones": c.meta.shares_float,
            "short_pct_float": c.meta.short_pct_float,
            "market_cap": c.meta.market_cap,
            "bolsa": c.meta.bolsa,
        },
        "catalizador": asdict(c.catalizador) if c.catalizador is not None else None,
        "minutos_desde_catalizador": c.minutos_desde_catalizador,
        "evaluacion": {
            "paso_detenido": r.paso_detenido,
            "dinero_entrando": r.dinero_entrando,
            "desequilibrio": r.desequilibrio,
            "patron": r.patron,
            "temprano": r.temprano,
            "early": asdict(r.early) if r.early is not None else None,
            "penalizaciones": list(r.penalizaciones),
            "score_base": r.score_base,
            "score_ajustado": r.score_ajustado,
            "accionable": r.accionable,
        },
        "decision": decision,
        "motivos": motivos,
        "que_tendria_que_cambiar": que_cambiaria,
    }


def registrar_corrida(
    snapshots: list[dict], dir_auditoria: Path = DIR_AUDITORIA, ahora: datetime | None = None,
) -> Path | None:
    """Appendea la corrida al archivo del día. Devuelve la ruta escrita,
    o None si no había nada que registrar (sin candidatos evaluados no
    hay decisión que auditar)."""
    if not snapshots:
        return None
    ahora = ahora or datetime.now(UTC)
    dir_auditoria.mkdir(parents=True, exist_ok=True)
    path = dir_auditoria / f"{ahora.date().isoformat()}.json"

    data: dict = {"corridas": []}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # Un archivo corrupto no debe tumbar la corrida ni borrar en
            # silencio: se renombra el corrupto y se empieza uno nuevo.
            path.rename(path.with_suffix(".corrupto.json"))
            data = {"corridas": []}

    data["corridas"].append({
        "timestamp": ahora.isoformat(timespec="seconds"),
        "candidatos_evaluados": len(snapshots),
        "candidatos": snapshots,
    })
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return path
