"""Pruebas del Market Radar -- agrupa lo que quedó "cerca" pero no
accionable, nunca incluye lo que se detuvo en el paso 1 (sin
catalizador) ni lo que ya se mandó como alerta de entrada."""

from __future__ import annotations

from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata
from momentum_hunter.radar import TOPE_RADAR_TICKERS, candidatos_para_radar, construir_resumen


def _bi(ticker) -> BarraIntradia:
    return BarraIntradia(ticker, ["2026-07-26T13:30:00+00:00"], [1.0], [1.0], [1.0], [1.0], [100.0])


def _candidato(ticker, paso_detenido=None, accionable=False, dinero_entrando=False,
              patron=None, temprano=True) -> CandidatoIntradia:
    resultado = ResultadoEvaluacion(
        paso_detenido=paso_detenido, dinero_entrando=dinero_entrando, desequilibrio=False,
        patron=patron, temprano=temprano, early=None, penalizaciones=[],
        score_base=50.0, score_ajustado=50.0, accionable=accionable,
    )
    catalizador = None if paso_detenido == "catalizador" else Catalizador(tipo="fda", titular="x", fuente="R")
    return CandidatoIntradia(
        ticker=ticker, nombre=None, catalizador=catalizador, minutos_desde_catalizador=None,
        factores=FactoresIntradia(), bi_hoy=_bi(ticker), meta=Metadata(ticker=ticker),
        atr_diario=None, resultado=resultado,
    )


def test_candidatos_para_radar_excluye_sin_catalizador():
    sin_catalizador = _candidato("SIN", paso_detenido="catalizador")
    con_patron = _candidato("CON", patron="micro_pullback")
    resultado = candidatos_para_radar([sin_catalizador, con_patron])
    assert [c.ticker for c in resultado] == ["CON"]


def test_candidatos_para_radar_excluye_accionables():
    accionable = _candidato("YA_ALERTADO", patron="bull_flag", accionable=True)
    resultado = candidatos_para_radar([accionable])
    assert resultado == []


def test_candidatos_para_radar_excluye_sin_señal_alguna():
    nada = _candidato("NADA", dinero_entrando=False, patron=None)
    assert candidatos_para_radar([nada]) == []


def test_construir_resumen_none_si_no_hay_nada():
    assert construir_resumen([]) is None


def test_construir_resumen_intro_sin_alertas_hoy():
    c = _candidato("T0", patron="micro_pullback")
    resumen = construir_resumen([c])
    assert "No encontré ninguna oportunidad con suficiente convicción para abrir una posición todavía." in resumen


def test_construir_resumen_intro_cuando_ya_hubo_alerta():
    # elegidas no vacío significa que esta misma corrida ya mandó una
    # alerta de entrada -- el radar es un extra, no el mensaje principal.
    ganadora = _candidato("GANA", patron="gap_and_go", accionable=True)
    otro = _candidato("T0", patron="micro_pullback")
    resumen = construir_resumen([ganadora, otro], elegidas={"GANA"})
    assert "Además de la alerta que te acabo de mandar, esto es lo que sigo vigilando:" in resumen


def test_construir_resumen_bloque_con_patron_dice_que_espera():
    c = _candidato("NOK", patron="trend_continuation", dinero_entrando=True)
    resumen = construir_resumen([c])
    assert "🔥 NOK" in resumen
    assert "Está subiendo de forma constante." in resumen
    assert "Lo que estoy esperando:" in resumen
    assert "• " in resumen
    assert "Si se confirma, te aviso automáticamente." in resumen
    # Lenguaje humano (pivote 2026-07-26) -- nunca el nombre técnico del patrón.
    assert "TREND_CONTINUATION" not in resumen.upper()


def test_construir_resumen_bloque_sin_patron_dice_que_espera():
    c = _candidato("VOL", dinero_entrando=True, patron=None)
    resumen = construir_resumen([c])
    assert "👀 VOL" in resumen
    assert "Hay dinero entrando, pero todavía no veo una forma clara de entrada." in resumen
    assert "Lo que estoy esperando:" in resumen
    assert "Que el precio forme una de las seis figuras que sé operar" in resumen


def test_construir_resumen_limita_a_tope_radar_tickers():
    candidatos = [_candidato(f"T{i}", patron="micro_pullback", dinero_entrando=True) for i in range(TOPE_RADAR_TICKERS + 2)]
    resumen = construir_resumen(candidatos)
    incluidos = sum(1 for c in candidatos if f"🔥 {c.ticker}" in resumen)
    assert incluidos == TOPE_RADAR_TICKERS


def test_construir_resumen_marca_oportunidades_tarde():
    c = _candidato("TARDE", patron="bull_flag", temprano=False)
    resumen = construir_resumen([c])
    assert "ya se movió demasiado" in resumen
    assert "TARDE" in resumen
    assert "BULL FLAG" not in resumen.upper()
    # "Tarde" no trae lista de "lo que estoy esperando" -- no hay nada
    # que esperar, ya corrió sin nosotros.
    assert "Lo que estoy esperando" not in resumen


def test_subcampeona_aparece_con_su_lugar_en_la_fila():
    # Accionable pero no elegida (perdió la competencia relativa) -- no
    # desaparece en silencio (Principio 7).
    ganadora = _candidato("GANA", patron="gap_and_go", accionable=True)
    subcampeona = _candidato("PLATA", patron="bull_flag", accionable=True)
    resumen = construir_resumen([ganadora, subcampeona], elegidas={"GANA"})
    assert "🥈 PLATA" in resumen
    assert "no fue la mejor" in resumen
    assert "GANA" not in [ln.split()[1].rstrip(":") for ln in resumen.splitlines() if ln.startswith("🥈")]


def test_vetada_aparece_con_su_motivo_exacto():
    vetada = _candidato("VETO", patron="gap_and_go", accionable=True)
    resumen = construir_resumen(
        [vetada], elegidas=set(),
        vetadas={"VETO": "El dinero está dejando de entrar."},
    )
    assert "⛔ VETO" in resumen
    assert "El dinero está dejando de entrar." in resumen


def test_elegida_no_aparece_en_el_radar():
    ganadora = _candidato("GANA", patron="gap_and_go", accionable=True)
    assert construir_resumen([ganadora], elegidas={"GANA"}) is None
