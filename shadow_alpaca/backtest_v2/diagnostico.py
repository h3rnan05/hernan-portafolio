"""Diagnóstico de entrada: qué hace el precio en los 60 min tras la ruptura.

No simula trades. Para cada señal de la regla base (cierre de la vela de
ruptura del ORB, con todos los filtros ya pasados) mira las velas de los
60 minutos siguientes y responde:

  - ¿volvió al máximo del rango de apertura? ¿en qué minuto?
  - ¿volvió al VWAP (acumulado del día en cada minuto)? ¿en qué minuto?
  - mínimo del retroceso: la mínima más baja de los 60 min, en % desde el
    precio de la señal, y en qué minuto;
  - máximo alcanzado después de ese mínimo, en R con riesgo = precio de
    la señal − mínimo del retroceso;
  - MFE desde tres entradas hipotéticas, en R:
      cierre de ruptura   entrada = precio de la señal; R = distancia al
                          mínimo del ORB acotada a 1,5–4 % (lo que la
                          estrategia usaría si el tope no la descartara);
      retest del máximo   entrada = máximo del ORB en el minuto del retest;
                          R = entrada − mínimo entre la ruptura y el retest
                          (piso 1,5 %);
      retest del VWAP     ídem con el VWAP del minuto del retest.
    MFE = (máximo posterior a la entrada dentro de los 60 min − entrada) / R.

Los minutos cuentan desde el cierre de la vela de ruptura. "—" es sin
dato (no volvió, o no hay velas): nunca se rellena con cero.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from estrategia_v2 import reglas

from shadow_alpaca.backtest_v2.informe import _mediana, _p, _r

VENTANA = timedelta(minutes=60)
PISO_R = 0.015
TECHO_R = 0.04
MTY = ZoneInfo("America/Monterrey")


@dataclass
class Fila:
    ticker: str
    dia: str
    hora_ny: str
    precio: float
    orb_alto: float | None
    vwap_senal: float | None
    velas: int
    retest_max_min: int | None
    retest_vwap_min: int | None
    retroceso_pct: float | None
    retroceso_min: int | None
    max_tras_retroceso_r: float | None
    mfe_ruptura_r: float | None
    mfe_retest_max_r: float | None
    mfe_retest_vwap_r: float | None


def _minutos(desde: datetime, v) -> int:
    return int((v.t + reglas.UN_MINUTO - desde).total_seconds() // 60)


def analizar_senal(s, velas, cfg) -> Fila:
    sen = cfg.senal
    ny = ZoneInfo(sen.zona_horaria)
    regulares_previas = [v for v in velas if reglas.es_regular(v, sen) and v.t < s.momento]
    vwap_senal = reglas.vwap(regulares_previas)
    tras = [v for v in velas if s.momento <= v.t < s.momento + VENTANA]
    fila = Fila(s.ticker, s.dia.isoformat(), s.momento.astimezone(ny).strftime("%H:%M"), s.precio, s.orb_alto,
                vwap_senal, len(tras), None, None, None, None, None, None, None, None)
    if not tras:
        return fila
    # Retests.
    acumuladas = list(regulares_previas)
    minimo_hasta = None
    vwap_en_retest = None
    for v in tras:
        acumuladas.append(v)
        minimo_hasta = v.l if minimo_hasta is None else min(minimo_hasta, v.l)
        if fila.retest_max_min is None and s.orb_alto is not None and v.l <= s.orb_alto:
            fila.retest_max_min = _minutos(s.momento, v)
            min_retest_max = minimo_hasta
            idx_retest_max = tras.index(v)
        vw = reglas.vwap(acumuladas)
        if fila.retest_vwap_min is None and vw is not None and v.l <= vw:
            fila.retest_vwap_min = _minutos(s.momento, v)
            vwap_en_retest = vw
            min_retest_vwap = minimo_hasta
            idx_retest_vwap = tras.index(v)
    # Retroceso mínimo y máximo posterior.
    k_min = min(range(len(tras)), key=lambda k: tras[k].l)
    minimo = tras[k_min].l
    fila.retroceso_pct = (s.precio - minimo) / s.precio
    fila.retroceso_min = _minutos(s.momento, tras[k_min])
    despues = tras[k_min:]
    riesgo = s.precio - minimo
    if riesgo > 0:
        fila.max_tras_retroceso_r = (max(v.h for v in despues) - s.precio) / riesgo
    # MFE desde el cierre de ruptura con el riesgo de la estrategia (acotado).
    dist = (s.precio - s.orb_bajo) / s.precio if s.precio > 0 else None
    if dist is not None:
        r_base = s.precio * min(max(dist, PISO_R), TECHO_R)
        fila.mfe_ruptura_r = (max(v.h for v in tras) - s.precio) / r_base
    if fila.retest_max_min is not None:
        entrada = s.orb_alto
        r = max(entrada - min_retest_max, entrada * PISO_R)
        fila.mfe_retest_max_r = (max(v.h for v in tras[idx_retest_max:]) - entrada) / r
    if fila.retest_vwap_min is not None:
        entrada = vwap_en_retest
        r = max(entrada - min_retest_vwap, entrada * PISO_R)
        fila.mfe_retest_vwap_r = (max(v.h for v in tras[idx_retest_vwap:]) - entrada) / r
    return fila


def analizar(res, cfg) -> list[Fila]:
    senales = res.senales_base or res.senales
    return [analizar_senal(s, res.velas_de.get((s.ticker, s.dia), []), cfg) for s in senales]


def resumen(filas: list[Fila]) -> dict:
    con = [f for f in filas if f.velas]
    def med(xs):
        return _mediana([x for x in xs if x is not None])
    return {
        "senales": len(filas), "con_velas": len(con),
        "pct_retest_max": (sum(1 for f in con if f.retest_max_min is not None) / len(con)) if con else None,
        "pct_retest_vwap": (sum(1 for f in con if f.retest_vwap_min is not None) / len(con)) if con else None,
        "minuto_retest_max_med": med([f.retest_max_min for f in con]),
        "minuto_retest_vwap_med": med([f.retest_vwap_min for f in con]),
        "retroceso_pct_med": med([f.retroceso_pct for f in con]),
        "retroceso_min_med": med([f.retroceso_min for f in con]),
        "max_tras_retroceso_r_med": med([f.max_tras_retroceso_r for f in con]),
        "mfe_ruptura_r_med": med([f.mfe_ruptura_r for f in con]),
        "mfe_retest_max_r_med": med([f.mfe_retest_max_r for f in con]),
        "mfe_retest_vwap_r_med": med([f.mfe_retest_vwap_r for f in con]),
        "mfe_ruptura_mayor_05": sum(1 for f in con if (f.mfe_ruptura_r or 0) > 0.5),
        "mfe_retest_max_mayor_05": sum(1 for f in con if (f.mfe_retest_max_r or 0) > 0.5),
        "mfe_retest_vwap_mayor_05": sum(1 for f in con if (f.mfe_retest_vwap_r or 0) > 0.5),
    }


def _n(v, fmt="{:.2f}"):
    return "—" if v is None else fmt.format(v)


def markdown(filas: list[Fila], desde, hasta) -> str:
    r = resumen(filas)
    ahora = datetime.now(UTC)
    out = [f"# Diagnóstico de entrada — señales base {desde} a {hasta}", "",
           f"Generado {ahora:%Y-%m-%d %H:%M} UTC / {ahora.astimezone(MTY):%H:%M} Monterrey (UTC−6). "
           f"{r['senales']} señales de la regla base (cierre de la vela de ruptura), {r['con_velas']} con velas en los "
           "60 min siguientes. Sin trades simulados. Ver el docstring de `diagnostico.py` para las definiciones.", "",
           "## Resumen", "", "| medida | valor |", "|---|---:|",
           f"| volvió al máximo del ORB | {_p(r['pct_retest_max'])} (minuto mediano {_n(r['minuto_retest_max_med'], '{:.0f}')}) |",
           f"| volvió al VWAP | {_p(r['pct_retest_vwap'])} (minuto mediano {_n(r['minuto_retest_vwap_med'], '{:.0f}')}) |",
           f"| retroceso mínimo (mediana) | {_p(r['retroceso_pct_med'])} en el minuto {_n(r['retroceso_min_med'], '{:.0f}')} |",
           f"| máximo tras el retroceso (mediana, R = precio − mínimo) | {_r(r['max_tras_retroceso_r_med'])} |",
           "", "| entrada hipotética | MFE mediana | señales con MFE > 0,5 R |", "|---|---:|---:|",
           f"| cierre de la vela de ruptura (R = ORB acotado 1,5–4 %) | {_r(r['mfe_ruptura_r_med'])} | {r['mfe_ruptura_mayor_05']} |",
           f"| retest del máximo del ORB (R = mínimo del retroceso) | {_r(r['mfe_retest_max_r_med'])} | {r['mfe_retest_max_mayor_05']} |",
           f"| retest del VWAP (R = mínimo del retroceso) | {_r(r['mfe_retest_vwap_r_med'])} | {r['mfe_retest_vwap_mayor_05']} |",
           "", "## Por señal", "",
           "| ticker | día | hora NY | precio | ORB máx | VWAP | retest máx (min) | retest VWAP (min) | retroceso | min. | "
           "máx. tras retroceso | MFE ruptura | MFE retest máx | MFE retest VWAP |",
           "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for f in filas:
        out.append(f"| {f.ticker} | {f.dia} | {f.hora_ny} | {f.precio:.2f} | {_n(f.orb_alto)} | {_n(f.vwap_senal)} | "
                   f"{_n(f.retest_max_min, '{}')} | {_n(f.retest_vwap_min, '{}')} | {_p(f.retroceso_pct)} | "
                   f"{_n(f.retroceso_min, '{}')} | {_r(f.max_tras_retroceso_r)} | {_r(f.mfe_ruptura_r)} | "
                   f"{_r(f.mfe_retest_max_r)} | {_r(f.mfe_retest_vwap_r)} |")
    return "\n".join(out) + "\n"


def a_json(filas: list[Fila]) -> str:
    return json.dumps({"resumen": resumen(filas), "filas": [asdict(f) for f in filas]}, ensure_ascii=False, indent=1)
