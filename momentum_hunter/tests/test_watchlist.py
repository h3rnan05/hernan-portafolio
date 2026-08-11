"""Pruebas del State Engine persistido -- creación, transiciones,
expiración, latencia, y que un archivo corrupto (o una entrada corrupta)
nunca tumbe la corrida ni pierda las demás entradas."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion
from momentum_hunter.watchlist import (
    ESTADO_EXPIRED,
    ESTADO_INVALIDATED,
    ESTADO_MISSED,
    ESTADO_TRIGGERED,
    ESTADO_WATCHING,
    Transicion,
    activas,
    agregar_nuevas,
    cargar,
    catalizador_de,
    catalizador_vigente,
    desde_candidato_diario,
    expirar_vencidas,
    guardar,
    marcar_invalidated,
    marcar_missed,
    marcar_triggered,
    meta_de,
    registrar_latencia,
)

AHORA = datetime(2026, 8, 11, 14, 0, 0, tzinfo=UTC)


def _candidato_diario(ticker="RKLB", con_catalizador=True) -> CandidatoDiario:
    catalizador = (
        Catalizador(tipo="contrato", titular="Rocket Lab wins contract", fuente="Reuters",
                    fecha="2026-08-11T13:45:00+00:00", fuentes_adicionales=("AP",))
        if con_catalizador else None
    )
    return CandidatoDiario(
        ticker=ticker, nombre="Rocket Lab Corp", precio=80.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=1.5), catalizador=catalizador,
        meta=Metadata(ticker=ticker, shares_float=180_000_000.0, short_pct_float=0.05),
        puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
        es_large_cap=True,
    )


def test_desde_candidato_diario_arranca_en_watching():
    c = _candidato_diario()
    e = desde_candidato_diario(c, AHORA)
    assert e.estado == ESTADO_WATCHING
    assert e.ticker == "RKLB"
    assert e.creado_en == e.actualizado_en == "2026-08-11T14:00:00+00:00"
    assert len(e.transiciones) == 1
    assert e.transiciones[0].estado == ESTADO_WATCHING
    assert e.catalizador_tipo == "contrato"
    assert e.es_large_cap is True
    assert e.score_base == 88.0
    assert e.atr_diario == 1.5


def test_catalizador_de_reconstruye_el_catalizador_congelado():
    c = _candidato_diario()
    e = desde_candidato_diario(c, AHORA)
    cat = catalizador_de(e)
    assert cat is not None
    assert cat.tipo == "contrato"
    assert cat.titular == "Rocket Lab wins contract"
    assert cat.fuentes_adicionales == ("AP",)


def test_meta_de_reconstruye_float_y_short():
    c = _candidato_diario()
    e = desde_candidato_diario(c, AHORA)
    meta = meta_de(e)
    assert meta.shares_float == 180_000_000.0
    assert meta.short_pct_float == 0.05


def test_agregar_nuevas_no_duplica_ticker_ya_en_watchlist():
    c = _candidato_diario()
    entradas = [desde_candidato_diario(c, AHORA)]
    entradas = agregar_nuevas(entradas, [c], AHORA + timedelta(minutes=30))
    assert len(entradas) == 1
    assert entradas[0].creado_en == "2026-08-11T14:00:00+00:00"   # no se reinició


def test_agregar_nuevas_agrega_tickers_nuevos():
    entradas = agregar_nuevas([], [_candidato_diario("RKLB"), _candidato_diario("TTWO")], AHORA)
    assert {e.ticker for e in entradas} == {"RKLB", "TTWO"}
    assert all(e.estado == ESTADO_WATCHING for e in entradas)


def test_activas_solo_devuelve_watching():
    e1 = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    e2 = desde_candidato_diario(_candidato_diario("TTWO"), AHORA)
    marcar_missed(e2, "Ya no está a tiempo.", AHORA)
    assert [e.ticker for e in activas([e1, e2])] == ["RKLB"]


def test_catalizador_vigente_dentro_de_la_ventana():
    e = desde_candidato_diario(_candidato_diario(), AHORA)   # catalizador de hoy mismo
    assert catalizador_vigente(e, dias_ventana=3, ahora=AHORA) is True


def test_catalizador_vigente_false_fuera_de_la_ventana():
    e = desde_candidato_diario(_candidato_diario(), AHORA)   # catalizador del 2026-08-11
    mucho_despues = AHORA + timedelta(days=10)
    assert catalizador_vigente(e, dias_ventana=3, ahora=mucho_despues) is False


def test_marcar_invalidated_y_missed():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    marcar_invalidated(e, "El catalizador ya no es válido.", AHORA + timedelta(minutes=10))
    assert e.estado == ESTADO_INVALIDATED
    assert len(e.transiciones) == 2
    assert e.transiciones[-1].motivo == "El catalizador ya no es válido."


def test_marcar_triggered_guarda_los_tres_primeros_timestamps():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    market_ts = "2026-08-11T14:05:00+00:00"
    data_ts = "2026-08-11T14:05:03+00:00"
    eval_ts = "2026-08-11T14:05:03.200000+00:00"
    marcar_triggered(e, market_ts, data_ts, eval_ts, AHORA + timedelta(minutes=5))
    assert e.estado == ESTADO_TRIGGERED
    assert e.market_event_ts == market_ts
    assert e.data_received_ts == data_ts
    assert e.evaluador_ts == eval_ts
    assert e.signal_latency_ms is None   # todavía no se registró la latencia completa


def test_registrar_latencia_calcula_desde_el_evento_de_mercado():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    marcar_triggered(
        e, "2026-08-11T14:05:00+00:00", "2026-08-11T14:05:03+00:00",
        "2026-08-11T14:05:03.200000+00:00", AHORA,
    )
    registrar_latencia(e, "2026-08-11T14:05:03.400000+00:00", "2026-08-11T14:05:04+00:00")
    assert e.signal_latency_ms == 4000.0   # 4 segundos desde la vela hasta Telegram


def test_registrar_latencia_no_falla_sin_market_event_ts():
    e = desde_candidato_diario(_candidato_diario(), AHORA)   # nunca se marcó triggered
    registrar_latencia(e, "2026-08-11T14:05:03.400000+00:00", "2026-08-11T14:05:04+00:00")
    assert e.signal_latency_ms is None


def test_expirar_vencidas_transiciona_las_que_llevan_demasiado():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    expirar_vencidas([e], minutos_maximos=60, ahora=AHORA + timedelta(minutes=61))
    assert e.estado == ESTADO_EXPIRED


def test_expirar_vencidas_no_toca_las_que_todavia_tienen_tiempo():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    expirar_vencidas([e], minutos_maximos=60, ahora=AHORA + timedelta(minutes=30))
    assert e.estado == ESTADO_WATCHING


def test_expirar_vencidas_no_toca_estados_terminales():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    marcar_missed(e, "x", AHORA)
    expirar_vencidas([e], minutos_maximos=60, ahora=AHORA + timedelta(minutes=120))
    assert e.estado == ESTADO_MISSED   # no se reescribe a EXPIRED encima


# ------------------------- persistencia (sobrevive un "reinicio") -------------------------

def test_guardar_y_cargar_roundtrip(tmp_path):
    path = tmp_path / "watchlist.json"
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    marcar_triggered(
        e, "2026-08-11T14:05:00+00:00", "2026-08-11T14:05:03+00:00",
        "2026-08-11T14:05:03.200000+00:00", AHORA + timedelta(minutes=5),
    )
    registrar_latencia(e, "2026-08-11T14:05:03.400000+00:00", "2026-08-11T14:05:04+00:00")
    guardar([e], path)

    recargadas = cargar(path)
    assert len(recargadas) == 1
    r = recargadas[0]
    assert r.ticker == "RKLB"
    assert r.estado == ESTADO_TRIGGERED
    assert r.signal_latency_ms == 4000.0
    assert len(r.transiciones) == 2
    assert all(isinstance(t, Transicion) for t in r.transiciones)
    assert r.catalizador_fuentes_adicionales == ("AP",)


def test_cargar_archivo_inexistente_devuelve_lista_vacia(tmp_path):
    assert cargar(tmp_path / "no_existe.json") == []


def test_cargar_archivo_corrupto_no_lanza(tmp_path):
    path = tmp_path / "watchlist.json"
    path.write_text("{esto no es json")
    assert cargar(path) == []


def test_cargar_entrada_individual_corrupta_se_descarta_sin_perder_las_demas(tmp_path):
    path = tmp_path / "watchlist.json"
    buena = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    data = {"entradas": [
        asdict(buena),
        {"ticker": "ROTA", "estado": "watching"},   # le faltan campos obligatorios
    ]}
    path.write_text(json.dumps(data, ensure_ascii=False))
    recargadas = cargar(path)
    assert len(recargadas) == 1
    assert recargadas[0].ticker == "RKLB"
