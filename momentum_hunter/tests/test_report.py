"""Pruebas de ensamblado y formato del mensaje -- `tiene_opciones_fn` se
inyecta siempre en las pruebas (nunca llama a red)."""

from __future__ import annotations

from dataclasses import replace

from momentum_hunter.alerts import Candidato
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import CONFIG
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.report import construir_oportunidad, formatear
from momentum_hunter.scoring import Puntuacion


def _candidato_breakout() -> Candidato:
    factores = FactoresMomentum(
        rvol=6.0, gap_pct=0.08, breakout_20d=True, distancia_max_52s=0.99,
        rsi=65.0, macd=1.0, macd_signal=0.4, atr=0.5,
    )
    catalizador = Catalizador(tipo="fda", titular="Company Receives FDA Approval", fuente="Reuters")
    meta = Metadata(ticker="ACME", nombre="Acme Corp", shares_float=30_000_000, short_pct_float=0.05)
    puntuacion = Puntuacion(ticker="ACME", score_total=92.0, sub={})
    return Candidato(
        ticker="ACME", nombre="Acme Corp", precio=10.0, volumen_promedio=2_000_000.0,
        factores=factores, catalizador=catalizador, meta=meta, puntuacion=puntuacion,
    )


def test_construir_oportunidad_con_opciones_disponibles():
    c = _candidato_breakout()
    o = construir_oportunidad(c, CONFIG, tiene_opciones_fn=lambda t: True)
    assert o.ticker == "ACME"
    assert o.clasificacion == "🔥 BREAKOUT"
    assert o.estrategia_nombre == "Long Call"
    assert o.stop is not None and o.stop < o.entrada
    assert o.primer_objetivo > o.entrada
    assert o.segundo_objetivo > o.primer_objetivo


def test_construir_oportunidad_sin_opciones_cae_a_acciones():
    c = _candidato_breakout()
    o = construir_oportunidad(c, CONFIG, tiene_opciones_fn=lambda t: False)
    assert o.estrategia_nombre == "Comprar acciones"


def test_construir_oportunidad_tiene_opciones_fn_que_falla_degrada_a_acciones():
    c = _candidato_breakout()

    def _falla(_ticker: str) -> bool:
        raise RuntimeError("red no disponible")

    o = construir_oportunidad(c, CONFIG, tiene_opciones_fn=_falla)
    assert o.estrategia_nombre == "Comprar acciones"


def test_formatear_incluye_los_campos_del_prompt_8():
    c = _candidato_breakout()
    o = construir_oportunidad(c, CONFIG, tiene_opciones_fn=lambda t: False)
    texto = formatear(o)
    for campo in (
        "Ticker: ACME", "Empresa: Acme Corp", "Clasificación", "Convicción",
        "Catalizador:", "Qué ocurrió:", "Por qué puede seguir subiendo:",
        "Entrada:", "Stop:", "Primer objetivo:", "Segundo objetivo:",
        "Riesgo:", "Capital mínimo", "Nivel de urgencia:", "Qué espero que ocurra:",
        "Qué invalidaría la tesis:", "Tiempo esperado:", "Crear alertas", "Plan de acción:",
    ):
        assert campo in texto, f"falta '{campo}' en el mensaje"


def test_formatear_no_operar_no_sugiere_entrar():
    c = _candidato_breakout()
    c = replace(c, puntuacion=Puntuacion(ticker="ACME", score_total=10.0, sub={}))
    o = construir_oportunidad(c, CONFIG, tiene_opciones_fn=lambda t: True)
    assert o.estrategia_nombre == "No Operar"
    texto = formatear(o)
    assert "No abrir posición hoy" in texto
