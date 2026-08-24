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
    EntradaWatchlist,
    Transicion,
    activas,
    actualizar_niveles,
    agregar_nuevas,
    cargar,
    catalizador_de,
    catalizador_vigente,
    completar_latencia_transicion,
    con_niveles_que_refrescar,
    desde_candidato_diario,
    expirar_vencidas,
    guardar,
    marcar_invalidated,
    marcar_missed,
    marcar_triggered,
    meta_de,
    parsear,
    purgar_antiguas,
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


def test_agregar_nuevas_no_reagrega_estado_terminal_de_hoy():
    # Bug real encontrado en revisión de PR (2026-08-11): evita re-vigilar
    # dos veces en la misma sesión algo que ya se resolvió hace un rato.
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    marcar_missed(e, "x", AHORA + timedelta(minutes=5))
    entradas = agregar_nuevas([e], [_candidato_diario("RKLB")], AHORA + timedelta(minutes=10))
    assert len(entradas) == 1   # no se agregó una segunda entrada


def test_agregar_nuevas_si_reagrega_estado_terminal_de_un_dia_anterior():
    # El bug real: antes de este fix, un ticker que terminaba en estado
    # terminal UNA SOLA VEZ quedaba bloqueado para siempre en
    # `watchlist.json` (que se committea y nunca se limpiaba solo) --
    # nunca volvía a vigilarse aunque hubiera pasado una semana.
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    marcar_missed(e, "x", AHORA)
    un_dia_despues = AHORA + timedelta(days=1)
    entradas = agregar_nuevas([e], [_candidato_diario("RKLB")], un_dia_despues)
    assert len(entradas) == 2
    nueva = [x for x in entradas if x.estado == ESTADO_WATCHING][0]
    assert nueva.creado_en == un_dia_despues.isoformat(timespec="seconds")


def test_agregar_nuevas_si_reagrega_expired_el_mismo_dia():
    # Bug real encontrado en revisión de PR (2026-08-11, quinta vuelta):
    # a diferencia de MISSED/INVALIDATED/TRIGGERED (una decisión real
    # sobre la candidata), EXPIRED solo dice "se venció el intento" --
    # bloquearla el resto del día apagaba la vigilancia de 5 minutos
    # justo para las candidatas que el escaneo completo seguía
    # re-descubriendo como válidas.
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    expirar_vencidas([e], minutos_maximos=60, ahora=AHORA + timedelta(minutes=61))
    assert e.estado == ESTADO_EXPIRED

    despues = AHORA + timedelta(minutes=90)   # mismo día
    entradas = agregar_nuevas([e], [_candidato_diario("RKLB")], despues)
    assert len(entradas) == 2
    nueva = [x for x in entradas if x.estado == ESTADO_WATCHING][0]
    assert nueva.creado_en == despues.isoformat(timespec="seconds")   # TTL reiniciado


def test_agregar_nuevas_no_reagrega_algo_que_sigue_watching():
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    entradas = agregar_nuevas([e], [_candidato_diario("RKLB")], AHORA + timedelta(days=10))
    assert len(entradas) == 1   # WATCHING nunca se re-agrega, sin importar cuánto tiempo pase


# ------------------------- purgar_antiguas -------------------------

def test_purgar_antiguas_descarta_terminales_viejas():
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    marcar_missed(e, "x", AHORA)
    ocho_dias_despues = AHORA + timedelta(days=8)
    assert purgar_antiguas([e], ocho_dias_despues, dias=7) == []


def test_purgar_antiguas_no_toca_terminales_recientes():
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    marcar_missed(e, "x", AHORA)
    un_dia_despues = AHORA + timedelta(days=1)
    assert purgar_antiguas([e], un_dia_despues, dias=7) == [e]


def test_purgar_antiguas_nunca_toca_watching_activas():
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    mucho_despues = AHORA + timedelta(days=365)
    assert purgar_antiguas([e], mucho_despues, dias=7) == [e]


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


