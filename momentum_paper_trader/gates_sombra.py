"""Gates del auto-endurecimiento EN SOMBRA dentro del ejecutor (PR-F).

GO del dueño 2026-10-01 15:55 UTC: "arranca la sombra". Justo antes de
colocar una orden, el ejecutor pregunta "¿algún knob o el modo
defensivo la habría bloqueado?" y registra la respuesta como evento
`gate_sombra`. NUNCA cambia la decisión: no devuelve nada que el
ejecutor use, y cualquier excepción se traga (queda `gate_sombra_error`).
Encender los knobs es el PR-H y exige otro GO del dueño.

El régimen NO se calcula en el camino de la orden (podría tardar): el
gate solo LEE el último `regimen.json`. Se refresca al final de cada
corrida (`refrescar_regimen`, desde `run.py`), solo en horario de sesión
y con TTL de 5 min. Régimen ausente o de más de 15 min = CAUTELA
(fail-closed: no saber el régimen nunca es NORMAL)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from momentum_paper_trader import autoajuste, memoria_trades, regimen

log = logging.getLogger("momentum_paper_trader.gates_sombra")

NY = ZoneInfo("America/New_York")
MAX_EDAD_REGIMEN_S = 900


def _minutos_sesion(ahora: datetime) -> tuple[float, float]:
    t = ahora.astimezone(NY)
    m = t.hour * 60 + t.minute + t.second / 60
    return round(m - 570, 1), round(960 - m, 1)


def regimen_para_gate(ahora: datetime, base=None) -> dict:
    est = regimen.cargar(regimen.ruta(base))
    edad = None
    if est and est.get("calculado_en"):
        try:
            edad = (ahora - datetime.fromisoformat(est["calculado_en"])).total_seconds()
        except ValueError:
            edad = None
    if est is None or edad is None or edad < -60 or edad > MAX_EDAD_REGIMEN_S:
        return {"nivel": regimen.CAUTELA, "acciones_sombra": dict(regimen.ACCIONES[regimen.CAUTELA]),
                "motivos": ["régimen sin dato o viejo (fail-closed)"], "racha": {}}
    return est


def candidato(e, decision, n_posiciones: int | None, ahora: datetime) -> dict:
    precio = getattr(e, "ultima_entrada", None)
    disparo = memoria_trades._ts(memoria_trades._primer_disparo(e))
    desde, hasta = _minutos_sesion(ahora)
    return {
        "patron": getattr(e, "ultimo_patron", None),
        "catalizador_tipo": getattr(e, "catalizador_tipo", None),
        "es_large_cap": getattr(e, "es_large_cap", None),
        "banda_precio": memoria_trades.banda_precio(precio if isinstance(precio, (int, float)) else None),
        "franja_et": memoria_trades.franja_et(ahora),
        "confianza": getattr(decision, "confianza", None),
        "espera_desde_disparo_min": round((ahora - disparo).total_seconds() / 60, 1) if disparo else None,
        "n_posiciones": n_posiciones,
        "minutos_desde_apertura": desde,
        "minutos_hasta_cierre": hasta,
    }


def evaluar(e, decision, n_posiciones: int | None, ahora: datetime, base=None) -> dict:
    cotas, problema = autoajuste.cotas_o_none()
    ajustes = (autoajuste.cargar(autoajuste.ruta(base)) or {}).get("ajustes") or []
    reg = regimen_para_gate(ahora, base)
    ef = autoajuste.efectivos(ajustes if cotas else [], reg, cotas)
    c = candidato(e, decision, n_posiciones, ahora)
    return {"modo": "sombra", "ticker": getattr(e, "ticker", None), "creado_en": getattr(e, "creado_en", None),
            "bloquearia": autoajuste.evaluar_gates(c, ef), "nivel": reg.get("nivel"),
            "efectivos": ef, "candidato": c, "problema": problema}


def registrar(evento_fn, e, decision, n_posiciones: int | None, ahora: datetime, base=None) -> None:
    """Llama a `evento_fn("gate_sombra", **campos)`. Nunca lanza y no
    devuelve nada: el ejecutor no puede usar esto para decidir."""
    try:
        r = evaluar(e, decision, n_posiciones, ahora, base)
        evento_fn("gate_sombra", **r)
        if r["bloquearia"]:
            log.info("%s: [sombra] habría bloqueado: %s", r["ticker"],
                     ", ".join(b["knob"] for b in r["bloquearia"]))
    except Exception as ex:
        try:
            evento_fn("gate_sombra_error", ticker=getattr(e, "ticker", None), error=type(ex).__name__)
        except Exception:
            pass


def en_horario(ahora: datetime) -> bool:
    t = ahora.astimezone(NY)
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and 565 <= m <= 965


def refrescar_regimen(ahora: datetime | None = None, base=None, provider=None) -> dict | None:
    """Al final de la corrida. Nunca lanza."""
    ahora = ahora or datetime.now(UTC)
    try:
        if not en_horario(ahora):
            return None
        # Solo con el feed de Alpaca (el del VPS): no se carga Yahoo con
        # esto. Con otro proveedor el régimen queda sin dato (CAUTELA).
        if provider is None and os.environ.get("MOMENTUM_DATA_PROVIDER", "").strip().lower() != "alpaca":
            return None
        trades = memoria_trades.cargar(memoria_trades.ruta_memoria(base))

        def calcular(previo):
            prov = provider
            if prov is None:
                from momentum_hunter.data.fuente import proveedor_configurado
                prov = proveedor_configurado()
            return regimen.evaluar(prov, trades, ahora, previo)
        return regimen.vigente(ahora, calcular, regimen.ruta(base))
    except Exception as ex:
        log.warning("régimen sombra no refrescado (%s)", type(ex).__name__)
        return None
