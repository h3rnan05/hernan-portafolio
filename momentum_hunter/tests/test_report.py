"""Pruebas del mensaje en lenguaje humano (pivote 2026-07-26) --
verifica que NINGÚN indicador crudo (RVOL/EMA9/VWAP/ATR/MACD/RSI) ni
nombre de patrón en jerga aparezca en el texto final, que las 4
preguntas de la narrativa estén presentes, y que el mensaje sea corto."""

from __future__ import annotations

from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import CONFIG
from momentum_hunter.early_opportunity import EarlyOpportunity
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata
from momentum_hunter.report import construir_oportunidad, formatear, niveles_entrada_salida

# Palabras que NUNCA deben aparecer en un mensaje enviado a Telegram --
# la prueba central del pivote ("el usuario nunca debería sentir que
# necesita saber análisis técnico").
JERGA_PROHIBIDA = ["RVOL", "EMA9", "VWAP", "ATR", "MACD", "RSI"]


def _bi() -> BarraIntradia:
    return BarraIntradia("ACME", ["2026-07-26T13:33:00+00:00"], [5.20], [5.20], [5.25], [5.15], [5_000.0])


def _candidato(patron="gap_and_go", temprano=True, razon="ok", accionable=True) -> CandidatoIntradia:
    factores = FactoresIntradia(
        precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=4.0, gap_pct=0.10,
        aceleracion_volumen=1.5, maximo_premarket=5.00, maximo_dia=5.25, velas_desde_ruptura=1,
    )
    early = EarlyOpportunity(
        score=90.0, veredicto="temprano" if temprano else "tarde", razon=razon,
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
        meta=Metadata(ticker="ACME", shares_float=12_000_000), atr_diario=0.30, resultado=resultado,
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


def test_construir_oportunidad_guarda_materia_prima_para_aprendizaje():
    c = _candidato(patron="gap_and_go")
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    # No se muestra en el mensaje, pero debe quedar disponible para stats.py.
    assert o.patron_clave == "gap_and_go"
    assert o.catalizador_tipo == "fda"
    assert o.float_acciones == 12_000_000
    assert o.gap_pct == 0.10
    assert o.rvol == 4.0
    assert 0 <= o.hora_utc <= 23


def test_construir_oportunidad_urgencia_muy_alta_con_patron_recien_activado():
    c = _candidato(patron="gap_and_go", temprano=True)
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert o.urgencia == "Muy Alta"   # velas_desde_ruptura=1


def test_construir_oportunidad_no_vale_la_pena_si_no_temprano():
    c = _candidato(patron="gap_and_go", temprano=False, razon="extension")
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert o.vale_la_pena is False
    assert o.urgencia == "Baja"
    assert "Ya subió demasiado rápido" in o.por_que_vale_la_pena


def test_que_paso_menciona_minutos_reales():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert "Hace 12 min" in o.que_paso


def test_formatear_responde_las_cuatro_preguntas_de_la_narrativa():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    texto = formatear(o)
    assert "1)" in texto and "2)" in texto and "3)" in texto
    assert "¿Todavía vale la pena?" in texto


def test_formatear_incluye_por_que_esta_alerta_niveles_e_invalidacion():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    texto = formatear(o)
    assert "oportunidad" in texto.lower() or "exijo" in texto.lower()  # "por qué esta alerta"
    assert "Si decides entrar" in texto
    assert "se cancela la idea" in texto
    assert 'Fuente: "Company Receives FDA Approval" (Reuters)' in texto


def test_formatear_nunca_muestra_jerga_de_indicadores():
    """El corazón del pivote: ningún indicador crudo llega al usuario."""
    for patron in ("gap_and_go", "opening_range_breakout", "bull_flag",
                   "micro_pullback", "high_tight_flag", "trend_continuation"):
        c = _candidato(patron=patron)
        o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
        texto = formatear(o)
        for palabra in JERGA_PROHIBIDA:
            assert palabra not in texto, f"'{palabra}' apareció en el mensaje de {patron}"


def test_formatear_nunca_muestra_el_nombre_tecnico_del_patron():
    c = _candidato(patron="high_tight_flag")
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    texto = formatear(o)
    assert "high tight flag" not in texto.lower()


def test_formatear_se_lee_corto():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    texto = formatear(o)
    # "Debe poder leerse en menos de 15 segundos" -- límite generoso pero
    # mucho más chico que el formato de reporte de antes del pivote.
    assert len(texto) < 800


# ------ Pedido 2026-07-27: probabilidades, advertencias, competencia ------

def test_formatear_incluye_probabilidad_historica_cuando_existe():
    c = _candidato()
    o = construir_oportunidad(
        c, CONFIG.velas_maximas_desde_patron,
        probabilidad_historica="Jugadas como esta me han funcionado 62% de las veces (13 casos medidos).",
    )
    assert "62% de las veces" in formatear(o)


def test_formatear_incluye_que_podria_salir_mal():
    c = _candidato()
    o = construir_oportunidad(
        c, CONFIG.velas_maximas_desde_patron,
        advertencias=["Queda menos de una hora de mercado."],
    )
    texto = formatear(o)
    assert "Qué podría salir mal:" in texto
    assert "menos de una hora" in texto


def test_formatear_sin_advertencias_omite_la_seccion():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert "Qué podría salir mal:" not in formatear(o)


def test_por_que_esta_alerta_cita_la_competencia_real():
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron, n_evaluados=17)
    assert "17 candidatas" in o.por_que_esta_alerta
    assert "sobrevivió" in o.por_que_esta_alerta


def test_formatear_cierra_con_la_regla_inquebrantable():
    # "La decisión final de ejecutar una operación siempre la toma el
    # humano" -- el mensaje lo dice en cada alerta, no solo en el README.
    c = _candidato()
    o = construir_oportunidad(c, CONFIG.velas_maximas_desde_patron)
    assert "La decisión de operar siempre es tuya" in formatear(o)
