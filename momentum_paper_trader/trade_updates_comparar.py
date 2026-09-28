"""Compara la sombra de `trade_updates` con lo que vio el polling REST.

No coloca órdenes y no lee el stream para decidir. La autoridad sigue
siendo el REST: `seguimiento.py`, `cierre.py` (incluido `_esperar_patas`)
y `reconciliacion.py`. Este módulo solo informa diferencias.

Fuentes, todas opcionales salvo que sin la sombra o sin ninguna fuente
REST no hay comparación (no se declara "cero discrepancias"):

  - JSONL de la sombra (eventos `trade_update`, más huecos)
  - estado por orden que escribe el mismo proceso
  - `revisiones.json` (lo que seguimiento ya persistió: `resultado`,
    `order_id`, `cierre_order_id`)
  - `events.jsonl` del panel, tipos `orden` y `decision`
  - `GET /v2/orders?status=all` del día, de un archivo o pedido en el
    momento (`--pedir-rest`)

`reconciliacion.py` no deja un diario: mira posiciones y órdenes y
avisa. Acá se usa el mismo `revisiones.json` que ella lee y, si se
pide, el listado de órdenes. No se llama a `detectar` (mandaría
Telegram).

Latencia: `latencia_ms = t_rest - t_stream`. Positivo quiere decir que
el polling anotó el hecho después de que la sombra recibió el frame.
El reloj REST es el `ts` del evento `orden` en `events.jsonl`. El
`timestamp` de una revisión es la hora en que se persistió la
colocación, y solo se usa para un alta (`new` / `pending_new` /
`accepted`) cuando no hay evento `orden`. No es la hora a la que
seguimiento vio un fill: si esa hora no está guardada, `latencia_ms`
queda null. Un null no es cero.

Alpaca filtra `after`/`until` por `submitted_at`. `--pedir-rest` pide
desde 7 días antes del día de sesión para que un bracket de ayer que
se llenó hoy siga en el listado. El tope de Alpaca es 500; si la
página viene llena no se afirma que una orden de la sombra "no está
en REST": la ausencia sería la página, no una evidencia.

USO
  python -m momentum_paper_trader.trade_updates_comparar --dia 2026-09-28
  python -m momentum_paper_trader.trade_updates_comparar --dia 2026-09-28 --pedir-rest
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum_paper_trader.alpaca_client import ordenes_con_patas
from momentum_paper_trader.estado import PATH as PATH_REVISIONES

log = logging.getLogger("momentum_paper_trader.trade_updates_comparar")

_NY = ZoneInfo("America/New_York")
_RE_DIA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# `after`/`until` filtran por submitted_at. Una semana alcanza para el
# bracket que se colocó ayer y se llenó en la sesión que se compara.
DIAS_LOOKBACK_REST = 7
_EVENTOS_DE_ALTA = frozenset({"new", "pending_new", "accepted"})
_EVENTOS_FILL = frozenset({"fill", "partial_fill"})
_RESULTADO_CON_FILL = frozenset({"abierta", "objetivo", "stop", "cerrada"})
_STATUS_MUERTA_SIN_FILL = frozenset({
    "canceled", "expired", "rejected", "suspended",
})
_FUENTES = ("sombra", "estado", "revisiones", "eventos", "ordenes_rest")


@dataclass
class Informe:
    dia: str
    fuentes: dict
    n_eventos_sombra: int | None
    eventos_ausentes: list = field(default_factory=list)
    estados_distintos: list = field(default_factory=list)
    datos_distintos: list = field(default_factory=list)
    datos_ausentes: list = field(default_factory=list)
    latencias: list = field(default_factory=list)
    huecos: list = field(default_factory=list)
    sin_reloj: list = field(default_factory=list)
    lineas_ilegibles: int = 0
    ordenes_truncadas: bool = False


def codigo_salida(inf: Informe) -> int:
    """2 si no se pudo comparar. 1 si hay discrepancias. 0 si las
    fuentes que había coinciden. Una latencia null no es un fallo:
    es un reloj que no existe, y el informe lo trae igual."""
    if inf.fuentes.get("sombra") in ("ausente", "ilegible", None):
        return 2
    if all(inf.fuentes.get(k) in ("ausente", "ilegible", None) for k in ("revisiones", "eventos", "ordenes_rest")):
        return 2
    if inf.eventos_ausentes or inf.estados_distintos or inf.datos_distintos or inf.datos_ausentes:
        return 1
    return 0


def _parse(iso) -> datetime | None:
    if not isinstance(iso, str) or not iso.strip():
        return None
    texto = iso.strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def _fecha_ny(iso) -> str | None:
    dt = _parse(iso)
    if dt is None:
        return None
    return dt.astimezone(_NY).date().isoformat()


def delta_ms(inicio, fin) -> float | None:
    """`fin - inicio` en ms. None si falta un extremo o no tiene zona.
    Un reloj ausente no se cuenta como cero."""
    a, b = _parse(inicio), _parse(fin)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000.0, 1)


def _cantidad(v) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return format(Decimal(str(v)), "f")
    return None


def _status(v) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if not s:
        return None
    if s == "cancelled":
        return "canceled"
    return s


def _mismo_numero(a: str, b: str) -> bool:
    try:
        return Decimal(a) == Decimal(b)
    except (InvalidOperation, ValueError):
        return a.strip() == b.strip()


def limites_ny(dia: str) -> tuple[datetime, datetime]:
    if not isinstance(dia, str) or not _RE_DIA.fullmatch(dia):
        raise ValueError("dia")
    try:
        inicio = datetime.strptime(dia, "%Y-%m-%d").replace(tzinfo=_NY)
    except ValueError as ex:
        raise ValueError("dia") from ex
    return inicio, inicio + timedelta(days=1)


def fuentes_de(
    *,
    sombra,
    estado_ordenes,
    revisiones,
    eventos,
    ordenes_rest,
    ilegibles: frozenset[str] = frozenset(),
) -> dict:
    datos = {
        "sombra": sombra,
        "estado": estado_ordenes,
        "revisiones": revisiones,
        "eventos": eventos,
        "ordenes_rest": ordenes_rest,
    }
    out = {}
    for nombre in _FUENTES:
        if nombre in ilegibles:
            out[nombre] = "ilegible"
        elif datos[nombre] is None:
            out[nombre] = "ausente"
        elif datos[nombre] == [] or datos[nombre] == {}:
            out[nombre] = "vacia"
        else:
            out[nombre] = "presente"
    return out


def _min_iso(cadenas) -> str | None:
    mejor = None
    mejor_iso = None
    for c in cadenas:
        dt = _parse(c)
        if dt is None:
            continue
        if mejor is None or dt < mejor:
            mejor = dt
            mejor_iso = c
    return mejor_iso


def _reducir(eventos: list[dict]) -> dict:
    """Último evento, pero un campo null no borra qty/precio vistos
    antes. Igual que el archivo de estado del listener."""
    acc: dict = {}
    for ev in eventos:
        for k in ("qty", "filled_qty", "event_qty", "price", "symbol", "client_order_id", "execution_id"):
            if ev.get(k) is not None:
                acc[k] = ev[k]
        if ev.get("status") is not None:
            acc["status"] = ev["status"]
        if ev.get("event") is not None:
            acc["event"] = ev["event"]
        if ev.get("order_id") is not None:
            acc["order_id"] = ev["order_id"]
        if ev.get("recibido_en") is not None:
            acc["recibido_en"] = ev["recibido_en"]
        if ev.get("timestamp") is not None:
            acc["timestamp"] = ev["timestamp"]
    return acc


def _indexar_rest(ordenes: list) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for o in ordenes_con_patas(ordenes):
        oid = o.get("id")
        if not isinstance(oid, str) or not oid.strip():
            continue
        oid = oid.strip()
        if oid not in idx:
            idx[oid] = o
            continue
        # Una copia posterior sin status no pisa la que sí lo traía.
        if _status(o.get("status")) is None:
            continue
        idx[oid] = o
    return idx


def _en_dia_orden(o: dict, dia: str) -> bool | None:
    """True si algún reloj del broker cae en el día. False si todos los
    que parsean caen en otro. None si no hay ni un reloj usable: no es
    'fuera del día'."""
    vio = False
    for clave in (
        "submitted_at", "created_at", "filled_at", "canceled_at",
        "updated_at", "expired_at", "replaced_at",
    ):
        valor = o.get(clave)
        if not isinstance(valor, str) or not valor.strip():
            continue
        fecha = _fecha_ny(valor)
        if fecha is None:
            continue
        vio = True
        if fecha == dia:
            return True
    if not vio:
        return None
    return False


def _clasificar_sombra(sombra: list[dict], dia: str):
    en_dia: list[dict] = []
    sin_reloj: list[dict] = []
    huecos: list[dict] = []
    for ev in sombra:
        if not isinstance(ev, dict):
            continue
        tipo = ev.get("tipo")
        if tipo in ("hueco", "hueco_cerrado"):
            fecha = _fecha_ny(ev.get("recibido_en"))
            if fecha == dia:
                huecos.append(ev)
            elif fecha is None:
                sin_reloj.append({"fuente": "sombra", "tipo": tipo})
            continue
        if tipo != "trade_update":
            continue
        ts = ev.get("timestamp")
        if isinstance(ts, str) and ts.strip():
            fecha = _fecha_ny(ts)
            if fecha is None:
                sin_reloj.append({"fuente": "sombra", "order_id": ev.get("order_id"), "event": ev.get("event")})
            elif fecha == dia:
                en_dia.append(ev)
            continue
        rec = ev.get("recibido_en")
        if isinstance(rec, str) and rec.strip():
            fecha = _fecha_ny(rec)
            if fecha is None:
                sin_reloj.append({"fuente": "sombra", "order_id": ev.get("order_id"), "event": ev.get("event")})
            elif fecha == dia:
                en_dia.append(ev)
            continue
        sin_reloj.append({"fuente": "sombra", "order_id": ev.get("order_id"), "event": ev.get("event")})
    return en_dia, sin_reloj, huecos


def _resultado_contradice(resultado, reducido: dict) -> str | None:
    """None si no hay contradicción o si el resultado todavía no dice
    nada. Un `resultado` vacío no es 'abierta' ni 'cancelada'."""
    if not isinstance(resultado, str) or not resultado.strip():
        return None
    resultado = resultado.strip()
    status = _status(reducido.get("status"))
    event = reducido.get("event") if isinstance(reducido.get("event"), str) else None
    if resultado in _RESULTADO_CON_FILL and status in _STATUS_MUERTA_SIN_FILL:
        return "seguimiento_con_fill_y_stream_muerto"
    if resultado == "no_ejecutada" and (
        status in ("filled", "partially_filled") or event in _EVENTOS_FILL
    ):
        return "seguimiento_sin_ejecutar_y_stream_con_fill"
    return None


def _comparar_campo(campo: str, sombra_v, rest_v, order_id, symbol, distintos: list, ausentes: list) -> None:
    if sombra_v is None and rest_v is None:
        return
    if sombra_v is None or rest_v is None:
        ausentes.append({
            "order_id": order_id,
            "symbol": symbol,
            "campo": campo,
            "sombra": sombra_v,
            "rest": rest_v,
        })
        return
    if campo == "symbol":
        igual = sombra_v == rest_v
    else:
        igual = _mismo_numero(sombra_v, rest_v)
    if not igual:
        distintos.append({
            "order_id": order_id,
            "symbol": symbol,
            "campo": campo,
            "sombra": sombra_v,
            "rest": rest_v,
        })


def comparar(
    dia: str,
    *,
    sombra: list[dict] | None = None,
    estado_ordenes: dict | None = None,
    revisiones: list[dict] | None = None,
    eventos: list[dict] | None = None,
    ordenes_rest: list | None = None,
    ilegibles: frozenset[str] = frozenset(),
    lineas_ilegibles: int = 0,
    ordenes_truncadas: bool = False,
) -> Informe:
    """`None` en una fuente es 'no se leyó'. Una lista vacía es 'se leyó
    y no había nada'. No son lo mismo, y la vacía no se inventa cuando
    el archivo falta o no parsea."""
    limites_ny(dia)
    if "sombra" in ilegibles:
        sombra = None
    if "estado" in ilegibles:
        estado_ordenes = None
    if "revisiones" in ilegibles:
        revisiones = None
    if "eventos" in ilegibles:
        eventos = None
    if "ordenes_rest" in ilegibles:
        ordenes_rest = None
    fuentes = fuentes_de(
        sombra=sombra,
        estado_ordenes=estado_ordenes,
        revisiones=revisiones,
        eventos=eventos,
        ordenes_rest=ordenes_rest,
        ilegibles=ilegibles,
    )
    if sombra is None:
        return Informe(
            dia=dia, fuentes=fuentes, n_eventos_sombra=None,
            lineas_ilegibles=lineas_ilegibles, ordenes_truncadas=ordenes_truncadas,
        )

    en_dia, sin_reloj, huecos = _clasificar_sombra(sombra, dia)
    por_id: dict[str, list[dict]] = {}
    ids_sin_reloj: set[str] = set()
    for item in sin_reloj:
        oid = item.get("order_id")
        if isinstance(oid, str) and oid:
            ids_sin_reloj.add(oid)
    eventos_ausentes: list[dict] = []
    for ev in en_dia:
        oid = ev.get("order_id")
        if not isinstance(oid, str) or not oid:
            eventos_ausentes.append({
                "lado": "sombra",
                "motivo": "evento_sin_order_id",
                "order_id": None,
                "symbol": ev.get("symbol"),
                "event": ev.get("event"),
            })
            continue
        por_id.setdefault(oid, []).append(ev)
    reducidos = {oid: _reducir(evs) for oid, evs in por_id.items()}

    rest_idx: dict[str, dict] = {}
    rest_en_dia: dict[str, dict] = {}
    if ordenes_rest is not None:
        rest_idx = _indexar_rest(ordenes_rest)
        for oid, o in rest_idx.items():
            marca = _en_dia_orden(o, dia)
            if marca is None:
                sin_reloj.append({"fuente": "ordenes_rest", "order_id": oid, "symbol": o.get("symbol")})
            elif marca:
                rest_en_dia[oid] = o

    ids_revision: set[str] = set()
    revisiones_dia: list[dict] = []
    if revisiones is not None:
        for rev in revisiones:
            if not isinstance(rev, dict):
                continue
            for clave in ("order_id", "cierre_order_id"):
                oid = rev.get(clave)
                if isinstance(oid, str) and oid.strip():
                    ids_revision.add(oid.strip())
            ts = rev.get("timestamp")
            if not isinstance(ts, str) or not ts.strip():
                if rev.get("order_id"):
                    sin_reloj.append({
                        "fuente": "revisiones", "order_id": rev.get("order_id"), "ticker": rev.get("ticker"),
                    })
                continue
            fecha = _fecha_ny(ts)
            if fecha is None:
                sin_reloj.append({
                    "fuente": "revisiones", "order_id": rev.get("order_id"), "ticker": rev.get("ticker"),
                })
            elif fecha == dia:
                revisiones_dia.append(rev)

    ordenes_panel: list[dict] = []
    decisiones: list[dict] = []
    if eventos is not None:
        for ev in eventos:
            if not isinstance(ev, dict):
                continue
            if ev.get("tipo") not in ("orden", "decision"):
                continue
            fecha = _fecha_ny(ev.get("ts")) if isinstance(ev.get("ts"), str) else None
            if fecha is None:
                sin_reloj.append({
                    "fuente": "eventos", "tipo": ev.get("tipo"), "order_id": ev.get("order_id"),
                })
                continue
            if fecha != dia:
                continue
            if ev.get("tipo") == "orden":
                ordenes_panel.append(ev)
            else:
                decisiones.append(ev)

    # --- eventos que una fuente vio y la otra no ---
    for oid, o in rest_en_dia.items():
        if oid not in por_id and oid not in ids_sin_reloj:
            eventos_ausentes.append({
                "lado": "sombra",
                "motivo": "orden_rest_sin_evento_en_el_stream",
                "order_id": oid,
                "symbol": o.get("symbol") or o.get("_symbol"),
                "status_rest": _status(o.get("status")),
            })

    if ordenes_rest is not None and not ordenes_truncadas:
        for oid, red in reducidos.items():
            if oid in rest_idx or oid in ids_revision:
                continue
            eventos_ausentes.append({
                "lado": "rest",
                "motivo": "evento_stream_sin_orden_rest",
                "order_id": oid,
                "symbol": red.get("symbol"),
                "event": red.get("event"),
                "status_sombra": _status(red.get("status")),
            })

    for ev in ordenes_panel:
        if ev.get("estado") != "enviada":
            continue
        oid = ev.get("order_id")
        if not isinstance(oid, str) or not oid.strip():
            eventos_ausentes.append({
                "lado": "sombra",
                "motivo": "orden_enviada_sin_id",
                "order_id": None,
                "symbol": ev.get("ticker"),
            })
            continue
        oid = oid.strip()
        if oid not in por_id and oid not in ids_sin_reloj:
            eventos_ausentes.append({
                "lado": "sombra",
                "motivo": "orden_enviada_sin_evento_en_el_stream",
                "order_id": oid,
                "symbol": ev.get("ticker"),
            })

    for rev in revisiones_dia:
        oid = rev.get("order_id")
        if not isinstance(oid, str) or not oid.strip():
            continue
        oid = oid.strip()
        evs = por_id.get(oid, [])
        if not evs and oid not in ids_sin_reloj:
            eventos_ausentes.append({
                "lado": "sombra",
                "motivo": "revision_sin_evento_en_el_stream",
                "order_id": oid,
                "symbol": rev.get("ticker"),
                "resultado": rev.get("resultado"),
            })
            continue
        if rev.get("resultado") in _RESULTADO_CON_FILL and not any(
            e.get("event") in _EVENTOS_FILL for e in evs
        ):
            eventos_ausentes.append({
                "lado": "sombra",
                "motivo": "seguimiento_con_fill_sin_evento_fill",
                "order_id": oid,
                "symbol": rev.get("ticker"),
                "resultado": rev.get("resultado"),
            })
        coid = rev.get("cierre_order_id")
        if isinstance(coid, str) and coid.strip():
            coid = coid.strip()
            if coid not in por_id and coid not in ids_sin_reloj and coid not in rest_idx:
                eventos_ausentes.append({
                    "lado": "sombra",
                    "motivo": "cierre_sin_evento_en_el_stream",
                    "order_id": coid,
                    "symbol": rev.get("ticker"),
                })

    # --- status y cantidades, solo donde las dos fuentes tienen la orden ---
    estados_distintos: list[dict] = []
    datos_distintos: list[dict] = []
    datos_ausentes: list[dict] = []
    for oid, red in reducidos.items():
        symbol = red.get("symbol")
        if oid in rest_idx:
            o = rest_idx[oid]
            symbol = symbol or o.get("symbol") or o.get("_symbol")
            st_s = _status(red.get("status"))
            st_r = _status(o.get("status"))
            if st_s != st_r:
                estados_distintos.append({
                    "order_id": oid,
                    "symbol": symbol,
                    "motivo": "status_distinto" if st_s is not None and st_r is not None else "status_ausente",
                    "status_sombra": st_s,
                    "status_rest": st_r,
                    "event": red.get("event"),
                })
            _comparar_campo(
                "filled_qty", _cantidad(red.get("filled_qty")), _cantidad(o.get("filled_qty")),
                oid, symbol, datos_distintos, datos_ausentes,
            )
            sim_s = red.get("symbol") if isinstance(red.get("symbol"), str) else None
            sim_r = o.get("symbol") if isinstance(o.get("symbol"), str) else None
            if sim_r is None and isinstance(o.get("_symbol"), str):
                sim_r = o.get("_symbol")
            _comparar_campo("symbol", sim_s, sim_r, oid, symbol, datos_distintos, datos_ausentes)

    if isinstance(estado_ordenes, dict):
        for oid, red in reducidos.items():
            fila = estado_ordenes.get(oid)
            if not isinstance(fila, dict):
                continue
            st_jsonl = _status(red.get("status"))
            st_arch = _status(fila.get("status"))
            if st_jsonl is None and st_arch is None:
                continue
            if st_jsonl != st_arch:
                estados_distintos.append({
                    "order_id": oid,
                    "symbol": red.get("symbol"),
                    "motivo": "estado_archivo_distinto_del_jsonl",
                    "status_sombra": st_jsonl,
                    "status_archivo": st_arch,
                })

    for rev in revisiones_dia:
        oid = rev.get("order_id")
        if not isinstance(oid, str) or oid not in reducidos:
            continue
        motivo = _resultado_contradice(rev.get("resultado"), reducidos[oid])
        if motivo:
            estados_distintos.append({
                "order_id": oid,
                "symbol": rev.get("ticker") or reducidos[oid].get("symbol"),
                "motivo": motivo,
                "resultado": rev.get("resultado"),
                "status_sombra": _status(reducidos[oid].get("status")),
                "event": reducidos[oid].get("event"),
            })

    # --- latencia: reloj del stream contra el reloj en que el REST lo anotó ---
    latencias: list[dict] = []
    ids_latencia = set(reducidos) | {
        ev.get("order_id").strip()
        for ev in ordenes_panel
        if isinstance(ev.get("order_id"), str) and ev.get("order_id").strip()
    }
    decision_por_ticker: dict[str, str] = {}
    for ev in decisiones:
        if ev.get("entra") is not True:
            continue
        ticker = ev.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            continue
        ts = ev.get("ts")
        if _parse(ts) is None:
            continue
        previo = decision_por_ticker.get(ticker)
        if previo is None or (_parse(ts) < _parse(previo)):
            decision_por_ticker[ticker] = ts

    for oid in sorted(ids_latencia):
        evs = por_id.get(oid, [])
        red = reducidos.get(oid, {})
        t_stream = None
        for ev in evs:
            if _parse(ev.get("recibido_en")) is not None:
                t_stream = ev.get("recibido_en")
                break
        t_rest = _min_iso(
            ev.get("ts") for ev in ordenes_panel
            if isinstance(ev.get("order_id"), str) and ev.get("order_id").strip() == oid
        )
        reloj_rest = "events.jsonl" if t_rest is not None else None
        event_primero = evs[0].get("event") if evs else None
        if t_rest is None and event_primero in _EVENTOS_DE_ALTA:
            for rev in revisiones_dia:
                if rev.get("order_id") == oid and _parse(rev.get("timestamp")) is not None:
                    t_rest = rev.get("timestamp")
                    reloj_rest = "revisiones.timestamp"
                    break
        symbol = red.get("symbol")
        if symbol is None:
            for ev in ordenes_panel:
                if ev.get("order_id") == oid and isinstance(ev.get("ticker"), str):
                    symbol = ev.get("ticker")
                    break
        t_decision = decision_por_ticker.get(symbol) if isinstance(symbol, str) else None
        if t_stream is None and t_rest is None and t_decision is None:
            if evs:
                latencias.append({
                    "order_id": oid,
                    "symbol": symbol,
                    "t_stream": None,
                    "t_rest": None,
                    "reloj_rest": None,
                    "latencia_ms": None,
                    "t_decision": None,
                    "latencia_decision_a_stream_ms": None,
                    "motivo": "sin_reloj_de_deteccion_rest",
                })
            continue
        if t_stream is None:
            motivo = "sin_evento_sombra"
            latencia = None
        elif t_rest is None:
            motivo = "sin_reloj_de_deteccion_rest"
            latencia = None
        else:
            motivo = None
            latencia = delta_ms(t_stream, t_rest)
        latencias.append({
            "order_id": oid,
            "symbol": symbol,
            "t_stream": t_stream,
            "t_rest": t_rest,
            "reloj_rest": reloj_rest,
            "latencia_ms": latencia,
            "t_decision": t_decision,
            "latencia_decision_a_stream_ms": delta_ms(t_decision, t_stream),
            "motivo": motivo,
        })

    return Informe(
        dia=dia,
        fuentes=fuentes,
        n_eventos_sombra=len(en_dia),
        eventos_ausentes=eventos_ausentes,
        estados_distintos=estados_distintos,
        datos_distintos=datos_distintos,
        datos_ausentes=datos_ausentes,
        latencias=latencias,
        huecos=huecos,
        sin_reloj=sin_reloj,
        lineas_ilegibles=lineas_ilegibles,
        ordenes_truncadas=ordenes_truncadas,
    )


def resumen(inf: Informe) -> str:
    n = "sin_dato" if inf.n_eventos_sombra is None else str(inf.n_eventos_sombra)
    fuentes = " ".join(f"{k}={v}" for k, v in inf.fuentes.items())
    return (
        f"dia={inf.dia} {fuentes} n_eventos_sombra={n} "
        f"eventos_ausentes={len(inf.eventos_ausentes)} "
        f"estados_distintos={len(inf.estados_distintos)} "
        f"datos_distintos={len(inf.datos_distintos)} "
        f"datos_ausentes={len(inf.datos_ausentes)} "
        f"latencias={len(inf.latencias)} huecos={len(inf.huecos)} "
        f"sin_reloj={len(inf.sin_reloj)}"
    )


def cargar_jsonl(path: Path) -> tuple[str, list[dict] | None, int]:
    if not path.is_file():
        return "ausente", None, 0
    try:
        texto = path.read_text(encoding="utf-8")
    except OSError:
        return "ilegible", None, 0
    regs: list[dict] = []
    malas = 0
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError:
            malas += 1
            continue
        if isinstance(obj, dict):
            regs.append(obj)
        else:
            malas += 1
    return "presente", regs, malas


def cargar_revisiones(path: Path) -> tuple[str, list[dict] | None]:
    """No usa `estado.cargar`: ese helper convierte un archivo corrupto
    en lista vacía, y vacío acá significaría 'no hubo revisiones'."""
    if not path.is_file():
        return "ausente", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "ilegible", None
    if not isinstance(data, dict) or not isinstance(data.get("revisiones"), list):
        return "ilegible", None
    out = [f for f in data["revisiones"] if isinstance(f, dict)]
    if len(out) != len(data["revisiones"]):
        return "ilegible", None
    return "presente", out


def cargar_ordenes(path: Path) -> tuple[str, list | None]:
    if not path.is_file():
        return "ausente", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "ilegible", None
    if not isinstance(data, list):
        return "ilegible", None
    return "presente", data


def cargar_estado(path: Path) -> tuple[str, dict | None]:
    if not path.is_file():
        return "ausente", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "ilegible", None
    if not isinstance(data, dict) or data.get("version") != 1:
        return "ilegible", None
    ordenes = data.get("ordenes")
    if not isinstance(ordenes, dict):
        return "ilegible", None
    return "presente", ordenes


def _aplicar_carga(nombre: str, estado: str, datos, ilegibles: set[str]):
    if estado == "ilegible":
        ilegibles.add(nombre)
        return None
    if estado == "ausente":
        return None
    return datos


def pedir_ordenes_rest(dia: str) -> tuple[str, list | None, bool]:
    """GET paper `status=all` de la ventana. Un fallo no se disfraza
    de lista vacía."""
    key = os.environ.get("ALPACA_PAPER_API_KEY")
    secret = os.environ.get("ALPACA_PAPER_API_SECRET")
    if not key or not secret:
        log.error("sin credenciales de Alpaca paper; no se pide /v2/orders")
        return "ilegible", None, False
    inicio, fin = limites_ny(dia)
    desde = (inicio - timedelta(days=DIAS_LOOKBACK_REST)).isoformat()
    hasta = fin.isoformat()
    try:
        from momentum_paper_trader.alpaca_client import AlpacaPaperClient
        datos = AlpacaPaperClient(key, secret).ordenes_del_dia(desde, hasta)
    except Exception as ex:
        log.error("no se pudieron leer las órdenes (%s)", type(ex).__name__)
        return "ilegible", None, False
    return "presente", datos, len(datos) >= 500


def _ruta_sombra() -> Path:
    return Path(os.environ.get("MOMENTUM_TRADE_UPDATES_JSONL", "/var/lib/momentum/trade_updates.jsonl"))


def _ruta_estado() -> Path:
    return Path(os.environ.get("MOMENTUM_TRADE_UPDATES_ESTADO", "/var/lib/momentum/trade_updates_estado.json"))


def _ruta_eventos() -> Path:
    env = os.environ.get("DASH_EVENTOS")
    if env:
        return Path(env)
    vps = Path("/var/lib/momentum/events.jsonl")
    if vps.is_file():
        return vps
    return Path("logs/events.jsonl")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Compara la sombra trade_updates con el polling REST. No coloca órdenes.",
    )
    ap.add_argument("--dia", required=True, help="sesión America/New_York, YYYY-MM-DD")
    ap.add_argument("--sombra", type=Path, default=None)
    ap.add_argument("--estado", type=Path, default=None)
    ap.add_argument("--revisiones", type=Path, default=None)
    ap.add_argument("--eventos", type=Path, default=None)
    ap.add_argument("--ordenes-rest", type=Path, default=None, help="JSON de GET /v2/orders?status=all")
    ap.add_argument("--pedir-rest", action="store_true", help="pide el listado a Alpaca paper; no coloca nada")
    ap.add_argument("--salida", type=Path, default=None, help="escribe el JSON del informe acá")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        limites_ny(args.dia)
    except ValueError:
        log.error("dia ilegible; no se compara")
        return 2
    if args.pedir_rest and args.ordenes_rest is not None:
        log.error("pida el REST o pase un archivo, no las dos cosas")
        return 2

    ilegibles: set[str] = set()
    est_s, sombra, malas = cargar_jsonl(args.sombra or _ruta_sombra())
    sombra = _aplicar_carga("sombra", est_s, sombra, ilegibles)
    est_e, estado = cargar_estado(args.estado or _ruta_estado())
    estado = _aplicar_carga("estado", est_e, estado, ilegibles)
    est_r, revisiones = cargar_revisiones(args.revisiones or PATH_REVISIONES)
    revisiones = _aplicar_carga("revisiones", est_r, revisiones, ilegibles)
    est_ev, eventos, malas_ev = cargar_jsonl(args.eventos or _ruta_eventos())
    eventos = _aplicar_carga("eventos", est_ev, eventos, ilegibles)
    truncadas = False
    if args.pedir_rest:
        est_o, ordenes, truncadas = pedir_ordenes_rest(args.dia)
    elif args.ordenes_rest is not None:
        est_o, ordenes = cargar_ordenes(args.ordenes_rest)
    else:
        est_o, ordenes = "ausente", None
    ordenes = _aplicar_carga("ordenes_rest", est_o, ordenes, ilegibles)

    inf = comparar(
        args.dia,
        sombra=sombra,
        estado_ordenes=estado,
        revisiones=revisiones,
        eventos=eventos,
        ordenes_rest=ordenes,
        ilegibles=frozenset(ilegibles),
        lineas_ilegibles=malas + malas_ev,
        ordenes_truncadas=truncadas,
    )
    texto = json.dumps(asdict(inf), ensure_ascii=False, indent=2)
    if args.salida is not None:
        args.salida.write_text(texto + "\n", encoding="utf-8")
    else:
        print(texto)
    log.info("%s", resumen(inf))
    return codigo_salida(inf)


if __name__ == "__main__":
    raise SystemExit(main())