def test_cargar_transicion_corrupta_se_descarta_sin_tumbar_la_corrida(tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11): antes de este
    # fix, `Transicion(**t)` vivía FUERA del try/except que solo envolvía
    # `EntradaWatchlist(...)` -- una transición mal formada (clave
    # desconocida) tumbaba TODA la carga, no solo esa entrada.
    path = tmp_path / "watchlist.json"
    buena = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    rota = asdict(desde_candidato_diario(_candidato_diario("ROTA"), AHORA))
    rota["transiciones"] = [{"estado": "watching", "clave_desconocida": "x"}]   # falta "timestamp"/"motivo"
    data = {"entradas": [asdict(buena), rota]}
    path.write_text(json.dumps(data, ensure_ascii=False))
    recargadas = cargar(path)
    assert [r.ticker for r in recargadas] == ["RKLB"]


def test_cargar_json_que_no_es_un_objeto_se_reinicia_vacia(tmp_path):
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps([1, 2, 3]))   # válido como JSON, pero no es {"entradas": [...]}
    assert cargar(path) == []


# ------------------------- parsear (integración de Telegram) -------------------------

def test_parsear_es_lo_mismo_que_usa_cargar_reusable_sin_filesystem():
    # `telegram_bot` (Render, sin filesystem local) descarga el JSON vía
    # la API de GitHub y llama `parsear` directo -- debe dar exactamente
    # el mismo resultado que `cargar` sobre un archivo real.
    e = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    data = {"entradas": [asdict(e)]}
    recargadas = parsear(data)
    assert len(recargadas) == 1
    assert recargadas[0].ticker == "RKLB"


def test_parsear_formato_inesperado_no_lanza():
    assert parsear([1, 2, 3]) == []


# ------------------------- latencia por transición (integración de Telegram) -------------------------

def test_completar_latencia_transicion_calcula_las_tres_metricas():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    marcar_missed(
        e, "x", AHORA, deteccion_ts="2026-08-11T14:00:00+00:00", evaluacion_ts="2026-08-11T14:00:01+00:00")
    completar_latencia_transicion(e, "2026-08-11T14:00:02+00:00", "2026-08-11T14:00:05+00:00")
    t = e.transiciones[-1]
    assert t.mensaje_generado_ts == "2026-08-11T14:00:02+00:00"
    assert t.telegram_enviado_ts == "2026-08-11T14:00:05+00:00"
    assert t.latencia_desde_deteccion_ms == 5000.0
    assert t.latencia_desde_evaluacion_ms == 4000.0
    assert t.latencia_desde_transicion_ms == 5000.0   # transición ocurrió en AHORA == deteccion_ts en este caso


def test_completar_latencia_transicion_sin_transiciones_no_lanza():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    e.transiciones = []
    completar_latencia_transicion(e, "2026-08-11T14:00:02+00:00", "2026-08-11T14:00:05+00:00")   # no debe lanzar


def test_expirar_vencidas_devuelve_las_recien_expiradas():
    activa = desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    ya_expirada = desde_candidato_diario(_candidato_diario("VIEJA"), AHORA)
    ya_expirada.estado = ESTADO_EXPIRED
    aun_a_tiempo = desde_candidato_diario(_candidato_diario("OK"), AHORA + timedelta(minutes=59))

    expiradas = expirar_vencidas(
        [activa, ya_expirada, aun_a_tiempo], minutos_maximos=60, ahora=AHORA + timedelta(minutes=61))
    assert [e.ticker for e in expiradas] == ["RKLB"]   # solo la que de verdad transicionó ESTA llamada


# ------------------------- niveles cacheados para /trade (integración de Telegram) -------------------------

