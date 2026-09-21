"""Aviso de fallo técnico de la IA: racha, un Telegram por día, evento rojo.

No coloca órdenes. El fail-closed (no persistir la revisión) lo cubre
`test_executor.py`; acá se fija que el silencio del 2026-09-21 no se repita
y que no se spamee cada 5 minutos.
"""

from __future__ import annotations

import json

from momentum_paper_trader import aviso_fallo_ia as aviso
from momentum_paper_trader import estado, executor, ia_decision
from momentum_paper_trader.tests.test_executor import (
    AHORA,
    CFG,
    _FakeAlpacaClient,
    _entrada_triggered,
    _parchear,
)


def _env(monkeypatch, tmp_path, hoy="2026-09-21"):
    monkeypatch.setenv("MOMENTUM_AVISOS_DIR", str(tmp_path / "avisos"))
    monkeypatch.setenv("DASH_EVENTOS", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(aviso, "_hoy", lambda: hoy)
    enviados: list[str] = []

    def _ok(texto: str) -> bool:
        enviados.append(texto)
        return True

    monkeypatch.setattr(aviso, "_enviar_telegram", _ok)
    return enviados


def _eventos(tmp_path):
    ruta = tmp_path / "events.jsonl"
    if not ruta.exists():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_credito_avisa_una_vez_por_dia_y_deja_evento(monkeypatch, tmp_path):
    enviados = _env(monkeypatch, tmp_path)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)

    assert len(enviados) == 1
    assert "credit balance" in enviados[0]
    assert "[PAPER]" in enviados[0]
    assert "sk-" not in enviados[0]
    eventos = _eventos(tmp_path)
    assert len(eventos) == 2
    assert all(e["tipo"] == "ia_fallo_tecnico" and e["codigo"] == "credito" for e in eventos)
    assert eventos[0]["motivo"] == "saldo Anthropic insuficiente"

    monkeypatch.setattr(aviso, "_hoy", lambda: "2026-09-22")
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)
    assert len(enviados) == 2


def test_api_espera_tres_corridas_y_no_repite_el_telegram(monkeypatch, tmp_path):
    enviados = _env(monkeypatch, tmp_path)
    for _ in range(2):
        aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    assert enviados == [] and _eventos(tmp_path) == []

    aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    assert len(enviados) == 1
    assert len(_eventos(tmp_path)) == 2
    assert "varias corridas" in enviados[0]


def test_una_decision_real_corta_la_racha(monkeypatch, tmp_path):
    enviados = _env(monkeypatch, tmp_path)
    aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    aviso.observar_corrida(fallos=[], hubo_decision=True)
    aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    assert enviados == []


def test_sin_senal_no_borra_la_racha_ni_avisa(monkeypatch, tmp_path):
    # Mercado cerrado o nada TRIGGERED: no es evidencia de que la IA
    # respondió, así que la racha de saldo sigue viva.
    enviados = _env(monkeypatch, tmp_path)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)
    aviso.observar_corrida(fallos=[], hubo_decision=False)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)
    assert len(enviados) == 1
    marca = json.loads((tmp_path / "avisos" / "ia_fallo_tecnico.json").read_text(encoding="utf-8"))
    assert marca["consecutivos"] == 2


def test_dry_run_no_escribe_ni_avisa(monkeypatch, tmp_path):
    enviados = _env(monkeypatch, tmp_path)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False, dry_run=True)
    assert enviados == []
    assert not (tmp_path / "avisos" / "ia_fallo_tecnico.json").exists()
    assert not (tmp_path / "events.jsonl").exists()


def test_si_telegram_falla_no_marca_el_dia_y_reintenta(monkeypatch, tmp_path):
    enviados = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(aviso, "_enviar_telegram", lambda texto: False)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)
    assert _eventos(tmp_path)  # el panel sí se entera

    def _ok(texto: str) -> bool:
        enviados.append(texto)
        return True

    monkeypatch.setattr(aviso, "_enviar_telegram", _ok)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)
    assert len(enviados) == 1


def test_no_lanza_si_no_puede_guardar_la_marca(monkeypatch, tmp_path):
    bloqueo = tmp_path / "soy_archivo"
    bloqueo.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MOMENTUM_AVISOS_DIR", str(bloqueo / "no"))
    monkeypatch.setattr(aviso, "_hoy", lambda: "2026-09-21")
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)


def test_credito_y_tecnico_no_se_comen_el_aviso(monkeypatch, tmp_path):
    # Clases distintas: un fallo de red a la mañana no tapa el saldo.
    enviados = _env(monkeypatch, tmp_path)
    for _ in range(3):
        aviso.observar_corrida(fallos=[("RKLB", "api")], hubo_decision=False)
    aviso.observar_corrida(fallos=[("NTLA", "credito")], hubo_decision=False)
    assert len(enviados) == 2
    assert "credit balance" in enviados[-1]


def test_el_texto_de_la_excepcion_no_sale_en_el_aviso(monkeypatch, tmp_path):
    enviados = _env(monkeypatch, tmp_path)
    aviso.observar_corrida(
        fallos=[("NTLA", "credito"), ("no es ticker <script>", "credito")],
        hubo_decision=False)
    assert "script" not in enviados[0]
    assert "<" not in enviados[0]
    evento = _eventos(tmp_path)[0]
    assert "script" not in json.dumps(evento)
    assert evento["tickers"] == "NTLA"


_DECISION_CREDITO = ia_decision.DecisionIA(
    entrar=False, confianza=0,
    razonamiento="La revisión de la IA falló -- fail-closed.",
    fallo_tecnico=True, codigo_fallo="credito",
)


def test_el_ejecutor_no_opera_y_avisa_el_saldo(monkeypatch, tmp_path):
    enviados_ia = _env(monkeypatch, tmp_path)
    e = _entrada_triggered()
    _, rev_path, enviados_trade, _ = _parchear(
        monkeypatch, tmp_path, [e], decision=_DECISION_CREDITO)
    client = _FakeAlpacaClient(cash=10_000.0)

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == []
    assert enviados_trade == []
    assert estado.cargar(rev_path) == []
    assert len(enviados_ia) == 1

    eventos = _eventos(tmp_path)
    assert any(ev["tipo"] == "ia_fallo_tecnico" and ev["codigo"] == "credito" for ev in eventos)
    decisiones = [ev for ev in eventos if ev["tipo"] == "decision"]
    assert decisiones and decisiones[0]["fallo_tecnico"] is True
    assert decisiones[0]["codigo"] == "credito"

    executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA)
    assert len(enviados_ia) == 1
    assert client.ordenes_colocadas == []
