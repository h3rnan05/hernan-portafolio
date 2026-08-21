"""Pruebas de `run.revisar_watchlist` -- el chequeo liviano de la
watchlist persistida ("Fase 2", 2026-08-11): sin red (provider falso y
`_construir_candidato_intradia` parcheado a candidatos ya construidos,
mismo patrón que `test_seleccion.py`), sin tocar el `watchlist.json`
real del repo (`cargar`/`guardar` parcheados a un archivo temporal, ver
`_preparar_watchlist`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
        es_large_cap=True,
    )


def _bi(ticker: str) -> BarraIntradia:
    return BarraIntradia(ticker, ["2026-08-11T14:05:00+00:00"], [5.2], [5.2], [5.25], [5.15], [5000.0])


def _candidato_intradia(
    ticker: str, accionable: bool = True, temprano: bool = True,
    motivo_tarde: str = "ya se movió demasiado", score: float = 90.0,
    patron: str | None = "_default_",
) -> CandidatoIntradia:
    if patron == "_default_":
        patron = "gap_and_go" if accionable else None
    factores = FactoresIntradia(
        precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=4.0, aceleracion_volumen=1.5,
        gap_pct=0.10, maximo_premarket=5.00, maximo_dia=5.25, velas_desde_ruptura=1,
    )
    early = EarlyOpportunity(score=90.0, veredicto="temprano" if temprano else "tarde",
                              razon="ok", motivo_veredicto=motivo_tarde)
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True,
        patron=patron, temprano=temprano, early=early,
        penalizaciones=[] if accionable else ["No hay un patrón técnico claro formándose todavía."],
        score_base=score, score_ajustado=score if accionable else 0.0, accionable=accionable,
    )
    return CandidatoIntradia(
        ticker=ticker, nombre=None,
        catalizador=Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                                 fecha="2026-08-11T13:45:00+00:00"),
        minutos_desde_catalizador=10.0, factores=factores, bi_hoy=_bi(ticker),
        meta=Metadata(ticker=ticker), atr_diario=0.30, resultado=resultado,
    )


class _FakeProviderIntradia(DataProvider):
    """Solo devuelve barras para los tickers en `tickers_con_datos` --
    los demás simulan un fallo transitorio del proveedor para ese ticker
    (yfinance caído, símbolo deslistado a mitad de sesión, etc.)."""

    def __init__(self, tickers_con_datos: set[str]) -> None:
        self._tickers_con_datos = tickers_con_datos

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {t: _bi(t) for t in tickers if t in self._tickers_con_datos}


def _preparar_watchlist(monkeypatch, tmp_path, entradas):
    path = tmp_path / "watchlist.json"
    watchlist.guardar(entradas, path)
    real_cargar = watchlist.cargar
    real_guardar = watchlist.guardar
    monkeypatch.setattr(watchlist, "cargar", lambda p=path: real_cargar(p))
    monkeypatch.setattr(watchlist, "guardar", lambda es, p=path: real_guardar(es, p))
    return path


def _parchear_efectos_secundarios(monkeypatch):
    enviados: list[str] = []
    registrados: list[list] = []
    auditados: list[list] = []
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda texto: enviados.append(texto))
    monkeypatch.setattr(run_mod.tracker, "registrar", lambda ops: registrados.append(ops))
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: auditados.append(snapshots))
    return enviados, registrados, auditados


def test_watchlist_vacia_no_hace_nada(monkeypatch, tmp_path):
    _preparar_watchlist(monkeypatch, tmp_path, [])
    enviados, registrados, _ = _parchear_efectos_secundarios(monkeypatch)
    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia(set()), dry_run=False, ahora=AHORA)
    assert enviados == []
    assert registrados == []


def test_dispara_trigger_y_registra_latencia(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    enviados, registrados, auditados = _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    recargadas = watchlist.cargar(path)
    assert recargadas[0].estado == watchlist.ESTADO_TRIGGERED
    assert recargadas[0].signal_latency_ms is not None
    assert recargadas[0].signal_latency_ms >= 0
    assert len(enviados) == 1
    assert len(registrados) == 1
    assert len(auditados) == 1


def test_dry_run_no_manda_telegram_ni_persiste(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    enviados, registrados, auditados = _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=True, ahora=AHORA)

    assert enviados == []
    assert registrados == []
    assert auditados == []
    recargadas = watchlist.cargar(path)
    assert recargadas[0].estado == watchlist.ESTADO_WATCHING   # el archivo nunca se tocó


def test_fallo_del_proveedor_en_un_ticker_no_tumba_el_batch(monkeypatch, tmp_path):
    e_ok = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    e_falla = watchlist.desde_candidato_diario(_candidato_diario("TTWO"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e_ok, e_falla])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    # Solo RKLB tiene datos -- TTWO simula un fallo transitorio del
    # proveedor para ese ticker.
    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    recargadas = {r.ticker: r for r in watchlist.cargar(path)}
    assert recargadas["RKLB"].estado == watchlist.ESTADO_TRIGGERED
    assert recargadas["TTWO"].estado == watchlist.ESTADO_WATCHING   # se reintenta el próximo ciclo


def test_una_sola_lectura_tarde_no_marca_missed_todavia(monkeypatch, tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11): el veredicto
    # "tarde" es del instante (extensión desde VWAP/EMA9, velas desde la
    # ruptura), ambas condiciones pueden revertirse -- una sola lectura
    # no debe apagar la vigilancia para el resto de la sesión. Se pasa
    # un patrón real (a diferencia de accionable=False solo, que deja
    # patron=None -- ver el siguiente test para ESE caso).
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=False, patron="gap_and_go",
            motivo_tarde="Ya se movió más de un 12% desde la ruptura."))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    r = watchlist.cargar(path)[0]
    assert r.estado == watchlist.ESTADO_WATCHING
    assert r.tarde_consecutivas == 1


def test_sin_patron_detectado_nunca_llega_a_missed_por_tarde(monkeypatch, tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11, segunda vuelta):
    # `early_opportunity.calcular` corre SIEMPRE, incluso sin patrón --
    # sin un patrón real detectado no hay nada que "perseguir" todavía,
    # así que "tarde" nunca debe acumular hacia MISSED (solo el TTL
    # normal puede resolver esta candidata).
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=False, patron=None))

    for i in range(5):   # muchas más que verificaciones_tarde_para_missed
        run_mod.revisar_watchlist(
            CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=5 * i))

    r = watchlist.cargar(path)[0]
    assert r.estado == watchlist.ESTADO_WATCHING
    assert r.tarde_consecutivas == 0


def test_marca_missed_solo_tras_verificaciones_seguidas(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=False, patron="gap_and_go",
            motivo_tarde="Ya se movió más de un 12% desde la ruptura."))

    # CFG.verificaciones_tarde_para_missed == 2 -- hacen falta dos
    # chequeos SEGUIDOS con veredicto "tarde".
    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)
    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=5))

    r = watchlist.cargar(path)[0]
    assert r.estado == watchlist.ESTADO_MISSED
    assert r.transiciones[-1].motivo == "Ya se movió más de un 12% desde la ruptura."


def test_una_lectura_temprano_resetea_el_contador_de_tarde(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=False, patron="gap_and_go"))
    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)
    assert watchlist.cargar(path)[0].tarde_consecutivas == 1

    # Se recupera (vuelve a ser "temprano") -- el contador se reinicia,
    # no se acumula hacia MISSED.
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=True, patron="gap_and_go"))
    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=5))

    r = watchlist.cargar(path)[0]
    assert r.estado == watchlist.ESTADO_WATCHING
    assert r.tarde_consecutivas == 0


def test_marca_invalidated_cuando_el_catalizador_ya_expiro(monkeypatch, tmp_path):
    # Catalizador congelado del 2026-08-01 -- 10 días antes del "ahora"
    # de esta corrida, fuera de `dias_ventana_catalizador` (3 por default).
    c = _candidato_diario("RKLB", fecha_catalizador="2026-08-01T13:45:00+00:00")
    e = watchlist.desde_candidato_diario(c, AHORA - timedelta(days=1))
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=False, temprano=True))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    r = watchlist.cargar(path)[0]
    assert r.estado == watchlist.ESTADO_INVALIDATED


def test_sin_candidatos_no_persiste_en_dry_run(monkeypatch, tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11): la salida
    # temprana cuando el proveedor no devolvió datos para NINGÚN ticker
    # llamaba `guardar` sin chequear `dry_run` -- "calcula y muestra" no
    # debe tocar el archivo persistido bajo ninguna rama.
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    mtime_antes = path.stat().st_mtime_ns

    # Proveedor sin datos para RKLB -- `candidatos` termina vacío.
    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia(set()), dry_run=True, ahora=AHORA)

    assert path.stat().st_mtime_ns == mtime_antes   # el archivo nunca se reescribió


def test_watchlist_vacia_igual_purga_terminales_viejas(monkeypatch, tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11): cuando no hay
    # NADA activo que re-chequear, la salida temprana se saltaba
    # `purgar_antiguas` -- justo el caso para el que existe (el archivo
    # solo tiene entradas terminales).
    vieja = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA - timedelta(days=10))
    watchlist.marcar_missed(vieja, "x", AHORA - timedelta(days=10))
    path = _preparar_watchlist(monkeypatch, tmp_path, [vieja])

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia(set()), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path) == []


def test_sin_candidatos_igual_expira_las_vencidas(monkeypatch, tmp_path):
    # Mismo bug: la salida temprana también se saltaba `expirar_vencidas`
    # -- una candidata vencida se quedaba en WATCHING para siempre si el
    # proveedor fallaba justo ese ciclo.
    vieja = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA - timedelta(hours=3))
    path = _preparar_watchlist(monkeypatch, tmp_path, [vieja])
    _parchear_efectos_secundarios(monkeypatch)

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia(set()), dry_run=False, ahora=AHORA)

    r = watchlist.cargar(path)[0]
    assert r.estado == watchlist.ESTADO_EXPIRED


def test_actualizar_watchlist_no_fabrica_latencia_al_disparar(monkeypatch, tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11): `_actualizar_
    # watchlist` completaba `mensaje_generado_ts`/`telegram_enviado_ts`
    # con el mismo reloj capturado AL EMPEZAR la función, antes de que
    # `main()` siquiera armara el mensaje o llamara a Telegram -- una
    # latencia inventada, no medida. Ahora esos dos campos deben quedar
    # sin llenar hasta que `main()` los complete después del envío real.
    _preparar_watchlist(monkeypatch, tmp_path, [])
    c_diario = _candidato_diario("RKLB")
    c_intradia = _candidato_intradia("RKLB", accionable=True)

    entradas, disparadas, mensajes = run_mod._actualizar_watchlist(
        [c_diario], [c_intradia], {"RKLB"}, CFG, dry_run=False, ahora=AHORA)

    assert "RKLB" in disparadas
    e = disparadas["RKLB"]
    assert e.estado == watchlist.ESTADO_TRIGGERED
    assert e.market_event_ts is not None
    assert e.evaluador_ts is not None
    assert e.mensaje_generado_ts is None      # todavía no lo completó `main()`
    assert e.telegram_enviado_ts is None
    assert e.signal_latency_ms is None
    assert mensajes == []   # RKLB terminó TRIGGERED, no WATCHING -- sin mensaje de menor prioridad


def test_actualizar_watchlist_usa_dato_recibido_ts_real_no_evaluador_ts(monkeypatch, tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11): `marcar_triggered`
    # recibía el mismo reloj dos veces (evaluador_ts también como
    # data_received_ts) -- como si pedir los datos tardara cero segundos.
    _preparar_watchlist(monkeypatch, tmp_path, [])
    c_diario = _candidato_diario("RKLB")
    c_intradia = _candidato_intradia("RKLB", accionable=True)

    entradas, disparadas, _ = run_mod._actualizar_watchlist(
        [c_diario], [c_intradia], {"RKLB"}, CFG, dry_run=False, ahora=AHORA,
        dato_recibido_ts="2026-08-11T14:00:00+00:00")

    e = disparadas["RKLB"]
    assert e.data_received_ts == "2026-08-11T14:00:00+00:00"
    assert e.data_received_ts != e.evaluador_ts


def test_actualizar_watchlist_tambien_debounced_antes_de_missed(monkeypatch, tmp_path):
    _preparar_watchlist(monkeypatch, tmp_path, [])
    _parchear_efectos_secundarios(monkeypatch)
    c_diario = _candidato_diario("RKLB")
    c_intradia_tarde = _candidato_intradia("RKLB", accionable=False, temprano=False, patron="gap_and_go")

    entradas, _, mensajes = run_mod._actualizar_watchlist(
        [c_diario], [c_intradia_tarde], set(), CFG, dry_run=False, ahora=AHORA)
    e = entradas[0]
    assert e.estado == watchlist.ESTADO_WATCHING   # una sola lectura no basta
    # `_actualizar_watchlist` YA NO manda ella misma -- devuelve el mensaje
    # de menor prioridad para que `main()` lo mande DESPUÉS del TRIGGERED
    # de esta corrida (prioridad máxima, ver su docstring).
    assert len(mensajes) == 1
    assert "EN VIGILANCIA" in mensajes[0]
    assert e.tarde_consecutivas == 1


def test_dos_candidatos_watching_compiten_solo_la_mejor_dispara(monkeypatch, tmp_path):
    e_a = watchlist.desde_candidato_diario(_candidato_diario("MEJOR"), AHORA)
    e_b = watchlist.desde_candidato_diario(_candidato_diario("SEGUNDA"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e_a, e_b])
    _parchear_efectos_secundarios(monkeypatch)
    candidatos = {
        "MEJOR": _candidato_intradia("MEJOR", accionable=True, score=99.0),
        "SEGUNDA": _candidato_intradia("SEGUNDA", accionable=True, score=90.0),
    }
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia", lambda ticker, *a, **kw: candidatos[ticker])

    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"MEJOR", "SEGUNDA"}), dry_run=False, ahora=AHORA)

    recargadas = {r.ticker: r for r in watchlist.cargar(path)}
    assert recargadas["MEJOR"].estado == watchlist.ESTADO_TRIGGERED
    assert recargadas["SEGUNDA"].estado == watchlist.ESTADO_WATCHING   # perdió la competencia, sigue vigilada


# ------------------------- _filtrar_ya_resueltas_hoy -------------------------
# Bug real encontrado en revisión de PR (2026-08-11, tercera y quinta
# vuelta): el escaneo completo podía re-mandar la misma alerta que el
# chequeo liviano ya había mandado minutos antes (o incluso mandar UNA
# alerta nueva sin registrar ninguna transición) porque `seleccionar_y_
# auditar` re-evalúa el universo entero sin consultar la watchlist.

def test_filtrar_ya_resueltas_hoy_excluye_lo_ya_triggered_antes_de_esta_corrida():
    o = SimpleNamespace(ticker="RKLB")
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)   # ya disparado ANTES de esta corrida

    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy([o], [e], {}, ahora=AHORA)
    assert resultado == []
    assert excluidos == {"RKLB"}


def test_filtrar_ya_resueltas_hoy_no_excluye_lo_recien_disparado_esta_corrida():
    o = SimpleNamespace(ticker="RKLB")
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)

    # RKLB SÍ está en disparadas_watchlist -- lo disparó ESTA MISMA corrida.
    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy([o], [e], {"RKLB": e}, ahora=AHORA)
    assert resultado == [o]
    assert excluidos == set()


def test_filtrar_ya_resueltas_hoy_no_excluye_triggered_de_un_dia_anterior():
    o = SimpleNamespace(ticker="RKLB")
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)

    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy(
        [o], [e], {}, ahora=AHORA + timedelta(days=1))
    assert resultado == [o]
    assert excluidos == set()


def test_filtrar_ya_resueltas_hoy_no_toca_tickers_sin_entrada_en_watchlist():
    o = SimpleNamespace(ticker="OTRO")
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)

    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy([o], [e], {}, ahora=AHORA)
    assert resultado == [o]
    assert excluidos == {"RKLB"}   # RKLB se excluye, pero no afecta a OTRO


def test_filtrar_ya_resueltas_hoy_excluye_missed_no_solo_triggered(monkeypatch, tmp_path):
    # Bug real encontrado en revisión de PR (2026-08-11, quinta vuelta):
    # excluir solo TRIGGERED no bastaba -- un ticker resuelto hoy como
    # MISSED/INVALIDATED/EXPIRED también salió de WATCHING, y
    # seleccionar_y_auditar podía seguir eligiéndolo sin que quedara
    # NINGÚN rastro en la watchlist (ni siquiera se re-marca).
    o = SimpleNamespace(ticker="RKLB")
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_missed(e, "x", AHORA)

    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy([o], [e], {}, ahora=AHORA)
    assert resultado == []
    assert excluidos == {"RKLB"}


def test_filtrar_ya_resueltas_hoy_excluye_invalidated():
    o = SimpleNamespace(ticker="RKLB")
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_invalidated(e, "x", AHORA)

    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy([o], [e], {}, ahora=AHORA)
    assert resultado == []
    assert excluidos == {"RKLB"}


def test_filtrar_ya_resueltas_hoy_excluye_expired():
    o = SimpleNamespace(ticker="RKLB")
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.expirar_vencidas([e], minutos_maximos=1, ahora=AHORA + timedelta(hours=1))
    assert e.estado == watchlist.ESTADO_EXPIRED

    resultado, excluidos = run_mod._filtrar_ya_resueltas_hoy(
        [o], [e], {}, ahora=AHORA + timedelta(hours=1))
    assert resultado == []
    assert excluidos == {"RKLB"}


# ------------------------- invalidación por tesis rota (2026-08-21) -------------------------
# Antes de este bloque, WATCHING solo se abandonaba por reloj (TTL),
# calendario (catalizador vencido) o "ya vamos tarde". No había ninguna
# salida por lo que hiciera el PRECIO: medido sobre 329 entradas reales,
# 267 salieron por reloj, 35 por calendario, 4 por tarde y CERO porque la
# tesis se rompiera. Una candidata podía desplomarse en observación y el
# bot la seguía mirando hasta que se acabara el temporizador.

def _entrada_vigilada(ticker="RKLB", stop=5.00, ahora=AHORA):
    e = watchlist.desde_candidato_diario(_candidato_diario(ticker), ahora)
    watchlist.actualizar_niveles(e, 5.30, stop, 5.90, 5.25, ahora)
    return e


def test_precio_bajo_el_stop_invalida_la_idea():
    e = _entrada_vigilada(stop=5.00)
    c = _candidato_intradia("RKLB", accionable=False)
    c.factores.precio_actual = 4.80   # por debajo del stop de la idea

    mensaje = run_mod._evaluar_no_disparada(e, c, CFG, AHORA)

    assert e.estado == watchlist.ESTADO_INVALIDATED
    assert mensaje is not None and "INVALIDADA" in mensaje
    assert "4.80" in mensaje and "5.00" in mensaje   # ambos precios, sin inventar nada


def test_precio_justo_en_el_stop_no_invalida():
    # El corte es estricto (`<`): tocar el stop no es perderlo.
    e = _entrada_vigilada(stop=5.00)
    c = _candidato_intradia("RKLB", accionable=False)
    c.factores.precio_actual = 5.00

    assert run_mod._evaluar_no_disparada(e, c, CFG, AHORA) is None
    assert e.estado == watchlist.ESTADO_WATCHING


def test_precio_sano_sigue_en_observacion():
    e = _entrada_vigilada(stop=5.00)
    c = _candidato_intradia("RKLB", accionable=False)
    c.factores.precio_actual = 5.20

    assert run_mod._evaluar_no_disparada(e, c, CFG, AHORA) is None
    assert e.estado == watchlist.ESTADO_WATCHING


def test_sin_stop_cacheado_no_se_inventa_una_invalidacion():
    # Una entrada que todavía no tiene niveles calculados no puede
    # romper una tesis que aún no existe.
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    e.stop_tesis = None
    c = _candidato_intradia("RKLB", accionable=False)
    c.factores.precio_actual = 0.01

    run_mod._evaluar_no_disparada(e, c, CFG, AHORA)
    assert e.estado == watchlist.ESTADO_WATCHING


def test_sin_precio_actual_no_se_inventa_una_invalidacion():
    e = _entrada_vigilada(stop=5.00)
    c = _candidato_intradia("RKLB", accionable=False)
    c.factores.precio_actual = None

    run_mod._evaluar_no_disparada(e, c, CFG, AHORA)
    assert e.estado != watchlist.ESTADO_INVALIDATED


def test_tesis_rota_gana_sobre_el_conteo_de_tarde():
    # "Tarde" es una lectura del instante y reversible; el precio por
    # debajo del stop es objetivo y definitivo. No debe quedar
    # enmascarado por el contador de MISSED.
    e = _entrada_vigilada(stop=5.00)
    c = _candidato_intradia("RKLB", accionable=False, temprano=False, patron="gap_and_go")
    c.factores.precio_actual = 4.50

    run_mod._evaluar_no_disparada(e, c, CFG, AHORA)

    assert e.estado == watchlist.ESTADO_INVALIDATED
    assert e.tarde_consecutivas == 0   # no se contaminó el contador de MISSED


def test_catalizador_vencido_sigue_invalidando_cuando_el_precio_esta_sano():
    # Regresión: la causa vieja de INVALIDATED no debe romperse.
    e = watchlist.desde_candidato_diario(
        _candidato_diario("RKLB", fecha_catalizador="2026-07-01T13:45:00+00:00"), AHORA)
    watchlist.actualizar_niveles(e, 5.30, 5.00, 5.90, 5.25, AHORA)
    c = _candidato_intradia("RKLB", accionable=False)
    c.factores.precio_actual = 5.20   # precio sano

    mensaje = run_mod._evaluar_no_disparada(e, c, CFG, AHORA)

    assert e.estado == watchlist.ESTADO_INVALIDATED
    assert "ventana de vigencia" in mensaje


def test_stop_tesis_se_congela_y_no_persigue_al_precio():
    # El defecto que una prueba encontró al implementar esto:
    # `ultimo_stop` se recalcula en cada chequeo, así que baja con el
    # precio y la tesis nunca se daría por rota. El congelado no se toca.
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.actualizar_niveles(e, 5.30, 5.00, 5.90, 5.25, AHORA)
    assert e.stop_tesis == 5.00

    watchlist.actualizar_niveles(e, 4.60, 4.30, 5.10, 4.55, AHORA)   # todo bajó
    assert e.ultimo_stop == 4.30     # el vigente sí sigue al mercado
    assert e.stop_tesis == 5.00      # el de la tesis no se movió


def test_stop_tesis_se_congela_aunque_el_primer_calculo_llegue_vacio():
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.actualizar_niveles(e, None, None, None, None, AHORA)
    assert e.stop_tesis is None      # no se congela un None
    watchlist.actualizar_niveles(e, 5.30, 5.00, 5.90, 5.25, AHORA)
    assert e.stop_tesis == 5.00      # se congela en el primer valor real
