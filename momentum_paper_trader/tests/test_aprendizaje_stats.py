"""Estadísticas por segmento (PR-B): funciones puras con guardas."""
from momentum_paper_trader import aprendizaje_stats as st


def _t(r, fecha="2026-10-01", pnl=None, **kw):
    d = {"r": r, "fecha": fecha, "pnl": pnl if pnl is not None else (None if r is None else r * 10), "cuenta_para_aprender": True}
    d.update(kw)
    return d


def test_wilson_referencia():
    lo, hi = st.wilson(0, 8, 0.10)
    assert lo == 0.0 and 0.24 < hi < 0.27
    lo, hi = st.wilson(5, 10, 0.05)
    assert abs(lo - 0.2366) < 1e-3 and abs(hi - 0.7634) < 1e-3
    assert st.wilson(0, 0) is None


def test_beta_posterior():
    assert st.beta_posterior(0, 0) == 0.5
    assert st.beta_posterior(2, 4) == 0.5
    assert abs(st.beta_posterior(0, 8) - 2 / 12) < 1e-9


def test_contraccion():
    assert st.contraer(None, 0, -0.2) == -0.2
    assert st.contraer(-1.0, 0, -0.2) == -0.2
    assert abs(st.contraer(-1.0, 10, 0.0) - (-0.5)) < 1e-9
    assert st.contraer(-1.0, 5, None) == -1.0


def test_bootstrap_determinista_y_por_dia():
    ts = [_t(-1, "d1"), _t(-1, "d1"), _t(1, "d2"), _t(0.5, "d3")]
    a = st.bootstrap_por_dia(ts)
    assert a == st.bootstrap_por_dia(ts)
    assert a[0] <= a[1]
    assert st.bootstrap_por_dia([_t(1, "d1"), _t(-1, "d1")]) is None


def test_r_none_no_cuenta_como_cero():
    s = st.resumen([_t(None), _t(-1.0)])
    assert s["n"] == 1 and s["r_medio"] == -1.0
    assert st.resumen([])["r_medio"] is None


def test_n_bajo_nunca_es_propuesta():
    ts = [_t(-1.0, f"d{i}", patron="orb") for i in range(8)]
    out = st.por_segmento(ts, dimensiones=("patron",))
    seg = next(s for s in out["segmentos"] if s["valor"] == "orb")
    assert seg["estado"] == st.ESTADO_OBSERVACION


def test_muestra_grande_negativa_es_propuesta():
    ts = [_t(-1.0 if i % 4 else 0.2, f"d{i % 6}", patron="orb") for i in range(24)]
    ts += [_t(0.5, f"d{i % 6}", patron="tc") for i in range(24)]
    out = st.por_segmento(ts, dimensiones=("patron",))
    seg = {s["valor"]: s for s in out["segmentos"]}
    assert seg["orb"]["estado"] == st.ESTADO_PROPUESTA
    assert seg["tc"]["estado"] == st.ESTADO_SIN_EVIDENCIA


def test_sin_dato_va_aparte_y_nunca_se_propone():
    ts = [_t(-1.0, f"d{i % 6}") for i in range(30)]
    out = st.por_segmento(ts, dimensiones=("patron",))
    seg = out["segmentos"][0]
    assert seg["valor"] == "sin dato" and seg["estado"] == st.ESTADO_OBSERVACION


def test_bonferroni_y_solo_validos():
    ts = [_t(-1, patron="a"), _t(1, patron="b"), dict(_t(5, patron="c"), cuenta_para_aprender=False)]
    out = st.por_segmento(ts, dimensiones=("patron",))
    assert out["n_segmentos"] == 2 and abs(out["alfa_corregido"] - 0.05) < 1e-9
    assert out["global"]["n"] == 2


def test_espera_slot():
    assert st.valor_dimension({"espera_desde_disparo_min": 90}, "espera_slot") == ">=10min"
    assert st.valor_dimension({"espera_desde_disparo_min": 1}, "espera_slot") == "<10min"
    assert st.valor_dimension({}, "espera_slot") is None
