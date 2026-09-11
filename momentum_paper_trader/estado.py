"""Persistencia de qué entradas TRIGGERED ya pasaron por la revisión de la
IA -- mismo principio que el resto del repo (JSON chico, committeado por el
workflow, ver `momentum_hunter/heartbeat.py`/`tracker.py`).

Antes de la capa de decisión con IA (ver `ia_decision.py`), esto solo
registraba órdenes colocadas -- ahora registra CADA revisión, haya
terminado en orden o no. Es la diferencia importante: si no se registrara
también el "no" de la IA, cada corrida volvería a preguntarle lo mismo
sobre la misma entrada TRIGGERED (que queda en `watchlist.json` varios
días, ver `watchlist.RETENCION_DIAS_TERMINALES`), gastando llamadas a la
API y -- peor -- dándole otra oportunidad de decir "sí" por puro azar de
muestreo del modelo hasta que acierte que sí. Una entrada TRIGGERED se
revisa UNA vez, con cualquier resultado, y se queda así.

La clave es (ticker, `creado_en`) -- `creado_en` identifica la entrada
ÚNICA de la watchlist, no solo el ticker, así que el mismo ticker
disparando en dos días distintos genera dos revisiones distintas
correctamente."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

PATH = Path(__file__).resolve().parent / "revisiones.json"


@dataclass
class RevisionIA:
    ticker: str
    creado_en: str   # `EntradaWatchlist.creado_en` -- identifica la entrada única
    entro: bool       # veredicto final de la IA (ya con el "cinturón y tirantes" aplicado)
    confianza: int
    razonamiento: str
    timestamp: str
    # Solo se llenan si `entro=True` -- una revisión que resultó en "no
    # entrar" nunca tuvo una orden real que registrar.
    order_id: str | None = None
    cantidad: int | None = None
    precio_entrada: float | None = None
    stop: float | None = None
    objetivo: float | None = None
    # -- Ciclo de vida del trade (ver `seguimiento.py`): un cambio de
    # `resultado` a fill/cierre se avisa por Telegram una vez. El resto
    # (p. ej. `no_ejecutada`) se persiste en silencio. Persistencia = 
    # anti-duplicado, igual que `alertas_enviadas.json` en
    # momentum_hunter. `None` = sin novedades todavía (o sin orden).
    # Campos opcionales con default para que los registros viejos sigan
    # cargando sin migración.
    resultado: str | None = None   # "abierta" | "objetivo" | "stop" | "cerrada" | "no_ejecutada"
    pnl: float | None = None       # ganancia/pérdida realizada en dólares (solo al cerrar)
    # -- Cadena de latencia e2e (solo instrumentación, 2026-09-11).
    # Copia los relojes que ya existen en la watchlist y agrega los
    # hops que solo este módulo ve. Todo opcional: un revisiones.json
    # viejo sigue cargando, y un campo ausente no se inventa. No
    # decide ni cambia el sizing.
    #   market_event_ts      -- vela que confirmó (dato, no reloj)
    #   watchlist_escrito_ts -- primera persistencia TRIGGERED
    #   executor_leido_ts    -- cuándo este proceso leyó esa entrada
    #   ia_decision_ts       -- cuándo devolvió ia_decision.decidir
    #   timestamp            -- orden colocada O rechazo persistido
    #   order_id             -- id paper si hubo orden; None si rechazo
    market_event_ts: str | None = None
    watchlist_escrito_ts: str | None = None
    executor_leido_ts: str | None = None
    ia_decision_ts: str | None = None
    latencia_descubrimiento_ms: float | None = None  # market_event → watchlist write
    latencia_e2e_ms: float | None = None             # market_event → timestamp


# `resultado` que ya no puede cambiar -- `seguimiento.revisar` no vuelve a
# consultar Alpaca por estas (la historia del trade ya terminó).
RESULTADOS_TERMINALES = frozenset({"objetivo", "stop", "cerrada", "no_ejecutada"})


def _clave(ticker: str, creado_en: str) -> str:
    return f"{ticker}|{creado_en}"


def cargar(path: Path = PATH) -> list[RevisionIA]:
    """Un archivo corrupto no debe tumbar la corrida -- se ignora y se
    reinicia vacío (mismo principio que `momentum_hunter.watchlist.cargar`)."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    revisiones: list[RevisionIA] = []
    for d in data.get("revisiones", []):
        try:
            revisiones.append(RevisionIA(**d))
        except TypeError:
            continue
    return revisiones


def guardar(revisiones: list[RevisionIA], path: Path = PATH) -> None:
    data = {"revisiones": [asdict(r) for r in revisiones]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def ya_revisada(revisiones: list[RevisionIA], ticker: str, creado_en: str) -> bool:
    clave = _clave(ticker, creado_en)
    return any(_clave(r.ticker, r.creado_en) == clave for r in revisiones)
