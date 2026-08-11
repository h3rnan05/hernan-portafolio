"""Pruebas de `run.revisar_watchlist` -- el chequeo liviano de la
watchlist persistida ("Fase 2", 2026-08-11): sin red (provider falso y
`_construir_candidato_intradia` parcheado a candidatos ya construidos,
mismo patrón que `test_seleccion.py`), sin tocar el `watchlist.json`
real del repo (`cargar`/`guardar` parcheados a un archivo temporal, ver
`_preparar_watchlist`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
) -> CandidatoIntradia:
    factores = FactoresIntradia(
        precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=4.0, aceleracion_volumen=1.5,
        gap_pct=0.10, maximo_premarket=5.00, maximo_dia=5.25, velas_desde_ruptura=1,
    )
    early = EarlyOpportunity(score=90.0, veredicto="temprano" if temprano else "tarde",
                              razon="ok", motivo_veredicto=motivo_tarde)
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True,
        patron="gap_and_go" if accionable else None, temprano=temprano, early=early,
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


def test_marca_missed_cuando_el_veredicto_es_tarde(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(
            ticker, accionable=False, temprano=False,
            motivo_tarde="Ya se movió más de un 12% desde la ruptura."))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    r = watchlist.cargar(path)[0]
    assert r.estado == watchlist.ESTADO_MISSED
    assert r.transiciones[-1].motivo == "Ya se movió más de un 12% desde la ruptura."


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

    entradas, disparadas = run_mod._actualizar_watchlist(
        [c_diario], [c_intradia], {"RKLB"}, CFG, dry_run=False, ahora=AHORA)

    assert "RKLB" in disparadas
    e = disparadas["RKLB"]
    assert e.estado == watchlist.ESTADO_TRIGGERED
    assert e.market_event_ts is not None
    assert e.evaluador_ts is not None
    assert e.mensaje_generado_ts is None      # todavía no lo completó `main()`
    assert e.telegram_enviado_ts is None
    assert e.signal_latency_ms is None


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
