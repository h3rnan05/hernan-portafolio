"""Pruebas del libro sombra -- simulador determinista sobre velas
sintéticas y selectores sobre archivos temporales con la misma forma
que escriben los módulos reales. Nada de red."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import BarraIntradia, FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion
from momentum_paper_trader import estado
from momentum_paper_trader import libro_sombra as ls

DIA = "2026-09-22"
T0 = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)     # 10:00 ET
P = ls.Parametros(slippage_pct=0.0)               # sin deslizamiento salvo donde se prueba


def _velas(precios: list[tuple[float, float, float]], inicio: datetime = T0, ticker: str = "ACME",
           dias_previos: int = 0) -> BarraIntradia:
    """Una vela por minuto desde `inicio`: (low, high, close). Los días
    previos, si se piden, son velas planas a 10 con rango 1 (para el ATR
    aproximado = 1)."""
    filas: list[tuple[str, float, float, float]] = []   # (ts, low, high, close)
    for d in range(dias_previos, 0, -1):
        base = inicio - timedelta(days=d)
        for m in range(3):
            filas.append(((base + timedelta(minutes=m)).isoformat(),
                          9.5 if m == 0 else 10.0, 10.5 if m == 0 else 10.0, 10.0))
    for m, (low, high, close) in enumerate(precios):
        filas.append(((inicio + timedelta(minutes=m)).isoformat(), low, high, close))
    return BarraIntradia(
        ticker, [f[0] for f in filas], [f[3] for f in filas], [f[3] for f in filas],
        [f[2] for f in filas], [f[1] for f in filas], [1.0] * len(filas))


def _senal(entrada=10.0, stop=9.0, objetivo=12.0, t0=T0, variante="disparadas") -> ls.Senal:
    return ls.Senal(variante, "ACME", t0.isoformat(), entrada, stop, objetivo, "triggered", False)


# ------------------------------ simulador ------------------------------

def test_llenado_stop_con_deslizamiento_y_mfe_mae():
    # Vela 0: no toca la entrada. Vela 1: toca (low 9.9). Vela 2: sube a
    # 11 (MFE 1R). Vela 3: cae a 8.9 -> stop.
    velas = _velas([(10.2, 10.5, 10.4), (9.9, 10.3, 10.1), (10.0, 11.0, 10.8), (8.9, 10.5, 9.0)])
    p = ls.Parametros(slippage_pct=0.01)
    r = ls.simular(_senal(), velas, p, DIA)
    assert r.llenada and r.precio_llenado == 10.1 and r.llenado_ts == velas.timestamps[1]
    assert r.motivo_salida == "stop" and r.precio_salida == 8.91
    # R = (8.91 - 10.1) / (10 - 9): el deslizamiento de ida y vuelta se paga en R.
    assert r.r == round((8.91 - 10.1) / 1.0, 3)
    assert r.mfe_r == round((11.0 - 10.1) / 1.0, 3) and r.mae_r == round((10.1 - 8.9) / 1.0, 3)
    assert r.minutos == 2.0


def test_objetivo_sin_deslizamiento():
    velas = _velas([(9.9, 10.1, 10.0), (10.0, 12.5, 12.2)])
    r = ls.simular(_senal(), velas, ls.Parametros(slippage_pct=0.01), DIA)
    assert r.motivo_salida == "objetivo" and r.precio_salida == 12.0
    assert r.r == round((12.0 - 10.1) / 1.0, 3)


def test_stop_y_objetivo_en_la_misma_vela_gana_el_stop():
    velas = _velas([(9.9, 10.1, 10.0), (8.5, 13.0, 12.0)])
    r = ls.simular(_senal(), velas, P, DIA)
    assert r.motivo_salida == "stop" and r.r == -1.0


def test_no_llenada_si_el_precio_no_baja_a_la_entrada_en_15_min():
    velas = _velas([(10.2, 10.6, 10.5)] * 20 + [(9.0, 10.0, 9.5)])   # toca recién en el minuto 20
    r = ls.simular(_senal(), velas, P, DIA)
    assert not r.llenada and r.motivo_salida == "no_llenada" and r.r is None


def test_sin_salida_se_cierra_10_min_antes_del_cierre():
    # 10:00 ET + 351 velas llega a 15:51 ET; el cierre simulado es 15:50.
    velas = _velas([(9.9, 10.1, 10.0)] + [(10.0, 10.6, 10.3)] * 352)
    r = ls.simular(_senal(), velas, ls.Parametros(slippage_pct=0.01), DIA)
    assert r.motivo_salida == "cierre"
    assert r.salida_ts == (T0 + timedelta(minutes=350)).isoformat()   # 15:50 ET
    assert r.precio_salida == round(10.3 * 0.99, 4)


def test_time_stop_cierra_a_los_30_min_si_no_va_medio_r():
    velas = _velas([(9.9, 10.1, 10.0)] + [(10.0, 10.3, 10.2)] * 40)
    p = ls.Parametros(slippage_pct=0.0, time_stop_minutos=30.0, time_stop_r=0.5)
    r = ls.simular(_senal(), velas, p, DIA)
    assert r.motivo_salida == "time_stop" and r.minutos == 30.0 and r.precio_salida == 10.2
    # Con +0,5R ya conseguido no se cierra por tiempo.
    velas2 = _velas([(9.9, 10.1, 10.0)] + [(10.4, 10.7, 10.6)] * 40)
    assert ls.simular(_senal(), velas2, p, DIA).motivo_salida == "fin_de_datos"


def test_riesgo_cero_o_sin_velas_no_se_llena():
    assert ls.simular(_senal(stop=10.0), _velas([(9.9, 10.1, 10.0)]), P, DIA).motivo_salida == "no_llenada"
    assert ls.simular(_senal(), BarraIntradia("ACME", [], [], [], [], [], []), P, DIA).motivo_salida == "no_llenada"


def test_velas_del_dia_y_atr_aproximado():
    bi = _velas([(9.9, 10.1, 10.0)], dias_previos=2)
    assert len(ls.velas_del_dia(bi, DIA).timestamps) == 1
    assert ls.atr_aproximado(bi, DIA) == 1.0          # rango 10,5 - 9,5 en cada día previo
    assert ls.atr_aproximado(_velas([(9.9, 10.1, 10.0)]), DIA) is None   # sin días previos no se inventa


# ------------------------------ selectores ------------------------------

def _candidato_diario(ticker: str) -> CandidatoDiario:
    cat = Catalizador(tipo="earnings", titular="x", fuente="Reuters", fecha=f"{DIA}T13:45:00+00:00")
    return CandidatoDiario(
        ticker=ticker, nombre=ticker, precio=10.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=0.5), catalizador=cat, meta=Metadata(ticker=ticker),
        puntuacion=Puntuacion(ticker=ticker, score_total=70.0, sub={}))


def _entrada(ticker, entrada, stop, objetivo, atr=None, disparo=T0, large=False):
    e = watchlist.desde_candidato_diario(_candidato_diario(ticker), T0 - timedelta(minutes=20))
    e.es_large_cap = large
    e.atr_diario = atr
    watchlist.marcar_triggered(e, "m", "d", "ev", disparo)
    watchlist.actualizar_niveles(e, entrada, stop, objetivo, entrada, disparo)
    return e


def _fuentes(tmp_path: Path) -> dict:
    # Watchlist: GS disparó a las 10:00 ET; LATE disparó a las 12:00 ET
    # (fuera del corte de 11:30); WAIT nunca disparó.
    gs = _entrada("GS", 10.0, 9.0, 12.0, atr=8.0, large=True)
    late = _entrada("LATE", 20.0, 19.0, 22.0, atr=1.0, disparo=T0 + timedelta(hours=2))
    wait = watchlist.desde_candidato_diario(_candidato_diario("WAIT"), T0)
    wl = tmp_path / "watchlist.json"
    watchlist.guardar([gs, late, wait], wl)
    # Revisiones: GS con 5 (cuenta para ia_confianza_5, no entró); LATE
    # con 4 (no cuenta); ORD colocada de verdad.
    revs = [
        estado.RevisionIA("GS", gs.creado_en, False, 5, "casi", (T0 + timedelta(seconds=20)).isoformat(),
                          ia_entraria=False, es_large_cap=True),
        estado.RevisionIA("LATE", late.creado_en, False, 4, "no", (T0 + timedelta(hours=2)).isoformat(),
                          ia_entraria=False),
        estado.RevisionIA("ORD", f"{DIA}T14:30:00+00:00", True, 8, "sí", (T0 + timedelta(minutes=30)).isoformat(),
                          precio_entrada=5.0, stop=4.5, objetivo=6.0, ia_entraria=True),
    ]
    rp = tmp_path / "revisiones.json"
    estado.guardar(revs, rp)
    # Auditoría: CASI con las tres condiciones y score 47 (entra en
    # score_45, no en score_50); GS accionable (no es "casi").
    aud = tmp_path / "auditoria"
    aud.mkdir()
    fi = {"precio_actual": 30.0, "vwap": 29.5, "ema9": 29.8}
    data = {"corridas": [
        {"timestamp": (T0 + timedelta(minutes=5)).isoformat(), "candidatos": [
            {"ticker": "CASI", "factores_intradia": fi,
             "evaluacion": {"patron": "orb", "temprano": True, "riesgo_definido": True, "score_ajustado": 47.0, "accionable": False}},
            {"ticker": "GS", "factores_intradia": fi,
             "evaluacion": {"patron": "orb", "temprano": True, "riesgo_definido": True, "score_ajustado": 60.0, "accionable": True}},
        ]}]}
    (aud / f"{DIA}.json").write_text(json.dumps(data))
    # Movers: CLB de clase B; RND solo en el screener (para el control).
    th = tmp_path / "telem_hunter"
    (th / DIA / "vps").mkdir(parents=True)
    mv = {"timestamp": (T0 + timedelta(minutes=10)).isoformat(), "candidatas": [
        {"ticker": "CLB", "clase": "B", "precio_intradia": 40.0, "vwap": 39.0, "hora_dato": (T0 + timedelta(minutes=9)).isoformat()},
        {"ticker": "RND", "clase": None, "precio_intradia": 50.0, "vwap": 49.0},
    ]}
    (th / DIA / "vps" / "movers.jsonl").write_text(json.dumps(mv) + "\n")
    return {"path_watchlist": wl, "path_revisiones": rp, "dir_auditoria": aud,
            "dir_telemetria_hunter": th, "dir_telemetria_paper": tmp_path / "telem_paper"}


class _Provider:
    """Cada ticker: 4 días previos planos (ATR aprox = 1) y hoy una serie
    que toca la entrada y luego el objetivo, salvo GS que va al stop."""
    def __init__(self):
        self.pedidos = []

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        self.pedidos.append(tuple(tickers))
        out = {}
        for t in tickers:
            base = {"GS": 10.0, "LATE": 20.0, "ORD": 5.0, "CASI": 30.0, "CLB": 40.0, "RND": 50.0}.get(t, 10.0)
            if t == "GS":
                hoy = [(base * 0.99, base * 1.01, base)] * 3 + [(base * 0.85, base, base * 0.9)] * 400
            else:
                hoy = [(base * 0.99, base * 1.01, base)] * 3 + [(base, base * 1.5, base * 1.4)] * 400
            out[t] = _velas(hoy, inicio=T0 - timedelta(minutes=30), ticker=t, dias_previos=4)
        return out


def test_correr_dia_arma_todas_las_variantes_y_guarda(tmp_path):
    fuentes = _fuentes(tmp_path)
    prov = _Provider()
    salida = ls.correr_dia(DIA, prov, p=ls.Parametros(slippage_pct=0.0, aleatorias_por_dia=2),
                           fuente="vps", umbral_real=55.0, **fuentes)

    assert prov.pedidos and set(prov.pedidos[0]) == {"GS", "LATE", "WAIT", "ORD", "CASI", "CLB", "RND"}
    por_variante: dict[str, list[dict]] = {}
    for r in salida["resultados"]:
        por_variante.setdefault(r["variante"], []).append(r)
    # disparadas: GS y LATE; corte_1130 solo GS; ia_confianza_5 solo GS.
    assert sorted(r["ticker"] for r in por_variante["disparadas"]) == ["GS", "LATE"]
    assert [r["ticker"] for r in por_variante["corte_1130"]] == ["GS"]
    assert [r["ticker"] for r in por_variante["ia_confianza_5"]] == ["GS"]
    assert [r["ticker"] for r in por_variante["ia_real"]] == ["ORD"]
    assert [r["ticker"] for r in por_variante["score_45"]] == ["CASI"] and "score_50" not in por_variante
    assert [r["ticker"] for r in por_variante["sin_catalizador"]] == ["CLB"]
    assert len(por_variante["aleatorio"]) == 2 and {r["ticker"] for r in por_variante["aleatorio"]} <= {"CLB", "RND"}
    # stop_05atr usa el ATR real de la watchlist (8): GS stop = 10 - 4 = 6, objetivo 18.
    gs05 = next(r for r in por_variante["stop_05atr"] if r["ticker"] == "GS")
    assert gs05["stop"] == 6.0 and gs05["objetivo"] == 18.0 and gs05["atr_aproximado"] is False
    # Resultados: GS va al stop (-1R); LATE al objetivo (+2R); CASI con niveles del snapshot.
    assert next(r for r in por_variante["disparadas"] if r["ticker"] == "GS")["r"] == -1.0
    assert next(r for r in por_variante["disparadas"] if r["ticker"] == "LATE")["motivo_salida"] == "objetivo"
    casi = por_variante["score_45"][0]
    assert casi["entrada"] == 30.0 and casi["atr_aproximado"] is True and casi["stop"] < 29.8
    # Archivo guardado donde el persist ya lo sube.
    ruta = fuentes["dir_telemetria_paper"] / DIA / "vps" / "sombra.json"
    assert ruta.exists() and json.loads(ruta.read_text())["dia"] == DIA


def test_control_aleatorio_es_reproducible(tmp_path):
    fuentes = _fuentes(tmp_path)
    a = ls.correr_dia(DIA, _Provider(), p=ls.Parametros(aleatorias_por_dia=2), **fuentes)
    b = ls.correr_dia(DIA, _Provider(), p=ls.Parametros(aleatorias_por_dia=2), **fuentes)
    ra = [(r["ticker"], r["t0"], r["entrada"]) for r in a["resultados"] if r["variante"] == "aleatorio"]
    rb = [(r["ticker"], r["t0"], r["entrada"]) for r in b["resultados"] if r["variante"] == "aleatorio"]
    assert ra == rb and len(ra) == 2


def test_sin_velas_la_senal_queda_fuera_y_se_dice(tmp_path):
    class _Vacio:
        def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
            return {}
    salida = ls.correr_dia(DIA, _Vacio(), **_fuentes(tmp_path))
    assert salida["resultados"] == [] and salida["tickers_con_velas"] == 0
    assert "disparadas:GS" in salida["senales_sin_velas"]


def test_provider_roto_no_tumba_la_corrida(tmp_path):
    class _Roto:
        def barras_intradia(self, *a, **kw):
            raise RuntimeError("sin red")
    salida = ls.correr_dia(DIA, _Roto(), **_fuentes(tmp_path))
    assert salida["resultados"] == []


# ------------------------------ resumen ------------------------------

def test_resumir_calcula_las_metricas_por_variante():
    rs = [
        {"variante": "v", "t0": "a", "llenada": True, "r": 2.0, "mfe_r": 2.1, "mae_r": 0.9, "motivo_salida": "objetivo"},
        {"variante": "v", "t0": "b", "llenada": True, "r": -1.0, "mfe_r": 0.3, "mae_r": 1.0, "motivo_salida": "stop"},
        {"variante": "v", "t0": "c", "llenada": True, "r": -1.0, "mfe_r": 0.1, "mae_r": 1.0, "motivo_salida": "stop"},
        {"variante": "v", "t0": "d", "llenada": False, "r": None, "motivo_salida": "no_llenada"},
        {"variante": "w", "t0": "a", "llenada": True, "r": 0.5, "mfe_r": 0.8, "mae_r": 0.2, "motivo_salida": "cierre"},
    ]
    m = ls.resumir(rs)
    v = m["v"]
    assert v.senales == 4 and v.llenadas == 3 and v.ganadoras == 1
    assert v.tasa_acierto == 1 / 3 and v.expectativa_r == 0.0 and v.profit_factor == 1.0
    assert v.ganadoras_con_mae_alto == 1          # la ganadora tocó -0,9R antes: stop dentro del ruido
    assert v.drawdown_max_r == 2.0                # +2 y luego -1 -1
    assert v.salidas == {"objetivo": 1, "stop": 2, "no_llenada": 1}
    assert m["w"].profit_factor is None           # sin perdedoras no hay cociente que inventar
    texto = ls.formatear_resumen(m, DIA, DIA)
    assert "LIBRO SOMBRA" in texto and "v " in texto and "33%" in texto and "salidas: no_llenada 1" in texto
    assert ls.formatear_resumen({}, DIA, DIA).endswith("(sin resultados en el rango)")


def test_cargar_resultados_respeta_el_rango(tmp_path):
    for dia in ("2026-09-20", "2026-09-22"):
        (tmp_path / dia / "vps").mkdir(parents=True)
        (tmp_path / dia / "vps" / "sombra.json").write_text(json.dumps({"resultados": [{"variante": "v", "t0": dia}]}))
    assert len(ls.cargar_resultados("2026-09-21", "2026-09-22", tmp_path)) == 1
    assert ls.cargar_resultados("2026-09-01", "2026-09-30", tmp_path / "no") == []
