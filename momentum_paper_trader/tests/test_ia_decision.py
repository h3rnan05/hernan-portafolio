"""Pruebas de la capa de decisión con IA -- sin red real (cliente Anthropic
mockeado por completo, mismo patrón que `test_executor.py` mockea Alpaca)
y sin leer los archivos reales del repo (auditoría/tracker parcheados)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion
from momentum_paper_trader import ia_decision

AHORA = datetime(2026, 8, 11, 14, 0, 0, tzinfo=UTC)


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, texto: str) -> None:
        self.content = [_FakeTextBlock(texto)]


class _FakeMessages:
    def __init__(self, respuesta: str | None = None, excepcion: Exception | None = None) -> None:
        self._respuesta = respuesta
        self._excepcion = excepcion
        self.llamadas: list[dict] = []

    def create(self, **kwargs):
        self.llamadas.append(kwargs)
        if self._excepcion:
            raise self._excepcion
        return _FakeMessage(self._respuesta or "")


class _FakeAnthropicClient:
    def __init__(self, respuesta: str | None = None, excepcion: Exception | None = None) -> None:
        self.messages = _FakeMessages(respuesta, excepcion)


def _entrada_triggered(ticker="RKLB", entrada=78.42, stop=76.90, objetivo=82.50) -> watchlist.EntradaWatchlist:
    catalizador = Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                               fecha="2026-08-11T13:45:00+00:00")
    candidato = CandidatoDiario(
        ticker=ticker, nombre="Rocket Lab", precio=80.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=1.5), catalizador=catalizador,
        meta=Metadata(ticker=ticker), puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
    )
    e = watchlist.desde_candidato_diario(candidato, AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)
    watchlist.actualizar_niveles(e, entrada, stop, objetivo, entrada, AHORA)
    return e


def _evidencia_hermetica(monkeypatch):
    """Los tests de `decidir` no deben leer la auditoría/tracker REALES
    del checkout -- se parchean las dos secciones best-effort a None."""
    monkeypatch.setattr(ia_decision, "_snapshot_intradia", lambda ticker: None)
    monkeypatch.setattr(ia_decision, "_historial_catalizador", lambda tipo: None)


def _parchear_anthropic(monkeypatch, **kwargs):
    _evidencia_hermetica(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    fake = _FakeAnthropicClient(**kwargs)
    monkeypatch.setattr(ia_decision, "Anthropic", lambda api_key: fake)
    return fake


def test_decidir_entra_true_con_confianza_alta(monkeypatch):
    _parchear_anthropic(monkeypatch, respuesta=(
        '{"entrar": true, "confianza": 8, "razonamiento": "catalizador sólido, riesgo/beneficio claro"}'
    ))
    e = _entrada_triggered()

    d = ia_decision.decidir(e)

    assert d.entrar is True
    assert d.confianza == 8
    assert d.fraccion == 1.0   # sin fracción explícita, tamaño completo
    assert "catalizador" in d.razonamiento


def test_decidir_entra_false_explicito(monkeypatch):
    _parchear_anthropic(monkeypatch, respuesta=(
        '{"entrar": false, "confianza": 3, "razonamiento": "noticia vieja, ya corrió"}'
    ))
    e = _entrada_triggered()

    d = ia_decision.decidir(e)

    assert d.entrar is False
    assert d.confianza == 3


def test_decidir_cinturon_y_tirantes_confianza_insuficiente(monkeypatch):
    # El modelo dice "entrar": true pero con confianza < 7 -- la regla
    # dura del prompt se re-valida en código, no se confía ciegamente.
    _parchear_anthropic(monkeypatch, respuesta=(
        '{"entrar": true, "confianza": 5, "razonamiento": "dudoso pero interesante"}'
    ))
    e = _entrada_triggered()

    d = ia_decision.decidir(e)

    assert d.entrar is False


def test_decidir_acepta_fraccion_valida(monkeypatch):
    _parchear_anthropic(monkeypatch, respuesta=(
        '{"entrar": true, "confianza": 7, "fraccion": 0.5, "razonamiento": "bueno pero no probado"}'
    ))
    d = ia_decision.decidir(_entrada_triggered())

    assert d.entrar is True
    assert d.fraccion == 0.5


def test_fraccion_fuera_de_rango_o_basura_vuelve_a_tamano_completo(monkeypatch):
    # La fracción SOLO puede reducir dentro de [0.25, 1.0] -- cualquier
    # cosa rara (mayor a 1 para "apalancar", 0, negativa, texto) se
    # ignora: el riesgo configurado sigue siendo el techo.
    assert ia_decision._parsear_fraccion({"fraccion": 2.0}) == 1.0
    assert ia_decision._parsear_fraccion({"fraccion": 0.0}) == 1.0
    assert ia_decision._parsear_fraccion({"fraccion": -0.5}) == 1.0
    assert ia_decision._parsear_fraccion({"fraccion": 0.1}) == 1.0   # < mínimo 0.25
    assert ia_decision._parsear_fraccion({"fraccion": "mucho"}) == 1.0
    assert ia_decision._parsear_fraccion({}) == 1.0
    assert ia_decision._parsear_fraccion({"fraccion": 0.25}) == 0.25


def test_decidir_tolera_json_envuelto_en_markdown(monkeypatch):
    _parchear_anthropic(monkeypatch, respuesta=(
        '```json\n{"entrar": true, "confianza": 9, "razonamiento": "ruptura limpia con volumen"}\n```'
    ))
    e = _entrada_triggered()

    d = ia_decision.decidir(e)

    assert d.entrar is True
    assert d.confianza == 9


def test_decidir_json_invalido_falla_cerrado(monkeypatch):
    _parchear_anthropic(monkeypatch, respuesta="esto no es JSON en absoluto")
    assert ia_decision.decidir(_entrada_triggered()).entrar is False


def test_decidir_json_con_forma_inesperada_falla_cerrado(monkeypatch):
    _parchear_anthropic(monkeypatch, respuesta='{"algo_distinto": true}')
    assert ia_decision.decidir(_entrada_triggered()).entrar is False


def test_decidir_excepcion_de_red_falla_cerrado(monkeypatch):
    _parchear_anthropic(monkeypatch, excepcion=RuntimeError("timeout de red"))
    assert ia_decision.decidir(_entrada_triggered()).entrar is False


def test_decidir_sin_api_key_falla_cerrado_sin_llamar_a_nadie(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        ia_decision, "Anthropic",
        lambda api_key: (_ for _ in ()).throw(AssertionError("no debería instanciarse sin API key")))

    assert ia_decision.decidir(_entrada_triggered()).entrar is False


def test_decidir_sin_niveles_cacheados_falla_cerrado(monkeypatch):
    _parchear_anthropic(monkeypatch, respuesta='{"entrar": true, "confianza": 9, "razonamiento": "x"}')
    catalizador = Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                               fecha="2026-08-11T13:45:00+00:00")
    candidato = CandidatoDiario(
        ticker="RKLB", nombre="Rocket Lab", precio=80.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=1.5), catalizador=catalizador,
        meta=Metadata(ticker="RKLB"), puntuacion=Puntuacion(ticker="RKLB", score_total=88.0, sub={}),
    )
    e = watchlist.desde_candidato_diario(candidato, AHORA)
    watchlist.marcar_triggered(e, "m", "d", "ev", AHORA)   # nunca se llamó actualizar_niveles

    assert ia_decision.decidir(e).entrar is False


# ------------------------- el paquete de evidencia -------------------------

def test_construir_paquete_evidencia_incluye_niveles_congelados(monkeypatch):
    _evidencia_hermetica(monkeypatch)
    e = _entrada_triggered()
    paquete = ia_decision.construir_paquete_evidencia(e)

    assert "78.42" in paquete
    assert "76.90" in paquete
    assert "82.50" in paquete
    assert "RKLB" in paquete
    assert "transiciones" in paquete   # la historia de cómo llegó a TRIGGERED


def test_construir_paquete_evidencia_incluye_contexto_de_cuenta(monkeypatch):
    _evidencia_hermetica(monkeypatch)
    paquete = ia_decision.construir_paquete_evidencia(
        _entrada_triggered(), contexto_cuenta="Efectivo disponible: $5,000.00")

    assert "Estado actual de la cuenta paper" in paquete
    assert "$5,000.00" in paquete


def test_snapshot_intradia_lee_la_ultima_lectura_del_ticker(monkeypatch, tmp_path):
    hoy = datetime.now(UTC).date().isoformat()
    (tmp_path / f"{hoy}.json").write_text(json.dumps({"corridas": [
        {"timestamp": "t1", "candidatos": [{"ticker": "RKLB", "factores_intradia": {
            "precio_actual": 70.0, "rvol_actual": 1.0}, "evaluacion": {}}]},
        {"timestamp": "t2", "candidatos": [{"ticker": "RKLB", "factores_intradia": {
            "precio_actual": 78.5, "vwap": 77.2, "rvol_actual": 4.2,
            "aceleracion_volumen": 2.1},
            "evaluacion": {"early": {"veredicto": "temprano", "motivo_veredicto": "ruptura fresca"}}}]},
    ]}))
    monkeypatch.setattr(ia_decision, "DIR_AUDITORIA", tmp_path)

    snapshot = ia_decision._snapshot_intradia("RKLB")

    assert snapshot is not None
    assert "t2" in snapshot          # la corrida MÁS RECIENTE, no la primera
    assert "4.20" in snapshot        # rvol de la última lectura
    assert "temprano" in snapshot
    assert "ruptura fresca" in snapshot


def test_snapshot_intradia_sin_archivo_devuelve_none(monkeypatch, tmp_path):
    monkeypatch.setattr(ia_decision, "DIR_AUDITORIA", tmp_path / "no_existe")
    assert ia_decision._snapshot_intradia("RKLB") is None


def test_historial_catalizador_usa_la_frase_honesta_de_memoria(monkeypatch):
    from momentum_hunter import tracker
    monkeypatch.setattr(tracker, "cargar", lambda: [])

    frase = ia_decision._historial_catalizador("contrato")

    assert frase is not None
    assert "no puedo" in frase or "Todavía no tengo" in frase   # sin muestra = admisión honesta
