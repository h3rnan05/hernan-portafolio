"""Motor de auto-endurecimiento (PR-D): allowlist, solo aprieta, vence, sombra."""
from datetime import UTC, datetime

import pytest

from momentum_paper_trader import autoajuste as aa
from momentum_paper_trader import config as cfgmod
from momentum_paper_trader import ia_decision


@pytest.fixture
def cotas():
    return aa.cargar_cotas()


def test_base_de_las_cotas_coincide_con_el_codigo(cotas):
    assert cotas["base"]["maximo_posiciones_abiertas"] == cfgmod.PaperTraderConfig().maximo_posiciones_abiertas
    assert cotas["base"]["confianza_minima_entrada"] == ia_decision.CONFIANZA_MINIMA_ENTRADA
    assert cotas["modo"] == "sombra"


def test_rechaza_aflojar(cotas):
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": aa.K3, "valor": 6}, cotas)
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": aa.K3, "valor": 5}, cotas)
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": aa.K3, "valor": 2}, cotas)  # debajo del piso firmado
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": aa.K2, "valor": 5, "segmento": {"dimension": "patron", "valor": "x"}}, cotas)
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": aa.K2, "valor": 9, "segmento": {"dimension": "patron", "valor": "x"}}, cotas)
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": aa.K2, "valor": 7}, cotas)  # nunca global
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": aa.K4, "valor": 120}, cotas)
    assert aa.validar({"knob": aa.K3, "valor": 3}, cotas)


def test_rechaza_knob_fuera_de_allowlist(cotas, tmp_path):
    with pytest.raises(aa.AjusteRechazado):
        aa.validar({"knob": "riesgo_dolares_por_operacion", "valor": 50}, cotas)
    p = tmp_path / "c.yaml"
    p.write_text("version: 1\nmodo: sombra\nbase: {maximo_posiciones_abiertas: 5, confianza_minima_entrada: 6}\n"
                 "knobs: {keywords: {}}\n")
    with pytest.raises(aa.CotasInvalidas):
        aa.cargar_cotas(p)


