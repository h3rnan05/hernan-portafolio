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


# ------------------------- decisión de cierre (2026-08-21) -------------------------

def _parchear_cierre(monkeypatch, **kwargs):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    fake = _FakeAnthropicClient(**kwargs)
    monkeypatch.setattr(ia_decision, "Anthropic", lambda api_key: fake)
    return fake


def test_cierre_la_ia_puede_aguantar_con_conviccion(monkeypatch):
    _parchear_cierre(monkeypatch, respuesta=(
        '{"cerrar": false, "confianza": 8, "razonamiento": "el catalizador sigue vigente"}'))
    d = ia_decision.decidir_cierre("Ticker: RKLB")
    assert d.cerrar is False
    assert "catalizador" in d.razonamiento


def test_cierre_aguantar_con_poca_conviccion_se_convierte_en_cerrar(monkeypatch):
    # Cinturón y tirantes: aguantar de un día para otro exige confianza >= 7.
    _parchear_cierre(monkeypatch, respuesta=(
        '{"cerrar": false, "confianza": 4, "razonamiento": "quizás se recupere"}'))
    d = ia_decision.decidir_cierre("Ticker: RKLB")
    assert d.cerrar is True
    assert "Convicción insuficiente" in d.razonamiento


def test_cierre_sin_api_key_cierra_por_defecto(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ia_decision.decidir_cierre("x").cerrar is True


def test_cierre_error_de_red_cierra_por_defecto(monkeypatch):
    _parchear_cierre(monkeypatch, excepcion=RuntimeError("timeout"))
    assert ia_decision.decidir_cierre("x").cerrar is True


def test_cierre_json_invalido_cierra_por_defecto(monkeypatch):
    _parchear_cierre(monkeypatch, respuesta="no soy json")
    assert ia_decision.decidir_cierre("x").cerrar is True


def test_cierre_json_con_forma_rara_cierra_por_defecto(monkeypatch):
    _parchear_cierre(monkeypatch, respuesta='{"otra_cosa": 1}')
    assert ia_decision.decidir_cierre("x").cerrar is True


def test_cierre_tolera_markdown(monkeypatch):
    _parchear_cierre(monkeypatch, respuesta=(
        '```json\n{"cerrar": true, "confianza": 9, "razonamiento": "se agotó"}\n```'))
    assert ia_decision.decidir_cierre("x").cerrar is True


# ------------------------- respuesta vacía (fallo real 2026-08-24) -------------------------
# La PRIMERA señal que llegó a la IA en la historia del bot (LLY, tras
# corregir el umbral) recibió HTTP 200 pero con texto VACÍO, y el
# fail-closed la descartó. Causa más probable: presupuesto de tokens
# ajustado (500) -- si el modelo lo agota antes de emitir el JSON,
# `msg.content` puede no traer ningún bloque "text".

class _FakeMessageVacio:
    def __init__(self, stop_reason="max_tokens", bloques=None):
        self.content = bloques if bloques is not None else []
        self.stop_reason = stop_reason


class _MessagesSecuencia:
    """Devuelve respuestas distintas en cada llamada -- para probar el
    reintento."""
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.llamadas = 0

    def create(self, **kwargs):
        self.llamadas += 1
        self.kwargs = kwargs
        r = self._respuestas.pop(0) if self._respuestas else self._respuestas
        return r


class _ClienteSecuencia:
    def __init__(self, respuestas):
        self.messages = _MessagesSecuencia(respuestas)


def test_presupuesto_de_tokens_es_holgado():
    # El valor que causó el fallo era 500; no debe volver a quedarse corto.
    assert ia_decision.MAX_TOKENS_ENTRADA >= 1500
    assert ia_decision.MAX_TOKENS_CIERRE >= 1000


def test_respuesta_vacia_reintenta_una_vez_y_puede_recuperarse(monkeypatch):
    _evidencia_hermetica(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    bueno = _FakeMessage('{"entrar": true, "confianza": 9, "razonamiento": "ruptura limpia"}')
    cliente = _ClienteSecuencia([_FakeMessageVacio(), bueno])
    monkeypatch.setattr(ia_decision, "Anthropic", lambda api_key: cliente)

    d = ia_decision.decidir(_entrada_triggered())

    assert cliente.messages.llamadas == 2   # reintentó
    assert d.entrar is True                  # y se recuperó


def test_dos_respuestas_vacias_fallan_cerrado_sin_reintentar_en_bucle(monkeypatch):
    _evidencia_hermetica(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cliente = _ClienteSecuencia([_FakeMessageVacio(), _FakeMessageVacio()])
    monkeypatch.setattr(ia_decision, "Anthropic", lambda api_key: cliente)

    d = ia_decision.decidir(_entrada_triggered())

    assert cliente.messages.llamadas == 2   # exactamente dos, no un bucle
    assert d.entrar is False                 # fail-closed intacto


def test_se_usa_el_presupuesto_grande_en_la_llamada(monkeypatch):
    _evidencia_hermetica(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cliente = _ClienteSecuencia([_FakeMessage('{"entrar": false, "confianza": 2, "razonamiento": "x"}')])
    monkeypatch.setattr(ia_decision, "Anthropic", lambda api_key: cliente)

    ia_decision.decidir(_entrada_triggered())

    assert cliente.messages.kwargs["max_tokens"] == ia_decision.MAX_TOKENS_ENTRADA


def test_texto_vacio_deja_constancia_del_motivo(caplog):
    # El 2026-08-24 hubo que deducir la causa desde fuera porque el log
    # no decía nada. Ahora debe decir stop_reason y los tipos de bloque.
    import logging
    with caplog.at_level(logging.WARNING):
        assert ia_decision._texto_de(_FakeMessageVacio(stop_reason="max_tokens"), "LLY") == ""
    assert "max_tokens" in caplog.text
    assert "LLY" in caplog.text


def test_bloques_no_texto_se_ignoran_pero_se_reportan(caplog):
    import logging

    class _BloqueThinking:
        type = "thinking"

    with caplog.at_level(logging.WARNING):
        msg = _FakeMessageVacio(bloques=[_BloqueThinking()])
        assert ia_decision._texto_de(msg, "LLY") == ""
    assert "thinking" in caplog.text


def test_decidir_cierre_tambien_reintenta_si_viene_vacio(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    bueno = _FakeMessage('{"cerrar": true, "confianza": 8, "razonamiento": "se agotó"}')
    cliente = _ClienteSecuencia([_FakeMessageVacio(), bueno])
    monkeypatch.setattr(ia_decision, "Anthropic", lambda api_key: cliente)

    d = ia_decision.decidir_cierre("Ticker: LLY")

    assert cliente.messages.llamadas == 2
    assert d.cerrar is True
