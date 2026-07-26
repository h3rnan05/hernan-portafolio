"""Pruebas del nuevo formato de mensaje (Prompts 3/5/6/7) -- verifica
niveles de entrada/salida, la secuencia "por qué apareció" (máximo 5
líneas, todas trazables a datos reales), la urgencia calculada, y que
las 7 preguntas del Prompt 3 estén presentes en el texto final."""

from __future__ import annotations

from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import CONFIG
from momentum_hunter.early_opportunity import EarlyOpportunity
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata
from momentum_hunter.report import construir_oportunidad, formatear, niveles_entrada_salida


def _bi() -> BarraIntradia:
    return BarraIntradia("ACME", ["2026-07-26T13:33:00+00:00"], [5.20], [5.20], [5.25], [5.15], [5_000.0])


def _candidato(patron="gap_and_go", temprano=True, accionable=True) -> CandidatoIntradia:
    factores = FactoresIntradia(
        precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=4.0, gap_pct=0.10,
        maximo_premarket=5.00, maximo_dia=5.25, velas_desde_ruptura=1,
    )
    early = EarlyOpportunity(
        score=90.0, veredicto="temprano" if temprano else "tarde",
        motivo_veredicto="El precio sigue cerca de sus anclas de corto plazo.",
    )
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True, patron=patron,
        temprano=temprano, early=early, penalizaciones=[], score_base=95.0,
        score_ajustado=95.0, accionable=accionable,
    )
    return CandidatoIntradia(
        ticker="ACME", nombre="Acme Corp",
        catalizador=Catalizador(tipo="fda", titular="Company Receives FDA Approval", fuente="Reuters"),
        minutos_desde_catalizador=12.0, factores=factores, bi_hoy=_bi(),
        meta=Metadata(ticker="ACME"), atr_diario=0.30, resultado=resultado,
    )


def test_niveles_usa_el_ancla_mas_cercana_por_debajo():
    f = FactoresIntradia(precio_actual=10.0, vwap=9.5, ema9=9.8)
    niveles = niveles_entrada_salida(f, atr_diario=None)
    assert niveles["entrada"] == 10.0
    assert niveles["stop"] == 9.8 * 0.995
    assert niveles["objetivo"] > niveles["entrada"]


def test_niveles_cae_a_atr_diario_sin_anclas():
    f = FactoresIntradia(precio_actual=10.0)
    niveles = niveles_entrada_salida(f, atr_diario=1.0)
    assert niveles["stop"] == 9.5


def test_niveles_none_sin_precio():
    niveles = niveles_entrada_salida(FactoresIntradia(), atr_diario=None)
    assert niveles == {"entrada": None, "stop": None, "objetivo": None}


def test_construir_oportunidad_clasificacion_y_urgencia():
    c = _candidato(patron="gap_and_go", temprano=True)
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert o.ticker == "ACME"
    assert o.patron == "🚀 GAP AND GO"
    assert o.veredicto_temprano is True
    assert o.urgencia == "Muy Alta"   # velas_desde_ruptura=1


def test_construir_oportunidad_no_temprano_es_urgencia_baja():
    c = _candidato(patron="gap_and_go", temprano=False)
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert o.urgencia == "Baja"


def test_por_que_aparecio_maximo_cinco_lineas_y_menciona_minutos():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert len(o.por_que_aparecio) <= 5
    assert any("Hace 12 min" in linea for linea in o.por_que_aparecio)


def test_formatear_incluye_las_siete_preguntas_del_prompt_3():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    texto = formatear(o)
    assert "ACME" in texto
    assert "Patrón:" in texto
    assert "Vamos temprano" in texto
    assert "Entro:" in texto
    assert "Salgo (stop):" in texto
    assert "Objetivo:" in texto
    assert "Qué invalida esto:" in texto
    assert "Qué espero:" in texto


def test_formatear_se_lee_corto():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    texto = formatear(o)
    # Prompt 3: "cada alerta debe poder leerse en menos de 20 segundos" --
    # un límite generoso mucho menor que el formato anterior tipo reporte.
    assert len(texto) < 900