def test_cotas_corruptas_dan_none_y_problema(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(": : :\n- [")
    c, prob = aa.cotas_o_none(p)
    assert c is None and "cotas ilegibles" in prob
    assert aa.efectivos([], None, None)["max_posiciones"] is None


def test_modo_distinto_de_sombra_se_trata_como_sombra(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("version: 1\nmodo: enforce\nbase: {maximo_posiciones_abiertas: 5, confianza_minima_entrada: 6}\n"
                 "knobs: {K3_max_posiciones: {valor: 3, piso: 3, duracion_sesiones: 1}}\n")
    assert aa.cargar_cotas(p)["modo"] == "sombra"


def test_vencimiento_y_renovacion():
    assert aa.sumar_sesiones("2026-10-01", 1) == "2026-10-02"
    assert aa.sumar_sesiones("2026-10-02", 1) == "2026-10-05"
    prev = [{"knob": aa.K3, "valor": 3, "desde": "2026-09-30", "vence": "2026-10-01"},
            {"knob": aa.K4, "valor": 60, "desde": "2026-09-30", "vence": "2026-10-10"}]
    vig, ven = aa.combinar(prev, [], "2026-10-01")
    assert [a["knob"] for a in vig] == [aa.K4] and ven[0]["estado"] == "vencido"
    nuevo = {"knob": aa.K3, "valor": 3, "desde": "2026-10-01", "vence": "2026-10-02", "segmento": None}
    vig, ven = aa.combinar(prev, [nuevo], "2026-10-01")
    assert {a["knob"] for a in vig} == {aa.K3, aa.K4} and not ven


def test_proponer_sin_muestra_no_propone_nada(cotas):
    stats = {"segmentos": [{"dimension": "patron", "valor": "orb", "n": 8, "sesiones": 4, "r_shr": -0.8,
                            "estado": "observacion"}]}
    assert aa.proponer(stats, [{"r": -1, "cuenta_para_aprender": True}] * 8, None, cotas, "2026-10-01") == []


def test_proponer_k1_k3_k4(cotas):
    trades = [{"r": -1, "cuenta_para_aprender": True}] * 100
    stats = {"segmentos": [
        {"dimension": "patron", "valor": "orb", "n": 25, "sesiones": 6, "r_shr": -0.6, "estado": "propuesta"},
        {"dimension": "espera_slot", "valor": ">=10min", "n": 9, "sesiones": 5, "r_shr": -0.5, "estado": "observacion"}]}
    reg = {"nivel": "CAUTELA", "racha": {"disparada": True, "motivos": ["3 trades perdedores seguidos"]},
           "acciones_sombra": {"sin_small_caps": True}}
    out = aa.proponer(stats, trades, reg, cotas, "2026-10-01")
    knobs = {a["knob"]: a for a in out}
    assert set(knobs) == {aa.K1, aa.K3, aa.K4}
    assert knobs[aa.K3]["valor"] == 3 and knobs[aa.K4]["valor"] == 60
    assert all(a["estado"] == "sombra" for a in out)


def test_k2_exige_calibracion(cotas):
    seg = {"dimension": "patron", "valor": "tc", "n": 20, "sesiones": 5, "r_shr": -0.3, "estado": "observacion"}
    malos7 = [{"patron": "tc", "confianza": 7, "r": -1.0, "cuenta_para_aprender": True}] * 10
    buenos6 = [{"patron": "tc", "confianza": 6, "r": 0.5, "cuenta_para_aprender": True}] * 10
    assert aa.proponer({"segmentos": [seg]}, malos7 + buenos6, None, cotas, "2026-10-01") == []
    buenos7 = [dict(t, r=0.8) for t in malos7]
    malos6 = [dict(t, r=-0.9) for t in buenos6]
    out = aa.proponer({"segmentos": [seg]}, buenos7 + malos6, None, cotas, "2026-10-01")
    assert out and out[0]["knob"] == aa.K2 and out[0]["valor"] == 7


def test_efectivos_lo_mas_restrictivo_gana(cotas):
    ajustes = [{"knob": aa.K3, "valor": 3}]
    ef = aa.efectivos(ajustes, {"nivel": "DEFENSIVO", "acciones_sombra": {"max_posiciones": 2}}, cotas)
    assert ef["max_posiciones"] == 2 and ef["modo"] == "sombra"
    assert aa.efectivos([], None, cotas)["max_posiciones"] == 5


def test_gates_y_fail_closed():
    ef = {"max_posiciones": 3, "sin_small_caps": True, "edad_max_min": 60,
          "segmentos_saltados": [{"dimension": "patron", "valor": "orb"}]}
    b = aa.evaluar_gates({"n_posiciones": 3, "es_large_cap": None, "espera_desde_disparo_min": None,
                          "patron": "orb"}, ef)
    assert {x["knob"] for x in b} == {aa.K3, aa.K5, aa.K4, aa.K1}
    assert aa.evaluar_gates({"n_posiciones": 1, "es_large_cap": True, "espera_desde_disparo_min": 5,
                             "patron": "tc"}, ef) == []
    assert any("fail-closed" in x["motivo"] for x in aa.evaluar_gates({}, {"max_posiciones": 3}))


def test_guardar_y_cargar(tmp_path):
    p = tmp_path / "a.json"
    aa.guardar(p, [{"knob": aa.K3}], [{"knob": aa.K4, "estado": "vencido"}], datetime(2026, 10, 1, tzinfo=UTC))
    d = aa.cargar(p)
    assert d["modo"] == "sombra" and d["ajustes"][0]["knob"] == aa.K3 and d["historial"][0]["knob"] == aa.K4
    p.write_text("{x")
    assert aa.cargar(p) is None
