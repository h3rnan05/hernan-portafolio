"""Archivo de entradas TRIGGERED cuyo desenlace paper ya es terminal.

POR QUÉ EXISTE. El State Engine del buscador trata TRIGGERED como
terminal de VIGILANCIA: no se re-evalúa, no se re-dispara. Eso está
bien para no reinventar la señal. No es un terminal del CICLO paper.
NTLA (2026-09-08, fill + stop, pnl −6.38) y BEAM (2026-09-10, rechazo
de la IA) se quedaron `estado: triggered` indefinidamente: el paper
trader escribía `revisiones.json` y nunca devolvía un estado a la
watchlist. Clasificación: (C) faltaba la transición post-revisión --
no un fallo de salida (A) ni de polling (B). Evidencia en el cuerpo
del PR y en `marcar_archivada`.

QUÉ HACE. Si una entrada sigue TRIGGERED y hay una revisión paper
terminal para esa identidad (`ticker` + `creado_en`), la pasa a
ARCHIVED (transición en la entrada, no se borra) y appendea una
línea a `archivo_triggered.jsonl`. Ese JSONL no se purga a los 7
días: es el rastro que sobrevive cuando `purgar_antiguas` limpia la
watchlist.

QUÉ NO HACE. No toca umbrales, stops, ATR, corte de confianza de la
IA, ni el endpoint paper. No archiva WATCHING/MISSED/EXPIRED/
INVALIDATED. No archiva un TRIGGERED sin revisión, ni uno cuya
orden paper sigue abierta (`entro=True` y `resultado` no terminal).
Ante un archivo ilegible, no inventa el dato que falta: omite.

Quién escribe `watchlist.json`: esta función puede mutarla. Es la
única escritura del paper trader sobre la watchlist -- nunca inventa
una oportunidad ni cambia un precio. El buscador sigue siendo el
dueño de discovery; el VPS no git-add de `watchlist.json` (conflicto
histórico), por eso el hunter también llama esto antes de persistir:
si el archivo quedó solo en el JSONL, la próxima corrida del
buscador aplica la transición y GHA la commitea.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter import watchlist

from momentum_paper_trader import estado

log = logging.getLogger("momentum_paper_trader.archivo")

PATH_LOG = Path(__file__).resolve().parent / "archivo_triggered.jsonl"

# Hipótesis verificada con NTLA/BEAM: no había transición terminal
# después de la revisión paper. A/B se descartan en el PR.
CAUSA_TRANSICION_AUSENTE = "C"
CAUSA_DESCONOCIDA = "desconocida"
CAUSAS_VALIDAS = frozenset({"A", "B", "C", "D", CAUSA_DESCONOCIDA})

CAMPOS_AUDITORIA = (
    "ticker",
    "creado_en",
    "estado_anterior",
    "estado_nuevo",
    "archivado_en",
    "revision_timestamp",
    "revision_entro",
    "revision_resultado",
    "revision_order_id",
    "desenlace_paper",
    "causa_raiz",
    "causa_raiz_detalle",
)


def _ahora_iso(ahora: datetime) -> str:
    return ahora.isoformat(timespec="seconds")


def desenlace_paper(r: estado.RevisionIA) -> str | None:
    """Desenlace terminal de la revisión, o None si el ciclo paper
    todavía no cerró. None no se inventa: una orden viva no es un
    archivo."""
    if not r.entro:
        return "rechazo_ia"
    if r.resultado in estado.RESULTADOS_TERMINALES:
        return r.resultado
    return None


def revision_es_terminal(r: estado.RevisionIA) -> bool:
    return desenlace_paper(r) is not None


def _clave(ticker: str, creado_en: str) -> str:
    return f"{ticker}|{creado_en}"


def _leer_log(path: Path) -> list[dict]:
    """Append-only: una línea ilegible no tumba el resto ni se
    reescribe el archivo."""
    if not path.exists():
        return []
    try:
        texto = path.read_text()
    except OSError:
        return []
    registros: list[dict] = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            registros.append(obj)
    return registros


def _ya_en_log(registros: list[dict], ticker: str, creado_en: str) -> bool:
    clave = _clave(ticker, creado_en)
    return any(_clave(str(r.get("ticker", "")), str(r.get("creado_en", ""))) == clave
               for r in registros)


def registro_auditoria(
    e: watchlist.EntradaWatchlist, r: estado.RevisionIA, ahora: datetime,
    causa_raiz: str = CAUSA_TRANSICION_AUSENTE,
) -> dict:
    """Los campos que el dueño pidió que no desaparezcan. Si la causa
    no es una de las clasificadas, se guarda como desconocida -- no se
    fuerza un veredicto."""
    if causa_raiz not in CAUSAS_VALIDAS:
        causa_raiz = CAUSA_DESCONOCIDA
    desenlace = desenlace_paper(r) or "desconocido"
    if causa_raiz == CAUSA_TRANSICION_AUSENTE:
        detalle = (
            "C: la watchlist no tenía transición terminal después de la "
            "revisión paper (fill/cierre/rechazo). TRIGGERED era terminal "
            "solo para el buscador."
        )
    else:
        detalle = "causa desconocida -- ver investigación"
    return {
        "ticker": e.ticker,
        "creado_en": e.creado_en,
        "estado_anterior": watchlist.ESTADO_TRIGGERED,
        "estado_nuevo": watchlist.ESTADO_ARCHIVED,
        "archivado_en": _ahora_iso(ahora),
        "revision_timestamp": r.timestamp,
        "revision_entro": r.entro,
        "revision_resultado": r.resultado,
        "revision_order_id": r.order_id,
        "desenlace_paper": desenlace,
        "causa_raiz": causa_raiz,
        "causa_raiz_detalle": detalle,
    }


def _appender_log(path: Path, registro: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _motivo_transicion(registro: dict) -> str:
    return (
        f"Desenlace paper terminal ({registro['desenlace_paper']}). "
        f"Causa raíz {registro['causa_raiz']}: no había transición "
        f"post-revisión. Revisión {registro['revision_timestamp']}."
    )


def archivar_revisadas(
    entradas: list[watchlist.EntradaWatchlist] | None = None,
    revisiones: list[estado.RevisionIA] | None = None,
    ahora: datetime | None = None,
    path_watchlist: Path | None = None,
    path_revisiones: Path | None = None,
    path_log: Path | None = None,
    persistir_watchlist: bool = True,
    causa_raiz: str = CAUSA_TRANSICION_AUSENTE,
) -> list[dict]:
    """Aplica ARCHIVED a cada TRIGGERED con revisión paper terminal.

    Devuelve los registros de auditoría escritos (o que se habrían
    escrito: si el JSONL ya tenía la línea, igual se transiciona la
    entrada y no se duplica el log). Vacío si no había nada que
    archivar -- un campo ausente no es evidencia de que haya que
    archivar."""
    ahora = ahora or datetime.now(UTC)
    path_log = path_log or PATH_LOG
    if entradas is None:
        entradas = watchlist.cargar(path_watchlist) if path_watchlist is not None else watchlist.cargar()
    if revisiones is None:
        revisiones = estado.cargar(path_revisiones) if path_revisiones is not None else estado.cargar()

    por_clave = {_clave(r.ticker, r.creado_en): r for r in revisiones}
    ya_logged = _leer_log(path_log)
    escritos: list[dict] = []

    for e in entradas:
        if e.estado != watchlist.ESTADO_TRIGGERED:
            continue
        r = por_clave.get(_clave(e.ticker, e.creado_en))
        if r is None or not revision_es_terminal(r):
            continue
        registro = registro_auditoria(e, r, ahora, causa_raiz=causa_raiz)
        if not watchlist.marcar_archivada(e, _motivo_transicion(registro), ahora):
            continue
        if not _ya_en_log(ya_logged, e.ticker, e.creado_en):
            _appender_log(path_log, registro)
            ya_logged.append(registro)
        escritos.append(registro)
        log.info(
            "%s: TRIGGERED -> ARCHIVED (desenlace=%s, causa=%s)",
            e.ticker, registro["desenlace_paper"], registro["causa_raiz"],
        )

    if persistir_watchlist and escritos:
        if path_watchlist is not None:
            watchlist.guardar(entradas, path_watchlist, ahora=ahora)
        else:
            watchlist.guardar(entradas, ahora=ahora)
    return escritos
