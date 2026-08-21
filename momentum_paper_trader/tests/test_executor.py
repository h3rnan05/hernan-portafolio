"""Pruebas del orquestador -- sin red real (Alpaca y la IA mockeados por
completo) y sin tocar el `watchlist.json`/`revisiones.json` reales del
repo (`cargar`/`guardar` parcheados a archivos temporales, mismo patrón
que `momentum_hunter/tests/test_run_watchlist.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion
from momentum_paper_trader import estado, executor, ia_decision
from momentum_paper_trader.config import PaperTraderConfig

AHORA = datetime(2026, 8, 11, 14, 0, 0, tzinfo=UTC)
CFG = PaperTraderConfig()

_DECISION_ENTRA = ia_decision.DecisionIA(
    entrar=True, confianza=9, razonamiento="catalizador sólido, asimetría clara")
_DECISION_ENTRA_MITAD = ia_decision.DecisionIA(
    entrar=True, confianza=7, razonamiento="bueno pero no probado", fraccion=0.5)
_DECISION_NO_ENTRA = ia_decision.DecisionIA(
    entrar=False, confianza=3, razonamiento="noticia vieja, ya corrió")


class _FakeAlpacaClient:
    def __init__(
        self, falla_para: set[str] | None = None, cash: float = 5000.0,
        posiciones: list[dict] | None = None, ordenes: list[dict] | None = None,
        cuenta_rota: bool = False,
    ) -> None:
        self.ordenes_colocadas: list[tuple] = []
        self._falla_para = falla_para or set()
        self._cash = cash
        self._posiciones = posiciones or []
        self._ordenes = ordenes or []
        self._cuenta_rota = cuenta_rota

    def info_cuenta(self) -> dict:
        if self._cuenta_rota:
            raise RuntimeError("Alpaca caído")
        return {"cash": str(self._cash), "equity": str(self._cash)}

    def posiciones(self) -> list[dict]:
        return self._posiciones

    def ordenes_abiertas(self) -> list[dict]:
        return self._ordenes

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


def _parchear(monkeypatch, tmp_path, entradas_watchlist, revisiones_previas=None, decision=_DECISION_ENTRA):
    # Referencias a las funciones reales ANTES de que monkeypatch las
    # reemplace en el módulo -- evita recursión infinita al parchear
    # `cargar`/`guardar` (mismo patrón que `momentum_hunter/tests/
    # test_run_watchlist.py::_preparar_watchlist`).
    real_wl_cargar, real_wl_guardar = watchlist.cargar, watchlist.guardar
    wl_path = tmp_path / "watchlist.json"
    real_wl_guardar(entradas_watchlist, wl_path)
    monkeypatch.setattr(watchlist, "cargar", lambda p=wl_path: real_wl_cargar(p))

    real_estado_cargar, real_estado_guardar = estado.cargar, estado.guardar
    rev_path = tmp_path / "revisiones.json"
    real_estado_guardar(revisiones_previas or [], rev_path)
    monkeypatch.setattr(estado, "cargar", lambda p=rev_path: real_estado_cargar(p))
    monkeypatch.setattr(estado, "guardar", lambda rs, p=rev_path: real_estado_guardar(rs, p))

    contextos: list[str | None] = []

    def _fake_decidir(e, contexto_cuenta=None):
        contextos.append(contexto_cuenta)
        return decision

    monkeypatch.setattr(ia_decision, "decidir", _fake_decidir)

    enviados: list[str] = []
    monkeypatch.setattr(executor, "enviar_telegram", lambda texto: enviados.append(texto))
    return wl_path, rev_path, enviados, contextos


def test_coloca_orden_para_triggered_nueva_cuando_la_ia_aprueba(monkeypatch, tmp_path):
    e = _entrada_triggered()
    wl_path, rev_path, enviados, contextos = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert len(nuevas) == 1
    assert client.ordenes_colocadas == [("RKLB", 65, 78.42, 76.90, 82.50)]
    assert len(enviados) == 1
    assert "[PAPER]" in enviados[0]
    assert "RKLB" in enviados[0]
    assert "catalizador sólido" in enviados[0]   # razonamiento de la IA, no solo niveles mecánicos

    persistidas = estado.cargar(rev_path)
    assert len(persistidas) == 1
    assert persistidas[0].entro is True
    assert persistidas[0].order_id == "orden-RKLB"


def test_la_ia_recibe_el_contexto_de_la_cuenta(monkeypatch, tmp_path):
    e = _entrada_triggered()
    *_, contextos = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=5000.0, posiciones=[{"symbol": "TTWO"}])

    executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert len(contextos) == 1
    assert "5,000.00" in contextos[0]
    assert "TTWO" in contextos[0]


def test_no_coloca_orden_cuando_la_ia_rechaza(monkeypatch, tmp_path):
    e = _entrada_triggered()
    wl_path, rev_path, enviados, _ = _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_NO_ENTRA)
    client = _FakeAlpacaClient(cash=10_000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert nuevas == []
    assert client.ordenes_colocadas == []   # nunca se llamó a Alpaca
    assert enviados == []   # sin orden, sin mensaje de confirmación

    # Pero SÍ queda registrada la revisión -- para no volver a preguntar.
    persistidas = estado.cargar(rev_path)
    assert len(persistidas) == 1
    assert persistidas[0].entro is False
    assert persistidas[0].order_id is None


def test_rechazo_de_la_ia_no_se_vuelve_a_preguntar(monkeypatch, tmp_path):
    e = _entrada_triggered()
    previa = estado.RevisionIA(
        ticker="RKLB", creado_en=e.creado_en, entro=False, confianza=3,
        razonamiento="ya se revisó y no", timestamp="x")
    _parchear(monkeypatch, tmp_path, [e], revisiones_previas=[previa], decision=_DECISION_ENTRA)
    client = _FakeAlpacaClient(cash=10_000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert nuevas == []
    assert client.ordenes_colocadas == []   # ni siquiera se volvió a pedir criterio a la IA


def test_no_duplica_orden_ya_procesada(monkeypatch, tmp_path):
    e = _entrada_triggered()
    previa = estado.RevisionIA(
        ticker="RKLB", creado_en=e.creado_en, entro=True, confianza=9,
        razonamiento="x", timestamp="x", order_id="orden-vieja", cantidad=65,
        precio_entrada=78.42, stop=76.90, objetivo=82.50)
    _parchear(monkeypatch, tmp_path, [e], revisiones_previas=[previa])
    client = _FakeAlpacaClient(cash=10_000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert nuevas == []
    assert client.ordenes_colocadas == []   # nunca se volvió a llamar a Alpaca


# ------------------------- guardarraíles deterministas de cartera -------------------------

def test_no_opera_si_la_cuenta_no_se_puede_leer(monkeypatch, tmp_path):
    e = _entrada_triggered()
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cuenta_rota=True)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert nuevas == []
    assert client.ordenes_colocadas == []   # fail-closed: sin lectura de cuenta, nada se opera


def test_no_duplica_ticker_con_posicion_ya_abierta(monkeypatch, tmp_path):
    e = _entrada_triggered("RKLB")
    *_, contextos = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0, posiciones=[{"symbol": "RKLB"}])

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert nuevas == []
    assert client.ordenes_colocadas == []
    assert contextos == []   # se descartó ANTES de gastar una llamada a la IA


def test_no_duplica_ticker_con_orden_pendiente(monkeypatch, tmp_path):
    e = _entrada_triggered("RKLB")
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0, ordenes=[{"symbol": "RKLB"}])

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == []


def test_respeta_el_maximo_de_posiciones_simultaneas(monkeypatch, tmp_path):
    e = _entrada_triggered("NUEVA")
    _parchear(monkeypatch, tmp_path, [e])
    ocupadas = [{"symbol": s} for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    client = _FakeAlpacaClient(cash=10_000.0, posiciones=ocupadas)

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []   # 5 abiertas = techo alcanzado
    assert client.ordenes_colocadas == []


def test_el_efectivo_real_recorta_la_cantidad_nunca_usa_margen(monkeypatch, tmp_path):
    # Sizing por riesgo pide 65 acciones (~$5,097) pero solo hay $1,000
    # de efectivo -- se recorta a lo que el CASH real permite (12), nunca
    # se toca el buying_power con margen.
    e = _entrada_triggered()
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=1000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert len(nuevas) == 1
    assert client.ordenes_colocadas[0][1] == int(1000.0 // 78.42)


def test_dos_ordenes_en_la_misma_corrida_no_gastan_el_mismo_efectivo(monkeypatch, tmp_path):
    # $6,000: la primera orden (~$5,097) deja ~$903 -- la segunda debe
    # dimensionarse contra el efectivo RESTANTE, no contra el inicial.
    e_a = _entrada_triggered("MEJOR")
    e_b = _entrada_triggered("SEGUNDA")
    _parchear(monkeypatch, tmp_path, [e_a, e_b])
    client = _FakeAlpacaClient(cash=6000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert len(nuevas) == 2
    restante = 6000.0 - client.ordenes_colocadas[0][1] * 78.42
    assert client.ordenes_colocadas[1][1] == int(restante // 78.42)


def test_fraccion_de_la_ia_reduce_la_cantidad(monkeypatch, tmp_path):
    e = _entrada_triggered()
    _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_ENTRA_MITAD)
    client = _FakeAlpacaClient(cash=10_000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert len(nuevas) == 1
    assert client.ordenes_colocadas[0][1] == 32   # int(65 * 0.5)


def test_fraccion_que_no_alcanza_para_una_accion_no_opera_pero_queda_registrada(monkeypatch, tmp_path):
    # Sizing base = 1 acción; fracción 0.5 -> 0 acciones: no se opera,
    # pero la revisión queda registrada para no re-preguntar a la IA.
    e = _entrada_triggered(entrada=100.0, stop=25.0, objetivo=250.0)   # riesgo/acción $75 -> 1 acción
    _, rev_path, enviados, _ = _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_ENTRA_MITAD)
    client = _FakeAlpacaClient(cash=10_000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert nuevas == []
    assert client.ordenes_colocadas == []
    persistidas = estado.cargar(rev_path)
    assert len(persistidas) == 1 and persistidas[0].entro is False


def test_mensaje_incluye_el_tamano_cuando_la_fraccion_es_parcial(monkeypatch, tmp_path):
    e = _entrada_triggered()
    *_, enviados, _ = _parchear(monkeypatch, tmp_path, [e], decision=_DECISION_ENTRA_MITAD)
    client = _FakeAlpacaClient(cash=10_000.0)

    executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert "50% del normal" in enviados[0]


# ------------------------- comportamientos previos que no deben romperse -------------------------

def test_ignora_entradas_no_triggered(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("TTWO"), AHORA)   # sigue WATCHING
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient()

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == []


def test_sin_niveles_cacheados_se_omite_sin_inventar_precio(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)   # nunca se llamó actualizar_niveles
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == []


def test_riesgo_insuficiente_para_una_accion_se_omite(monkeypatch, tmp_path):
    e = _entrada_triggered(entrada=1000.0, stop=800.0, objetivo=1200.0)   # riesgo/acción = 200 > $100 total
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=50_000.0)

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == []


def test_dry_run_no_llama_a_alpaca_ni_a_la_ia_ni_manda_telegram_ni_persiste(monkeypatch, tmp_path):
    e = _entrada_triggered()

    wl_path, rev_path, enviados, _ = _parchear(monkeypatch, tmp_path, [e])

    def _no_deberia_llamarse(e, contexto_cuenta=None):
        raise AssertionError("dry-run no debería consultar a la IA")

    monkeypatch.setattr(ia_decision, "decidir", _no_deberia_llamarse)
    client = _FakeAlpacaClient(cuenta_rota=True)   # ni la cuenta debería tocarse

    nuevas = executor.ejecutar(client, CFG, dry_run=True, ahora=AHORA)

    assert nuevas == []
    assert client.ordenes_colocadas == []
    assert enviados == []
    assert estado.cargar(rev_path) == []


def test_fallo_de_alpaca_en_un_ticker_no_tumba_el_resto(monkeypatch, tmp_path):
    e_falla = _entrada_triggered("ROTO")
    e_ok = _entrada_triggered("OK")
    _parchear(monkeypatch, tmp_path, [e_falla, e_ok])
    client = _FakeAlpacaClient(cash=20_000.0, falla_para={"ROTO"})

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert [n.ticker for n in nuevas] == ["OK"]
    assert client.ordenes_colocadas == [("OK", 65, 78.42, 76.90, 82.50)]


def test_multiples_triggered_simultaneas_generan_ordenes_independientes(monkeypatch, tmp_path):
    e_a = _entrada_triggered("MEJOR")
    e_b = _entrada_triggered("SEGUNDA")
    wl_path, rev_path, enviados, _ = _parchear(monkeypatch, tmp_path, [e_a, e_b])
    client = _FakeAlpacaClient(cash=50_000.0)

    nuevas = executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)

    assert {n.ticker for n in nuevas} == {"MEJOR", "SEGUNDA"}
    assert len(enviados) == 2
    assert len(estado.cargar(rev_path)) == 2


def test_sizing_respeta_el_riesgo_configurado(monkeypatch, tmp_path):
    e = _entrada_triggered(entrada=10.0, stop=9.0, objetivo=12.0)   # riesgo/acción = $1
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=50_000.0)
    cfg = PaperTraderConfig(riesgo_dolares_por_operacion=250.0)

    executor.ejecutar(client, cfg, dry_run=False, ahora=AHORA)

    assert client.ordenes_colocadas == [("RKLB", 250, 10.0, 9.0, 12.0)]


def test_nunca_menciona_broker_real_ni_ejecucion_fuera_de_paper():
    import inspect
    fuente = inspect.getsource(executor)
    bajo = fuente.lower()
    for prohibida in ("api.alpaca.markets", "live", "interactive_brokers", "ibapi"):
        assert prohibida not in bajo


# ------------------------- niveles rancios (2026-08-21) -------------------------
# El precio de entrada se congela cuando momentum_hunter evalúa la señal,
# pero la orden se coloca después: hasta ~9 min más tarde en el escaneo
# completo, y DÍAS más tarde si una corrida del trader falla y la señal
# queda TRIGGERED sin revisar (los estados terminales se conservan varios
# días). Sin este tope, el bot compraría a un precio que ya no existe.

def _con_niveles_de_hace(minutos: float, ticker="RKLB"):
    from datetime import timedelta
    e = _entrada_triggered(ticker)
    e.ultimos_niveles_ts = (datetime.now(UTC) - timedelta(minutes=minutos)).isoformat(timespec="seconds")
    return e


def test_niveles_frescos_si_operan(monkeypatch, tmp_path):
    e = _con_niveles_de_hace(2)
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    assert len(executor.ejecutar(client, CFG, dry_run=False)) == 1


def test_niveles_viejos_no_operan(monkeypatch, tmp_path):
    e = _con_niveles_de_hace(60)   # una hora
    _, rev_path, enviados, contextos = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    assert executor.ejecutar(client, CFG, dry_run=False) == []
    assert client.ordenes_colocadas == []
    assert enviados == []
    assert contextos == []   # ni se gastó una consulta a la IA


def test_niveles_de_hace_dias_no_operan(monkeypatch, tmp_path):
    # El escenario real que motivó el arreglo: la watchlist conserva
    # entradas TRIGGERED varios días.
    e = _con_niveles_de_hace(60 * 24 * 4)   # cuatro días
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    assert executor.ejecutar(client, CFG, dry_run=False) == []
    assert client.ordenes_colocadas == []


def test_niveles_viejos_no_se_marcan_como_revisados(monkeypatch, tmp_path):
    # Clave: la señal puede seguir siendo buena, lo viejo es el PRECIO.
    # No debe quemarse la oportunidad -- el siguiente re-chequeo
    # recalcula los niveles y ahí sí se opera.
    e = _con_niveles_de_hace(60)
    _, rev_path, _, _ = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    executor.ejecutar(client, CFG, dry_run=False)

    assert estado.cargar(rev_path) == []   # sin registro -> se reintenta luego


def test_sin_timestamp_de_niveles_no_se_bloquea(monkeypatch, tmp_path):
    # Ausencia de dato no es evidencia de que esté viejo -- no se inventa
    # el dato que falta en ninguna de las dos direcciones.
    e = _entrada_triggered()
    e.ultimos_niveles_ts = None
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    assert len(executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)) == 1


def test_timestamp_corrupto_no_lanza_ni_bloquea(monkeypatch, tmp_path):
    e = _entrada_triggered()
    e.ultimos_niveles_ts = "no-es-una-fecha"
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=10_000.0)

    assert len(executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)) == 1
