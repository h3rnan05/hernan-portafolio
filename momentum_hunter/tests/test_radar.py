"""Pruebas del Market Radar -- agrupa lo que quedó "cerca" pero no
accionable, nunca incluye lo que se detuvo en el paso 1 (sin
catalizador) ni lo que ya se mandó como alerta de entrada."""

from __future__ import annotations

from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata
from momentum_hunter.radar import candidatos_para_radar, construir_resumen


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


def test_construir_resumen_agrupa_por_patron():
    dos_pullback = [_candidato(f"T{i}", patron="micro_pullback") for i in range(2)]
    resumen = construir_resumen(dos_pullback)
    assert resumen is not None
    # Lenguaje humano (pivote 2026-07-26) -- nunca el nombre técnico del patrón.
    assert "2 acciones están recuperando tras un respiro corto: T0, T1." in resumen
    assert "MICRO PULLBACK" not in resumen.upper()


def test_construir_resumen_incluye_volumen_sin_patron():
    c = _candidato("VOL", dinero_entrando=True, patron=None)
    resumen = construir_resumen([c])
    assert "sin nada claro que operar" in resumen
    assert "VOL" in resumen


def test_construir_resumen_marca_oportunidades_tarde():
    c = _candidato("TARDE", patron="bull_flag", temprano=False)
    resumen = construir_resumen([c])
    assert "ya se movió demasiado" in resumen
    assert "TARDE" in resumen
    assert "BULL FLAG" not in resumen.upper()


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
