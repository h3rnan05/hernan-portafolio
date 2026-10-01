"""Motor de auto-endurecimiento con allowlist (PR-D). HOY SOLO SOMBRA.

GO del dueño 2026-10-01 15:55 UTC: "arranca la sombra y el panel". El
motor propone ajustes, los valida contra `config/autoajuste_cotas.yaml`
y calcula los límites efectivos, pero NINGÚN ajuste bloquea nada real:
el ejecutor solo registra qué habría bloqueado (`gate_sombra`, PR-F).
Encender un knob es el PR-H y exige otro GO, knob por knob.

Reglas que este módulo hace cumplir (no son opinables):
  1. Solo aprieta. `validar` rechaza cualquier valor más laxo que la base.
  2. Solo knobs de la allowlist (K1–K5). Otro nombre se rechaza.
  3. Cada ajuste nace con muestra mínima y una fecha de vencimiento;
     vencido sin re-proponerse, se cae.
  4. Fail-closed: cotas ilegibles → ningún ajuste (base) y aviso; nunca
     un aflojamiento inventado. Un dato faltante del candidato en un gate
     no se toma como 0: el gate dice "sin dato".
  5. `modo` distinto de "sombra" se trata como sombra (PR-H no autorizado)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("momentum_paper_trader.autoajuste")

RUTA_COTAS = Path(__file__).resolve().parent.parent / "config" / "autoajuste_cotas.yaml"
RELATIVO_DIR = "momentum_paper_trader/aprendizaje"
ARCHIVO = "ajustes.json"

K1, K2, K3, K4, K5 = ("K1_saltar_segmento", "K2_confianza_mas_uno", "K3_max_posiciones",
                      "K4_edad_max_senal", "K5_sin_small_caps")
ALLOWLIST = frozenset({K1, K2, K3, K4, K5})
MODO_SOMBRA = "sombra"


class CotasInvalidas(Exception):
    pass


class AjusteRechazado(Exception):
    pass


# ───────────────────────── cotas ─────────────────────────

def cargar_cotas(ruta: Path = RUTA_COTAS) -> dict:
    import yaml
    try:
        d = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    except Exception as ex:
        raise CotasInvalidas(type(ex).__name__) from ex
    if not isinstance(d, dict) or not isinstance(d.get("knobs"), dict) or not isinstance(d.get("base"), dict):
        raise CotasInvalidas("forma")
    desconocidos = set(d["knobs"]) - ALLOWLIST
    if desconocidos:
        raise CotasInvalidas(f"knobs fuera de la allowlist: {sorted(desconocidos)}")
    for k in ("maximo_posiciones_abiertas", "confianza_minima_entrada"):
        if not isinstance(d["base"].get(k), int):
            raise CotasInvalidas(f"base.{k}")
    if d.get("modo") != MODO_SOMBRA:
        log.warning("autoajuste: modo=%r no autorizado (PR-H sin GO) -- se trata como sombra", d.get("modo"))
    d["modo"] = MODO_SOMBRA
    return d


def cotas_o_none(ruta: Path = RUTA_COTAS) -> tuple[dict | None, str | None]:
    try:
        return cargar_cotas(ruta), None
    except CotasInvalidas as ex:
        log.error("autoajuste: cotas ilegibles (%s) -- sin ajustes, se usa la base", ex)
        return None, f"cotas ilegibles: {ex}"


# ───────────────────────── validación (solo aprieta) ─────────────────────────

def validar(aj: dict, cotas: dict) -> dict:
    knob = aj.get("knob")
    if knob not in ALLOWLIST or knob not in cotas["knobs"]:
        raise AjusteRechazado(f"knob fuera de la allowlist: {knob!r}")
    c, base = cotas["knobs"][knob], cotas["base"]
    v = aj.get("valor")
    if knob == K3:
        if not isinstance(v, int) or v >= base["maximo_posiciones_abiertas"] or v < c["piso"]:
            raise AjusteRechazado(f"K3 fuera de cota: {v!r} (base {base['maximo_posiciones_abiertas']}, piso {c['piso']})")
    elif knob == K2:
        if not isinstance(v, int) or v <= base["confianza_minima_entrada"] or v > c["tope"]:
            raise AjusteRechazado(f"K2 fuera de cota: {v!r}")
        if not aj.get("segmento"):
            raise AjusteRechazado("K2 nunca es global: falta segmento")
    elif knob == K4:
        if v not in c["valores_min"]:
            raise AjusteRechazado(f"K4 fuera de cota: {v!r}")
    elif knob == K1:
        if not aj.get("segmento") or aj["segmento"].get("valor") in (None, "sin dato"):
            raise AjusteRechazado("K1 necesita un segmento conocido")
    elif knob == K5:
        if v is not True:
            raise AjusteRechazado("K5 solo puede valer True")
    return aj


# ───────────────────────── fechas ─────────────────────────

def sumar_sesiones(desde: str, n: int) -> str:
    """Fecha (Lun–Vie) a `n` sesiones de `desde`. Feriados no se descuentan:
    vence antes, nunca después (conservador para un ajuste)."""
    d = date.fromisoformat(desde)
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d.isoformat()


# ───────────────────────── propuestas ─────────────────────────

def _ajuste(knob, valor, motivo, hoy, dur, segmento=None, evidencia=None) -> dict:
    return {"knob": knob, "valor": valor, "segmento": segmento, "motivo": motivo,
            "desde": hoy, "vence": sumar_sesiones(hoy, dur), "estado": "sombra",
            "evidencia": evidencia or {}}


def _calibracion(trades: list[dict], dim: str, valor: str, base_conf: int, n_min: int) -> dict | None:
    seg = [t for t in trades if str(t.get(dim)) == valor and t.get("r") is not None
           and t.get("confianza") is not None]
    alto = [t["r"] for t in seg if t["confianza"] >= base_conf + 1]
    bajo = [t["r"] for t in seg if t["confianza"] == base_conf]
    if len(alto) < n_min or len(bajo) < n_min:
        return None
    return {"r_alto": sum(alto) / len(alto), "r_base": sum(bajo) / len(bajo),
            "n_alto": len(alto), "n_base": len(bajo)}


def proponer(stats: dict, trades: list[dict], regimen_estado: dict | None, cotas: dict, hoy: str) -> list[dict]:
    """Ajustes que el motor propondría HOY (todos en sombra)."""
    out: list[dict] = []
    ks = cotas["knobs"]
    validos = [t for t in trades if t.get("cuenta_para_aprender") is True]
    total = len([t for t in validos if t.get("r") is not None]) or 0
    segs = stats.get("segmentos") or []
    # K1: segmentos en estado `propuesta` (las guardas ya están en aprendizaje_stats).
    if K1 in ks and total:
        c = ks[K1]
        cand = sorted((s for s in segs if s.get("estado") == "propuesta" and s.get("valor") != "sin dato"
                       and s["n"] >= c["n_minimo"] and s["sesiones"] >= c["sesiones_minimas"]
                       and s.get("r_shr") is not None and s["r_shr"] <= c["r_shr_maximo"]
                       and s["n"] / total <= c["max_fraccion_senales"]),
                      key=lambda s: s["r_shr"])
        for s in cand[: c["max_segmentos"]]:
            out.append(_ajuste(K1, True, f"{s['dimension']}={s['valor']}: R_shr {s['r_shr']:+.2f}, n={s['n']}",
                               hoy, c["duracion_sesiones"], {"dimension": s["dimension"], "valor": s["valor"]},
                               {"n": s["n"], "r_shr": s["r_shr"], "r_ic": s.get("r_ic")}))
    # K2: confianza +1 solo si en ese segmento más confianza rinde mejor.
    if K2 in ks:
        c = ks[K2]
        base_conf = cotas["base"]["confianza_minima_entrada"]
        for s in segs:
            if s.get("dimension") == "confianza" or s.get("valor") == "sin dato":
                continue
            if s["n"] < c["n_minimo"] or s["sesiones"] < c["sesiones_minimas"] or (s.get("r_shr") or 0) >= 0:
                continue
            cal = _calibracion(validos, s["dimension"], s["valor"], base_conf, c["n_minimo_por_nivel"])
            if cal and cal["r_alto"] > cal["r_base"]:
                out.append(_ajuste(K2, base_conf + 1, f"{s['dimension']}={s['valor']}: conf≥{base_conf + 1} rinde mejor",
                                   hoy, c["duracion_sesiones"], {"dimension": s["dimension"], "valor": s["valor"]}, cal))
    # K3: racha (no necesita muestra por segmento: es riesgo).
    rch = (regimen_estado or {}).get("racha") or {}
    if K3 in ks and rch.get("disparada"):
        out.append(_ajuste(K3, ks[K3]["valor"], "racha: " + ", ".join(rch.get("motivos") or []),
                           hoy, ks[K3]["duracion_sesiones"], evidencia=rch))
    # K4: espera de slot.
    if K4 in ks:
        c = ks[K4]
        s = next((s for s in segs if s.get("dimension") == "espera_slot" and s.get("valor") == ">=10min"), None)
        if s and s["n"] >= c["n_minimo"] and s.get("r_shr") is not None and s["r_shr"] <= c["r_shr_maximo"]:
            out.append(_ajuste(K4, c["valores_min"][0], f"espera ≥10 min: R_shr {s['r_shr']:+.2f}, n={s['n']}",
                               hoy, c["duracion_sesiones"], evidencia={"n": s["n"], "r_shr": s["r_shr"]}))
    # K5 no se propone de noche: lo aplica el régimen intradía en
    # `efectivos` (de noche no hay régimen que leer).
    validos_out = []
    for aj in out:
        try:
            validos_out.append(validar(aj, cotas))
        except AjusteRechazado as ex:
            log.warning("autoajuste: propuesta rechazada (%s)", ex)
    return validos_out


def _clave(aj: dict) -> tuple:
    seg = aj.get("segmento") or {}
    return (aj.get("knob"), seg.get("dimension"), seg.get("valor"))


def combinar(previos: list[dict], nuevos: list[dict], hoy: str) -> tuple[list[dict], list[dict]]:
    """(vigentes, vencidos). Uno re-propuesto renueva su vencimiento; uno
    vencido sin re-proponerse se cae (estado `vencido`)."""
    nuevos_por = {_clave(a): a for a in nuevos}
    vigentes, vencidos = [], []
    for a in previos:
        k = _clave(a)
        if k in nuevos_por:
            n = nuevos_por.pop(k)
            vigentes.append(dict(n, desde=a.get("desde") or n["desde"]))
        elif (a.get("vence") or "") > hoy:
            vigentes.append(a)
        else:
            vencidos.append(dict(a, estado="vencido", vencido_en=hoy))
    vigentes.extend(nuevos_por.values())
    return vigentes, vencidos


# ───────────────────────── límites efectivos (lo más restrictivo gana) ─────────────────────────

def efectivos(ajustes: list[dict], regimen_estado: dict | None, cotas: dict | None) -> dict:
    base_pos = (cotas or {}).get("base", {}).get("maximo_posiciones_abiertas")
    acc = (regimen_estado or {}).get("acciones_sombra") or {}
    topes = [v for v in [base_pos, acc.get("max_posiciones")] +
             [a["valor"] for a in ajustes if a.get("knob") == K3] if isinstance(v, int)]
    return {
        "modo": MODO_SOMBRA,
        "max_posiciones": min(topes) if topes else None,
        "segmentos_saltados": [a["segmento"] for a in ajustes if a.get("knob") == K1],
        "confianza_por_segmento": [{"segmento": a["segmento"], "minimo": a["valor"]}
                                   for a in ajustes if a.get("knob") == K2],
        "edad_max_min": min([a["valor"] for a in ajustes if a.get("knob") == K4], default=None),
        "sin_small_caps": bool(acc.get("sin_small_caps")) or any(a.get("knob") == K5 for a in ajustes),
        "sin_apertura_ni_ultima_hora": bool(acc.get("sin_apertura_ni_ultima_hora")),
        "sin_entradas": bool(acc.get("sin_entradas")),
        "regimen_nivel": (regimen_estado or {}).get("nivel"),
    }


def evaluar_gates(c: dict, ef: dict) -> list[dict]:
    """Qué knobs HABRÍAN bloqueado a este candidato. `c` trae los rasgos
    del candidato (patron, catalizador_tipo, es_large_cap, banda_precio,
    franja_et, confianza, espera_desde_disparo_min, n_posiciones,
    minutos_desde_apertura, minutos_hasta_cierre). Puro y sin efectos."""
    b: list[dict] = []
    if ef.get("sin_entradas"):
        b.append({"knob": "regimen", "motivo": "sin entradas nuevas (racha + SPY bajo SMA20)"})
    mp = ef.get("max_posiciones")
    if isinstance(mp, int):
        n = c.get("n_posiciones")
        if n is None:
            b.append({"knob": K3, "motivo": "sin dato de posiciones abiertas (fail-closed)"})
        elif n >= mp:
            b.append({"knob": K3, "motivo": f"{n} posiciones ≥ tope sombra {mp}"})
    if ef.get("sin_small_caps") and c.get("es_large_cap") is not True:
        b.append({"knob": K5, "motivo": "small cap o banda sin dato"})
    if ef.get("sin_apertura_ni_ultima_hora"):
        a, z = c.get("minutos_desde_apertura"), c.get("minutos_hasta_cierre")
        if a is None or z is None or a < 30 or z < 60:
            b.append({"knob": "regimen", "motivo": "DEFENSIVO: primeros 30 min o última hora"})
    for s in ef.get("segmentos_saltados") or []:
        v = c.get(s.get("dimension"))
        if v is not None and str(v) == s.get("valor"):
            b.append({"knob": K1, "motivo": f"segmento {s['dimension']}={s['valor']} saltado"})
    for x in ef.get("confianza_por_segmento") or []:
        s = x["segmento"]
        if str(c.get(s["dimension"])) == s["valor"]:
            conf = c.get("confianza")
            if conf is None or conf < x["minimo"]:
                b.append({"knob": K2, "motivo": f"confianza {conf} < {x['minimo']} en {s['dimension']}={s['valor']}"})
    em = ef.get("edad_max_min")
    if em is not None:
        e = c.get("espera_desde_disparo_min")
        if e is None:
            b.append({"knob": K4, "motivo": "sin dato de edad de la señal (fail-closed)"})
        elif e > em:
            b.append({"knob": K4, "motivo": f"señal con {e:.0f} min > {em} min"})
    return b


# ───────────────────────── persistencia ─────────────────────────

def ruta(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base) / ARCHIVO
    from momentum_hunter.rutas_estado import resolver
    return resolver(RELATIVO_DIR, es_dir=True) / ARCHIVO


def cargar(p: Path) -> dict | None:
    """None si falta o es ilegible: quien llama usa la base (fail-closed
    hacia NO apretar en real; en sombra eso solo significa 'sin gates')."""
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("ajustes"), list):
        return None
    return d


def guardar(p: Path, vigentes: list[dict], vencidos: list[dict], ahora: datetime, problema: str | None = None) -> None:
    previo = cargar(p) or {}
    historial = (previo.get("historial") or []) + vencidos
    d = {"version": 1, "modo": MODO_SOMBRA, "actualizado_en": ahora.astimezone(UTC).isoformat(timespec="seconds"),
         "ajustes": vigentes, "historial": historial[-200:], "problema": problema}
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(p).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, p)