def test_actualizar_niveles_cachea_exactamente_lo_pasado():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    actualizar_niveles(e, entrada=78.42, stop=76.90, objetivo=82.50, zona_entrada_baja=78.30, ahora=AHORA)
    assert e.ultima_entrada == 78.42
    assert e.ultimo_stop == 76.90
    assert e.ultimo_objetivo == 82.50
    assert e.ultima_zona_entrada_baja == 78.30
    assert e.ultimos_niveles_ts == "2026-08-11T14:00:00+00:00"


def test_actualizar_niveles_nunca_inventa_lo_que_no_llego():
    e = desde_candidato_diario(_candidato_diario(), AHORA)
    actualizar_niveles(e, entrada=None, stop=None, objetivo=None, zona_entrada_baja=None, ahora=AHORA)
    assert e.ultima_entrada is None and e.ultimo_stop is None and e.ultimo_objetivo is None


# ------------------------- refresco de niveles de las TRIGGERED (2026-08-24) -------------------------
# Bug real: `activas()` devuelve solo WATCHING, así que los niveles de
# una TRIGGERED quedaban congelados en el instante del disparo para
# siempre. Ver `con_niveles_que_refrescar`.

def _triggered_hace(horas: float, ticker="RKLB") -> EntradaWatchlist:
    disparo = AHORA - timedelta(hours=horas)
    e = desde_candidato_diario(_candidato_diario(ticker), disparo)
    marcar_triggered(e, "m", "d", "ev", disparo)
    return e


def test_refresca_las_triggered_recientes():
    e = _triggered_hace(0.5)
    assert con_niveles_que_refrescar([e], AHORA) == [e]


def test_no_refresca_las_triggered_viejas():
    # Las TRIGGERED se conservan 7 días: sin el tope se pedirían velas
    # para todas, cada 5 minutos, durante una semana.
    assert con_niveles_que_refrescar([_triggered_hace(30)], AHORA) == []


def test_no_refresca_estados_que_no_sean_triggered():
    # WATCHING ya lo cubre `activas()`; los otros terminales no se operan.
    watching = desde_candidato_diario(_candidato_diario("AAA"), AHORA)
    invalidada = desde_candidato_diario(_candidato_diario("BBB"), AHORA)
    marcar_invalidated(invalidada, "tesis rota", AHORA)

    assert con_niveles_que_refrescar([watching, invalidada], AHORA) == []


def test_fecha_ilegible_no_se_refresca_ni_lanza():
    e = _triggered_hace(0.5)
    e.actualizado_en = "no-es-una-fecha"
    assert con_niveles_que_refrescar([e], AHORA) == []


def test_refrescar_niveles_no_renueva_la_ventana():
    # `actualizar_niveles` no toca `actualizado_en` -- si lo tocara, una
    # TRIGGERED se refrescaría para siempre renovándose a sí misma.
    e = _triggered_hace(7.5)
    actualizar_niveles(e, 10.0, 9.0, 12.0, 10.0, AHORA)
    assert con_niveles_que_refrescar([e], AHORA + timedelta(hours=1)) == []


def test_refrescar_niveles_no_cambia_el_estado():
    # TRIGGERED es terminal: refrescar precios no reabre la decisión.
    e = _triggered_hace(0.5)
    transiciones_antes = len(e.transiciones)
    actualizar_niveles(e, 10.0, 9.0, 12.0, 10.0, AHORA)
    assert e.estado == ESTADO_TRIGGERED
    assert len(e.transiciones) == transiciones_antes


def test_refrescar_niveles_mueve_los_ultimos_pero_no_el_stop_de_la_tesis():
    e = _triggered_hace(0.5)
    actualizar_niveles(e, 10.0, 9.0, 12.0, 10.0, AHORA)
    actualizar_niveles(e, 11.0, 9.8, 13.0, 11.0, AHORA + timedelta(minutes=5))

    assert (e.ultima_entrada, e.ultimo_stop, e.ultimo_objetivo) == (11.0, 9.8, 13.0)
    assert e.stop_tesis == 9.0   # congelado en el primero, ver `actualizar_niveles`
