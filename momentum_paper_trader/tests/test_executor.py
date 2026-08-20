"""Pruebas del orquestador -- sin red real (Alpaca mockeado por
completo) y sin tocar el `watchlist.json`/`ordenes.json` reales del
repo (`cargar`/`guardar` parcheados a archivos temporales, mismo patrón
que `momentum_hunter/tests/test_run_watchlist.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion
from momentum_paper_trader import estado, executor
from momentum_paper_trader.config import PaperTraderConfig

AHORA = datetime(2026, 8, 11, 14, 0, 0, tzinfo=UTC)
CFG = PaperTraderConfig()


class _FakeAlpacaClient:
    def __init__(self, falla_para: set[str] | None = None) -> None:
        self.ordenes_colocadas: list[tuple] = []
        self._falla_para = falla_para or set()

    def colocar_orden_bracket(self, ticker, cantidad, entrada, stop, objetivo):
        if ticker in self._falla_para:
            raise RuntimeError(f"{ticker}: símbolo no soportado")
        self.ordenes_colocadas.append((ticker, cantidad, entrada, stop, objetivo))
        from momentum_paper_trader.alpaca_client import OrdenBracket
        return OrdenBracket(
            order_id=f"orden-{ticker}", ticker=ticker, cantidad=cantidad,
            precio_entrada=entrada, stop=stop, objetivo=objetivo, estado="accepted",
        )


def _candidato_diario(ticker="RKLB") -> CandidatoDiario:
    catalizador = Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                               fecha="2026-08-11T13:45:00+00:00")
    return CandidatoDiario(
        ticker=ticker, nombre="Rocket Lab", precio=80.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=1.5), catalizador=catalizador,
        meta=Metadata(ticker=ticker), puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
    )


def _entrada_triggered(
    ticker="RKLB", entrada=78.42, stop=76.90, objetivo=82.50, ahora=AHORA,
) -> watchlist.EntradaWatchlist:
    e = watchlist.desde_candidato_diario(_candidato_diario(ticker), ahora)
    watchlist.marcar_triggered(e, "m", "d", "ev", ahora)
    watchlist.actualizar_niveles(e, entrada, stop, objetivo, entrada, ahora)
    return e


def _parchear(monkeypatch, tmp_path, entradas_watchlist, ordenes_previas=None):
    # Referencias a las funciones reales ANTES de que monkeypatch las
    # reemplace en el módulo -- evita recursión infinita al parchear
    # `cargar`/`guardar` (mismo patrón que `momentum_hunter/tests/
    # test_run_watchlist.py::_preparar_watchlist`).
    real_wl_cargar, real_wl_guardar = watchlist.cargar, watchlist.guardar
    wl_path = tmp_path / "watchlist.json"
    real_wl_guardar(entradas_watchlist, wl_path)
    monkeypatch.setattr(watchlist, "cargar", lambda p=wl_path: real_wl_cargar(p))

    real_estado_cargar, real_estado_guardar = estado.cargar, estado.guardar
    ord_path = tmp_path / "ordenes.json"
    real_estado_guardar(ordenes_previas or [], ord_path)
    monkeypatch.setattr(estado, "cargar", lambda p=ord_path: real_estado_cargar(p))
    monkeypatch.setattr(estado, "guardar", lambda os_, p=ord_path: real_estado_guardar(os_, p))

    enviados: list[str] = []
    monkeypatch.setattr(executor, "enviar_telegram", lambda texto: enviados.append(texto))
    return wl_path, ord_path, enviados


def test_coloca_orden_para_triggered_nueva(monkeypatch, tmp_path):
    e = _entrada_triggered()
    wl_path, ord_path, enviados = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient()

    nuevas = executor.ejecutar(client, CFG, dry_run=False)

    assert len(nuevas) == 1
    assert client.ordenes_colocadas == [("RKLB", 65, 78.42, 76.90, 82.50)]
    assert len(enviados) == 1
    assert "[PAPER]" in enviados[0]
    assert "RKLB" in enviados[0]

    persistidas = estado.cargar(ord_path)
    assert len(persistidas) == 1
    assert persistidas[0].order_id == "orden-RKLB"


def test_no_duplica_orden_ya_procesada(monkeypatch, tmp_path):
    e = _entrada_triggered()
    previa = estado.OrdenRegistrada(
        ticker="RKLB", creado_en=e.creado_en, order_id="orden-vieja", cantidad=65,
        precio_entrada=78.42, stop=76.90, objetivo=82.50, timestamp="x")
    _parchear(monkeypatch, tmp_path, [e], ordenes_previas=[previa])
    client = _FakeAlpacaClient()

    nuevas = executor.ejecutar(client, CFG, dry_run=False)

    assert nuevas == []
    assert client.ordenes_colocadas == []   # nunca se volvió a llamar a Alpaca


def test_ignora_entradas_no_triggered(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("TTWO"), AHORA)   # sigue WATCHING
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient()

    nuevas = executor.ejecutar(client, CFG, dry_run=False)

    assert nuevas == []
    assert client.ordenes_colocadas == []


def test_sin_niveles_cacheados_se_omite_sin_inventar_precio(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)   # nunca se llamó actualizar_niveles
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient()

    nuevas = executor.ejecutar(client, CFG, dry_run=False)

    assert nuevas == []
    assert client.ordenes_colocadas == []


def test_riesgo_insuficiente_para_una_accion_se_omite(monkeypatch, tmp_path):
    # Stop casi pegado a la entrada -- riesgo por acción altísimo relativo
    # al presupuesto, o al revés: acá forzamos un riesgo por acción mayor
    # al total permitido para que 0 acciones sea el resultado.
    e = _entrada_triggered(entrada=1000.0, stop=800.0, objetivo=1200.0)   # riesgo/acción = 200 > $100 total
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient()

    nuevas = executor.ejecutar(client, CFG, dry_run=False)

    assert nuevas == []
    assert client.ordenes_colocadas == []


def test_dry_run_no_llama_a_alpaca_ni_manda_telegram_ni_persiste(monkeypatch, tmp_path):
    e = _entrada_triggered()
    wl_path, ord_path, enviados = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient()

    nuevas = executor.ejecutar(client, CFG, dry_run=True)

    assert nuevas == []
    assert client.ordenes_colocadas == []
    assert enviados == []
    assert estado.cargar(ord_path) == []


def test_fallo_de_alpaca_en_un_ticker_no_tumba_el_resto(monkeypatch, tmp_path):
    e_falla = _entrada_triggered("ROTO")
    e_ok = _entrada_triggered("OK")
    _parchear(monkeypatch, tmp_path, [e_falla, e_ok])
    client = _FakeAlpacaClient(falla_para={"ROTO"})

    nuevas = executor.ejecutar(client, CFG, dry_run=False)

    assert [n.ticker for n in nuevas] == ["OK"]
    assert client.ordenes_colocadas == [("OK", 65, 78.42, 76.90, 82.50)]


def test_multiples_triggered_simultaneas_generan_ordenes_independientes(monkeypatch, tmp_path):
    e_a = _entrada_triggered("MEJOR")
    e_b = _entrada_triggered("SEGUNDA")
    wl_path, ord_path, enviados = _parchear(monkeypatch, tmp_path, [e_a, e_b])
    client = _FakeAlpacaClient()

    nuevas = executor.ejecutar(client, CFG, dry_run=False)

    assert {n.ticker for n in nuevas} == {"MEJOR", "SEGUNDA"}
    assert len(enviados) == 2
    assert len(estado.cargar(ord_path)) == 2


def test_sizing_respeta_el_riesgo_configurado(monkeypatch, tmp_path):
    e = _entrada_triggered(entrada=10.0, stop=9.0, objetivo=12.0)   # riesgo/acción = $1
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient()
    cfg = PaperTraderConfig(riesgo_dolares_por_operacion=250.0)

    executor.ejecutar(client, cfg, dry_run=False)

    assert client.ordenes_colocadas == [("RKLB", 250, 10.0, 9.0, 12.0)]


def test_nunca_menciona_broker_real_ni_ejecucion_fuera_de_paper():
    import inspect
    fuente = inspect.getsource(executor)
    bajo = fuente.lower()
    for prohibida in ("api.alpaca.markets", "live", "interactive_brokers", "ibapi"):
        assert prohibida not in bajo
