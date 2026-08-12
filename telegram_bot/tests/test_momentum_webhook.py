"""Pruebas del webhook dedicado `/momentum/webhook` en `app.py` -- red
completamente mockeada (`_cargar_watchlist_momentum`,
`_cargar_auditoria_momentum_hoy`, `_momentum_telegram_send`
parcheados), namespace separado del `/telegram/webhook` del wizards bot
(que ya tiene su propio `/trade`). Cubre: secreto inválido, chat no
autorizado, dedup de update_id duplicado (Telegram reintenta por
timeout), y que cada comando responda lo que el State Engine ya
resolvió sin ejecutar nada."""

from __future__ import annotations

import os

os.environ.setdefault("MOMENTUM_TELEGRAM_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("MOMENTUM_TELEGRAM_CHAT_ID", "12345")

import app as app_mod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from momentum_hunter.watchlist import EntradaWatchlist  # noqa: E402

SECRET = "test-secret"
CHAT_ID = "12345"


def _update(update_id: int, texto: str, chat_id: str = CHAT_ID) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "text": texto}}


def _cliente(monkeypatch, entradas=None, auditoria=None):
    async def fake_cargar(*a, **kw):
        return entradas or []

    async def fake_auditoria(*a, **kw):
        return auditoria

    enviados: list[tuple[str, str]] = []

    async def fake_send(chat_id, texto):
        enviados.append((chat_id, texto))

    monkeypatch.setattr(app_mod, "_cargar_watchlist_momentum", fake_cargar)
    monkeypatch.setattr(app_mod, "_cargar_auditoria_momentum_hoy", fake_auditoria)
    monkeypatch.setattr(app_mod, "_momentum_telegram_send", fake_send)
    app_mod._updates_vistos_momentum.clear()
    return TestClient(app_mod.app), enviados


def _headers(secret: str | None = SECRET) -> dict:
    h = {}
    if secret is not None:
        h["X-Telegram-Bot-Api-Secret-Token"] = secret
    return h


def test_secreto_invalido_no_hace_nada(monkeypatch):
    client, enviados = _cliente(monkeypatch)
    resp = client.post("/momentum/webhook", json=_update(1, "/status"), headers=_headers("secreto-incorrecto"))
    assert resp.status_code == 200
    assert enviados == []


def test_chat_no_autorizado_recibe_rechazo_generico(monkeypatch):
    client, enviados = _cliente(monkeypatch)
    resp = client.post("/momentum/webhook", json=_update(1, "/status", chat_id="99999"), headers=_headers())
    assert resp.status_code == 200
    assert len(enviados) == 1
    assert enviados[0][0] == "99999"
    assert "privado" in enviados[0][1].lower()


def test_dedup_de_update_id_duplicado(monkeypatch):
    client, enviados = _cliente(monkeypatch)
    upd = _update(42, "/status")
    client.post("/momentum/webhook", json=upd, headers=_headers())
    client.post("/momentum/webhook", json=upd, headers=_headers())   # Telegram reintenta
    assert len(enviados) == 1   # NO se procesa dos veces


def test_comando_status_responde_con_el_resumen(monkeypatch):
    client, enviados = _cliente(monkeypatch, entradas=[])
    client.post("/momentum/webhook", json=_update(1, "/status"), headers=_headers())
    assert len(enviados) == 1
    assert "BOT STATUS" in enviados[0][1]


def test_comando_radar_sin_oportunidades(monkeypatch):
    client, enviados = _cliente(monkeypatch, entradas=[])
    client.post("/momentum/webhook", json=_update(1, "/radar"), headers=_headers())
    assert "No hay oportunidades activas" in enviados[0][1]


def test_comando_trade_sin_ticker_pide_uso(monkeypatch):
    client, enviados = _cliente(monkeypatch)
    client.post("/momentum/webhook", json=_update(1, "/trade"), headers=_headers())
    assert "Uso: /trade TICKER" in enviados[0][1]


def test_comando_trade_con_ticker_lee_el_estado_existente(monkeypatch):
    e = EntradaWatchlist(
        ticker="RKLB", nombre="Rocket Lab", estado="triggered",
        creado_en="2026-08-11T14:00:00+00:00", actualizado_en="2026-08-11T14:05:00+00:00",
        ultima_entrada=78.42, ultimo_stop=76.90, ultimo_objetivo=82.50,
    )
    client, enviados = _cliente(monkeypatch, entradas=[e])
    client.post("/momentum/webhook", json=_update(1, "/trade RKLB"), headers=_headers())
    assert len(enviados) == 1
    assert "TRIGGERED" in enviados[0][1]
    assert "$78.42" in enviados[0][1]


def test_comando_help(monkeypatch):
    client, enviados = _cliente(monkeypatch)
    client.post("/momentum/webhook", json=_update(1, "/help"), headers=_headers())
    assert "comandos" in enviados[0][1].lower()


def test_comando_desconocido_manda_ayuda_no_ejecuta_nada(monkeypatch):
    client, enviados = _cliente(monkeypatch)
    client.post("/momentum/webhook", json=_update(1, "/comprar RKLB 100"), headers=_headers())
    assert len(enviados) == 1
    assert enviados[0][1] == app_mod.AYUDA_MOMENTUM   # nunca "ejecutó" nada, solo respondió la ayuda


def test_mensaje_vacio_no_hace_nada(monkeypatch):
    client, enviados = _cliente(monkeypatch)
    resp = client.post(
        "/momentum/webhook",
        json={"update_id": 1, "message": {"chat": {"id": int(CHAT_ID)}, "text": ""}},
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert enviados == []


def test_namespace_separado_del_trade_del_screener(monkeypatch):
    # El /trade del wizards bot (screener, `trade_command.py`) vive en
    # /telegram/webhook -- este webhook es un namespace TOTALMENTE
    # separado, con su propio secreto y su propio chat permitido.
    assert "/telegram/webhook" != "/momentum/webhook"
    rutas = [r.path for r in app_mod.app.routes]
    assert "/telegram/webhook" in rutas
    assert "/momentum/webhook" in rutas
