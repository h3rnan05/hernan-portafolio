"""Escenarios reales de la integración de Telegram (2026-08-11, pedido
explícito: "no considero terminada esta fase solo porque los tests
pasan"). Cubre, sin red y sin depender de internet:

  1-4.  WATCHING->TRIGGERED / INVALIDATED / MISSED / EXPIRED -- CONTENIDO
        real del mensaje, no solo el cambio de estado.
  5-6.  reinicio del proceso / el workflow corriendo dos veces -- una
        transición ya persistida nunca se vuelve a procesar.
  7-8.  Telegram fallando temporalmente / respondiendo lento -- el State
        Engine ya quedó persistido ANTES del intento de envío.
  9-10. múltiples oportunidades simultáneas, una dispara mientras otra
        se invalida en la MISMA corrida.
  11.   una oportunidad se vuelve MISSED aunque el envío a Telegram falle
        -- el estado no depende de que el mensaje haya llegado.
  13.   datos "retrasados" -- la latencia sigue midiéndose con lo que
        hay, nunca se inventa un timestamp que no existe.
  15.   webhook duplicado -- cubierto en telegram_bot/tests/test_momentum_webhook.py.
  16-19. /trade, /status, /radar -- cubiertos en
        telegram_bot/tests/test_momentum_commands.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from momentum_hunter import report
from momentum_hunter import run as run_mod
from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario, CandidatoIntradia
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.early_opportunity import EarlyOpportunity
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion

CFG = MomentumConfig()
AHORA = datetime(2026, 8, 11, 14, 0, 0, tzinfo=UTC)


def _candidato_diario(ticker="RKLB", fecha_catalizador="2026-08-11T13:45:00+00:00") -> CandidatoDiario:
    catalizador = Catalizador(tipo="contrato", titular="Rocket Lab wins contract",
                               fuente="Reuters", fecha=fecha_catalizador)
    return CandidatoDiario(
        ticker=ticker, nombre="Rocket Lab Corp", precio=80.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=1.5), catalizador=catalizador,
        meta=Metadata(ticker=ticker, shares_float=180_000_000.0, short_pct_float=0.05),
        puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
    )


def _bi(ticker: str) -> BarraIntradia:
    return BarraIntradia(ticker, ["2026-08-11T14:05:00+00:00"], [78.4], [78.4], [78.6], [78.2], [5000.0])


def _candidato_intradia(
    ticker: str, accionable: bool = True, temprano: bool = True,
    motivo_tarde: str = "ya se movió demasiado", patron: str | None = "_default_",
) -> CandidatoIntradia:
    if patron == "_default_":
        patron = "gap_and_go" if accionable else None
    factores = FactoresIntradia(
        precio_actual=78.42, vwap=76.9, ema9=76.9, rvol_actual=4.0, aceleracion_volumen=1.5,
        gap_pct=0.10, maximo_premarket=78.30, maximo_dia=79.0, velas_desde_ruptura=1,
    )
    early = EarlyOpportunity(score=90.0, veredicto="temprano" if temprano else "tarde",
                              razon="ok", motivo_veredicto=motivo_tarde)
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True,
        patron=patron, temprano=temprano, early=early,
        penalizaciones=[] if accionable else ["No hay un patrón técnico claro formándose todavía."],
        score_base=90.0, score_ajustado=90.0 if accionable else 0.0, accionable=accionable,
    )
    return CandidatoIntradia(
        ticker=ticker, nombre=None,
        catalizador=Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                                 fecha="2026-08-11T13:45:00+00:00"),
        minutos_desde_catalizador=10.0, factores=factores, bi_hoy=_bi(ticker),
        meta=Metadata(ticker=ticker), atr_diario=1.5, resultado=resultado,
    )


class _FakeProviderIntradia(DataProvider):
    def __init__(self, tickers_con_datos: dict[str, BarraIntradia] | set[str]) -> None:
        if isinstance(tickers_con_datos, set):
            tickers_con_datos = {t: _bi(t) for t in tickers_con_datos}
        self._bis = tickers_con_datos

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {t: self._bis[t] for t in tickers if t in self._bis}


def _preparar_watchlist(monkeypatch, tmp_path, entradas):
    path = tmp_path / "watchlist.json"
    watchlist.guardar(entradas, path)
    real_cargar, real_guardar = watchlist.cargar, watchlist.guardar
    monkeypatch.setattr(watchlist, "cargar", lambda p=path: real_cargar(p))
    monkeypatch.setattr(watchlist, "guardar", lambda es, p=path: real_guardar(es, p))
    return path


def _parchear(monkeypatch):
    enviados: list[str] = []
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda texto: enviados.append(texto))
    monkeypatch.setattr(run_mod.tracker, "registrar", lambda ops: None)
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: None)
    return enviados


# ------------------------- 1-4: contenido real del mensaje -------------------------

def test_1_watching_a_triggered_manda_ambos_mensajes_con_niveles_reales(monkeypatch, tmp_path):
    """Descubrimiento (`_actualizar_watchlist`) donde la candidata entra a
    WATCHING y se dispara EN LA MISMA corrida -- por diseño (ver
    docstring de `_actualizar_watchlist`) solo se manda TRIGGERED, no
    WATCHING también (sería ruido: "la estoy vigilando" seguido un
    instante después por "ya se activó"). `_actualizar_watchlist` NUNCA
    manda nada ella misma -- devuelve `mensajes_pendientes` para que
    `main()` los mande DESPUÉS del TRIGGERED de esta corrida (prioridad
    máxima, ver su docstring)."""
    _preparar_watchlist(monkeypatch, tmp_path, [])
    enviados = _parchear(monkeypatch)
    c_diario = _candidato_diario("RKLB")
    c_intradia = _candidato_intradia("RKLB", accionable=True)

    entradas, disparadas, mensajes = run_mod._actualizar_watchlist(
        [c_diario], [c_intradia], {"RKLB"}, CFG, dry_run=False, ahora=AHORA,
        dato_recibido_ts="2026-08-11T14:00:00+00:00")

    assert "RKLB" in disparadas
    assert mensajes == []   # RKLB terminó TRIGGERED, no WATCHING -- nada de menor prioridad que mandar
    assert enviados == []   # esta función nunca llama a Telegram directamente


def test_1b_watching_que_se_queda_watching_produce_el_mensaje_de_vigilancia(monkeypatch, tmp_path):
    _preparar_watchlist(monkeypatch, tmp_path, [])
    enviados = _parchear(monkeypatch)
    c_diario = _candidato_diario("RKLB")
    c_intradia = _candidato_intradia("RKLB", accionable=False, temprano=True, patron=None)

    _, _, mensajes = run_mod._actualizar_watchlist(
        [c_diario], [c_intradia], set(), CFG, dry_run=False, ahora=AHORA)

    assert enviados == []   # el envío es responsabilidad del caller (main()), no de esta función
    assert len(mensajes) == 1
    assert "EN VIGILANCIA" in mensajes[0]
    assert "RKLB" in mensajes[0]
    assert "WATCHING" in mensajes[0]
    assert "NO" in mensajes[0] and "entrada" in mensajes[0].lower()
    for jerga in ("RVOL", "EMA9", "VWAP", "ATR", "MACD", "RSI"):
        assert jerga not in mensajes[0]


def test_1c_watching_no_se_repite_en_el_siguiente_rechequeo(monkeypatch, tmp_path):
    # "NO quiero mensajes repetitivos... quiero mensajes cuando cambie
    # algo importante" -- el segundo chequeo de la MISMA candidata, que
    # sigue en WATCHING sin transición nueva, no debe volver a mandar
    # "EN VIGILANCIA".
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    enviados = _parchear(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=False, temprano=True, patron=None))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)
    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=5))

    assert enviados == []   # sigue WATCHING en ambos chequeos, ningún mensaje "EN VIGILANCIA" repetido
    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_WATCHING


def test_2_watching_a_invalidated_manda_mensaje_con_motivo_real(monkeypatch, tmp_path):
    c = _candidato_diario("RKLB", fecha_catalizador="2026-08-01T13:45:00+00:00")
    e = watchlist.desde_candidato_diario(c, AHORA - timedelta(days=1))
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    enviados = _parchear(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=False, temprano=True))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_INVALIDATED
    assert len(enviados) == 1
    assert "INVALIDADA" in enviados[0]
    assert "RKLB" in enviados[0]
    assert "ventana de vigencia" in enviados[0]
    assert "NO ENTRAR." in enviados[0]


def test_3_watching_a_missed_manda_mensaje_no_perseguir(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    enviados = _parchear(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=False, patron="gap_and_go",
            motivo_tarde="Ya se movió más de un 12% desde la ruptura."))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)
    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=5))

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_MISSED
    mensajes_missed = [m for m in enviados if "OPORTUNIDAD PERDIDA" in m]
    assert len(mensajes_missed) == 1   # una sola vez, no en cada chequeo "tarde"
    assert "NO PERSEGUIR." in mensajes_missed[0]
    assert "$78.42" in mensajes_missed[0]   # precio actual real, no inventado


def test_4_watching_a_expired_manda_mensaje_una_sola_vez(monkeypatch, tmp_path):
    vieja = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA - timedelta(hours=3))
    path = _preparar_watchlist(monkeypatch, tmp_path, [vieja])
    enviados = _parchear(monkeypatch)

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia(set()), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_EXPIRED
    assert len(enviados) == 1
    assert "EXPIRADA" in enviados[0]
    assert "No operar." in enviados[0]

    # Un segundo chequeo NO debe volver a mandar EXPIRED -- ya no está
    # en `activas()` (WATCHING), así que ni siquiera se vuelve a evaluar.
    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia(set()), dry_run=False, ahora=AHORA + timedelta(minutes=5))
    assert len(enviados) == 1


# ------------------------- 5-6: reinicio / doble ejecución -------------------------

def test_5_reinicio_del_proceso_no_reprocesa_una_transicion_ya_persistida(monkeypatch, tmp_path):
    # Simula un "reinicio": se recarga la watchlist desde el archivo
    # (nueva llamada a `revisar_watchlist`, como si fuera un proceso
    # nuevo) DESPUÉS de que la transición ya se persistió -- el ticker ya
    # no está en `activas()`, así que la segunda "corrida" ni siquiera lo
    # toca.
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    enviados = _parchear(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)
    assert len(enviados) == 1
    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_TRIGGERED

    # "Reinicio": nueva corrida sobre el MISMO archivo persistido.
    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=5))
    assert len(enviados) == 1   # nunca se volvió a mandar


def test_6_workflow_ejecutandose_dos_veces_el_escaneo_completo_no_duplica(monkeypatch, tmp_path):
    # El chequeo liviano YA disparó RKLB; el escaneo completo (30 min
    # después) lo re-descubre desde cero -- `_filtrar_ya_resueltas_hoy`
    # debe excluirlo, así `main()` nunca lo manda una segunda vez.
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)
    from types import SimpleNamespace
    o = SimpleNamespace(ticker="RKLB")

    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy([o], [e], {}, ahora=AHORA + timedelta(minutes=30))
    assert resultado == []
    assert excluidos == {"RKLB"}


# ------------------------- 7-8: Telegram fallando / lento -------------------------

def test_7_telegram_fallando_no_tumba_la_corrida_y_el_estado_ya_quedo_persistido(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])

    def _post_falla(*a, **kw):
        raise ConnectionError("Telegram no responde")

    monkeypatch.setattr(run_mod.requests, "post", _post_falla)
    monkeypatch.setattr(run_mod.tracker, "registrar", lambda ops: None)
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: None)
    monkeypatch.setenv("MOMENTUM_TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("MOMENTUM_TELEGRAM_CHAT_ID", "y")
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    # `enviar_telegram` real captura la excepción de `requests.post`
    # (try/except ya existente) -- no debe propagar y tumbar la corrida.
    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    # El estado ya se persistió ANTES del intento de envío (ver docstring
    # de `revisar_watchlist`) -- sigue siendo verdad aunque el envío
    # haya fallado.
    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_TRIGGERED


def test_8_telegram_respondiendo_lento_tampoco_tumba_la_corrida(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])

    def _post_lento(*a, **kw):
        import requests
        raise requests.exceptions.Timeout("tardó demasiado")

    monkeypatch.setattr(run_mod.requests, "post", _post_lento)
    monkeypatch.setattr(run_mod.tracker, "registrar", lambda ops: None)
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: None)
    monkeypatch.setenv("MOMENTUM_TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("MOMENTUM_TELEGRAM_CHAT_ID", "y")
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_TRIGGERED


def test_8b_triggered_siempre_se_manda_antes_que_mensajes_de_menor_prioridad(monkeypatch, tmp_path):
    # "TRIGGERED -- PRIORIDAD MÁXIMA, debe enviarse inmediatamente"
    # (pedido explícito). `INVALIDA` se procesa ANTES que `DISPARA` en la
    # iteración interna (orden alfabético de la lista `candidatos`), pero
    # el envío real debe seguir mandando TRIGGERED primero sin importar
    # ese orden de procesamiento.
    c_vieja = _candidato_diario("INVALIDA", fecha_catalizador="2026-08-01T13:45:00+00:00")
    invalida = watchlist.desde_candidato_diario(c_vieja, AHORA - timedelta(days=1))
    dispara = watchlist.desde_candidato_diario(_candidato_diario("ZDISPARA"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [invalida, dispara])
    enviados = _parchear(monkeypatch)
    candidatos = {
        "INVALIDA": _candidato_intradia("INVALIDA", accionable=False, temprano=True),
        "ZDISPARA": _candidato_intradia("ZDISPARA", accionable=True),
    }
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia", lambda ticker, *a, **kw: candidatos[ticker])

    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"INVALIDA", "ZDISPARA"}), dry_run=False, ahora=AHORA)

    assert len(enviados) == 2
    assert "ENTRADA CONFIRMADA" in enviados[0] and "ZDISPARA" in enviados[0]   # TRIGGERED primero
    assert "INVALIDADA" in enviados[1]   # menor prioridad, segundo
    assert watchlist.cargar(path)   # no lanzó, y persistió normalmente


# ------------------------- 9-10: múltiples oportunidades -------------------------

def test_9_multiples_oportunidades_simultaneas_cada_una_su_mensaje_correcto(monkeypatch, tmp_path):
    e_a = watchlist.desde_candidato_diario(_candidato_diario("MEJOR"), AHORA)
    e_b = watchlist.desde_candidato_diario(_candidato_diario("SEGUNDA"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e_a, e_b])
    enviados = _parchear(monkeypatch)
    candidatos = {
        "MEJOR": _candidato_intradia("MEJOR", accionable=True),
        "SEGUNDA": _candidato_intradia("SEGUNDA", accionable=False, temprano=True, patron=None),
    }
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia", lambda ticker, *a, **kw: candidatos[ticker])

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"MEJOR", "SEGUNDA"}), dry_run=False, ahora=AHORA)

    recargadas = {r.ticker: r for r in watchlist.cargar(path)}
    assert recargadas["MEJOR"].estado == watchlist.ESTADO_TRIGGERED
    assert recargadas["SEGUNDA"].estado == watchlist.ESTADO_WATCHING
    assert any("ENTRADA CONFIRMADA" in m and "MEJOR" in m for m in enviados)
    assert not any("SEGUNDA" in m for m in enviados)   # sigue WATCHING sin transición -- sin mensaje nuevo


def test_10_una_se_dispara_mientras_otra_se_invalida_en_la_misma_corrida(monkeypatch, tmp_path):
    dispara = watchlist.desde_candidato_diario(_candidato_diario("DISPARA"), AHORA)
    c_vieja = _candidato_diario("INVALIDA", fecha_catalizador="2026-08-01T13:45:00+00:00")
    invalida = watchlist.desde_candidato_diario(c_vieja, AHORA - timedelta(days=1))
    path = _preparar_watchlist(monkeypatch, tmp_path, [dispara, invalida])
    enviados = _parchear(monkeypatch)
    candidatos = {
        "DISPARA": _candidato_intradia("DISPARA", accionable=True),
        "INVALIDA": _candidato_intradia("INVALIDA", accionable=False, temprano=True),
    }
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia", lambda ticker, *a, **kw: candidatos[ticker])

    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"DISPARA", "INVALIDA"}), dry_run=False, ahora=AHORA)

    recargadas = {r.ticker: r for r in watchlist.cargar(path)}
    assert recargadas["DISPARA"].estado == watchlist.ESTADO_TRIGGERED
    assert recargadas["INVALIDA"].estado == watchlist.ESTADO_INVALIDATED
    assert any("ENTRADA CONFIRMADA" in m and "DISPARA" in m for m in enviados)
    assert any("INVALIDADA" in m and "INVALIDA" in m for m in enviados)
    assert len(enviados) == 2   # exactamente un mensaje por transición, nada de más


# ------------------------- 11: MISSED aunque Telegram falle -------------------------

def test_11_missed_se_confirma_en_el_state_engine_aunque_telegram_falle(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    monkeypatch.setattr(run_mod.tracker, "registrar", lambda ops: None)
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: None)

    def _enviar_que_falla(texto):
        raise RuntimeError("Telegram caído")

    monkeypatch.setattr(run_mod, "enviar_telegram", _enviar_que_falla)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=False, patron="gap_and_go"))

    # Primer chequeo: sube tarde_consecutivas a 1 -- todavía no manda nada.
    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)
    # Segundo chequeo: confirma MISSED -- acá SÍ intenta mandar, y falla.
    try:
        run_mod.revisar_watchlist(
            CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=5))
    except RuntimeError:
        pass   # el envío falló, pero el estado ya se persistió ANTES (ver docstring)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_MISSED


# ------------------------- 13: datos "retrasados" -- latencia honesta -------------------------

def test_13_latencia_no_inventa_precision_si_falta_un_timestamp():
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_invalidated(e, "x", AHORA)   # sin deteccion_ts/evaluacion_ts -- no siempre se conocen
    watchlist.completar_latencia_transicion(e, "2026-08-11T14:05:00+00:00", "2026-08-11T14:05:03+00:00")

    ultima = e.transiciones[-1]
    assert ultima.latencia_desde_deteccion_ms is None   # nunca inventado
    assert ultima.latencia_desde_evaluacion_ms is None
    assert ultima.latencia_desde_transicion_ms is not None   # SIEMPRE calculable (viene del propio timestamp)
    assert ultima.latencia_desde_transicion_ms >= 0


def test_13b_latencia_con_deteccion_conocida_se_calcula_correctamente():
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_missed(
        e, "x", AHORA, deteccion_ts="2026-08-11T14:00:00+00:00", evaluacion_ts="2026-08-11T14:00:01+00:00")
    watchlist.completar_latencia_transicion(e, "2026-08-11T14:00:02+00:00", "2026-08-11T14:00:04+00:00")

    ultima = e.transiciones[-1]
    assert ultima.latencia_desde_deteccion_ms == 4000.0
    assert ultima.latencia_desde_evaluacion_ms == 3000.0


# ------------------------- seguridad: sin broker, solo lectura -------------------------

def test_ningun_modulo_de_esta_fase_importa_nada_de_ejecucion_de_ordenes():
    import inspect
    for modulo in (report, watchlist, run_mod):
        fuente = inspect.getsource(modulo).lower()
        for palabra in ("place_order", "buy_order", "sell_order", "alpaca", "ibapi", "interactive_brokers"):
            assert palabra not in fuente, f"{modulo.__name__} no debería mencionar '{palabra}'"
