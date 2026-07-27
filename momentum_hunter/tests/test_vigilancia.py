"""Pruebas del seguimiento post-alerta -- sin red: barras inyectadas.
Verifica la prioridad de estados (stop antes que objetivo, lectura
conservadora), que solo se avisa cuando el estado CAMBIA, y que las
velas previas a la alerta no cuentan."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_hunter.config import MomentumConfig
from momentum_hunter.factors import intradia as fi
from momentum_hunter.models import BarraIntradia
from momentum_hunter.tracker import AlertaRegistrada
from momentum_hunter.vigilancia import evaluar_estado, vigilar

CFG = MomentumConfig()
AHORA = datetime(2026, 7, 27, 15, 0, tzinfo=UTC)


def _bi(closes, highs=None, lows=None, vols=None, hora_inicio=13, ticker="ACME") -> BarraIntradia:
    n = len(closes)
    highs = highs or [c * 1.001 for c in closes]
    lows = lows or [c * 0.999 for c in closes]
    vols = vols or [10_000.0] * n
    marcas = [f"2026-07-27T{hora_inicio + i // 60:02d}:{30 + i % 30:02d}:00+00:00" for i in range(n)]
    return BarraIntradia(ticker, marcas, closes, closes, highs, lows, vols)


def _alerta(ticker="ACME", stop=5.00, objetivo=5.60, fecha="2026-07-27T13:35:00+00:00",
           ultimo_estado=None) -> AlertaRegistrada:
    return AlertaRegistrada(
        id="a1", ticker=ticker, fecha=fecha, precio_entrada=5.20, stop=stop,
        objetivo1=objetivo, objetivo2=None, clasificacion="gap_and_go", estrategia="",
        score=95.0, ultimo_estado=ultimo_estado,
    )


def test_rompio_stop_gana_sobre_objetivo_lectura_conservadora():
    # Después de la alerta el precio tocó AMBOS niveles -- se asume stop primero.
    closes = [5.20] * 10
    highs = [5.20] * 5 + [5.70] * 5   # tocó objetivo
    lows = [5.20] * 5 + [4.90] * 5    # y tocó stop
    bi = _bi(closes, highs=highs, lows=lows)
    factores = fi.calcular(bi)
    assert evaluar_estado(_alerta(), bi, factores) == "rompio_stop"


def test_alcanzo_objetivo():
    closes = [5.20] * 5 + [5.50] * 5
    highs = [5.25] * 5 + [5.65] * 5
    bi = _bi(closes, highs=highs)
    factores = fi.calcular(bi)
    assert evaluar_estado(_alerta(), bi, factores) == "alcanzo_objetivo"


def test_velas_previas_a_la_alerta_no_cuentan():
    # El high de la mañana (antes de la alerta de las 13:35) superó el
    # objetivo -- eso NO es "alcanzó objetivo".
    closes = [5.20] * 10
    highs = [5.90] * 4 + [5.25] * 6   # el pico fue ANTES de la alerta
    bi = _bi(closes, highs=highs)     # velas desde 13:30; alerta 13:35 -> las 4 primeras quedan fuera
    factores = fi.calcular(bi)
    assert evaluar_estado(_alerta(), bi, factores) != "alcanzo_objetivo"


def test_sigue_valida_cuando_nada_paso():
    closes = [5.20] * 10
    vols = [10_000.0] * 10
    bi = _bi(closes, vols=vols)
    factores = fi.calcular(bi)
    assert evaluar_estado(_alerta(), bi, factores) in ("sigue_valida", "debilitandose")


def test_vigilar_avisa_solo_en_cambios_de_estado():
    closes = [5.20] * 5 + [5.50] * 5
    highs = [5.25] * 5 + [5.65] * 5
    bi = _bi(closes, highs=highs)
    a = _alerta(ultimo_estado="sigue_valida")
    mensajes = vigilar([a], provider=None, cfg=CFG, ahora=AHORA, barras_intradia={"ACME": bi})
    assert len(mensajes) == 1
    assert "ACME" in mensajes[0]
    assert a.ultimo_estado == "alcanzo_objetivo"


def test_vigilar_primera_lectura_sana_no_avisa():
    bi = _bi([5.20] * 10)
    a = _alerta(ultimo_estado=None)
    mensajes = vigilar([a], provider=None, cfg=CFG, ahora=AHORA, barras_intradia={"ACME": bi})
    assert mensajes == []
    assert a.ultimo_estado is not None   # el estado sí se registró, solo no se avisó


def test_vigilar_ignora_estados_terminales():
    a = _alerta(ultimo_estado="rompio_stop")
    mensajes = vigilar([a], provider=None, cfg=CFG, ahora=AHORA, barras_intradia={})
    assert mensajes == []


def test_vigilar_ignora_alertas_de_otros_dias():
    a = _alerta(fecha="2026-07-20T13:35:00+00:00")
    mensajes = vigilar([a], provider=None, cfg=CFG, ahora=AHORA, barras_intradia={})
    assert mensajes == []


def test_vigilar_sin_cambio_no_repite_aviso():
    closes = [5.20] * 5 + [5.50] * 5
    highs = [5.25] * 5 + [5.65] * 5
    bi = _bi(closes, highs=highs)
    a = _alerta(ultimo_estado="alcanzo_objetivo")
    # alcanzo_objetivo es terminal -- ni siquiera se vuelve a evaluar.
    mensajes = vigilar([a], provider=None, cfg=CFG, ahora=AHORA, barras_intradia={"ACME": bi})
    assert mensajes == []
