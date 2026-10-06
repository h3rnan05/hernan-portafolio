"""TELEGRAM_SOLO_ENTRADAS (dueño, 2026-10-06): Telegram solo cuando
momentum ENTRA (la compra llenada) + seguridad crítica (posición sin
stop / sin seguimiento, cierre de fin de día fallido o que deja la
posición abierta, stop diario disparado). Todo lo demás queda en el log.

Estas pruebas corren el camino REAL hasta `requests.post` (mockeado):
el filtro vive en `momentum_hunter.run.enviar_telegram`, así que lo
que se verifica es lo que de verdad saldría al chat. La suite histórica
corre con el filtro apagado (ver conftest raíz); acá se prueba el
default de producción (activo), el interruptor por archivo y que =0
devuelve el comportamiento anterior."""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter import telegram_filtro
from momentum_paper_trader import (
    cierre,
    executor,
    notify,
    reconciliacion,
    seguimiento,
)
from momentum_paper_trader.tests import test_executor as tex
from momentum_paper_trader.tests import test_reconciliacion as trec
from momentum_paper_trader.tests import test_seguimiento as tseg

_ENVIAR_REAL = notify.enviar
RAIZ = Path(__file__).resolve().parents[2]
NOTIFY_SH = RAIZ / "scripts" / "notify_telegram.sh"


@pytest.fixture
def chat(monkeypatch, tmp_path):
    """Filtro en su default de producción (sin variable, sin archivo) y
    Telegram configurado con `requests.post` capturado."""
    monkeypatch.delenv("TELEGRAM_SOLO_ENTRADAS", raising=False)
    monkeypatch.setenv("MOMENTUM_TELEGRAM_FLAG_FILE", str(tmp_path / "no_existe"))
    monkeypatch.setenv("MOMENTUM_TELEGRAM_BOT_TOKEN", "token-de-prueba")
    monkeypatch.setenv("MOMENTUM_TELEGRAM_CHAT_ID", "123")
    monkeypatch.delenv("MOMENTUM_TELEGRAM_PREFIJO", raising=False)
    posts: list[str] = []
    monkeypatch.setattr(run_mod.requests, "post", lambda url, json=None, timeout=None: posts.append(json["text"]))
    return posts


def _sender_real(monkeypatch):
    """Los harness históricos parchean `notify.enviar` arriba del filtro;
    acá se vuelve al real para que el mensaje baje hasta `requests.post`."""
    monkeypatch.setattr(notify, "enviar", _ENVIAR_REAL)
    monkeypatch.setattr(seguimiento, "enviar_telegram", _ENVIAR_REAL)


# --------------------------- el interruptor ---------------------------

def test_default_activo_sin_variable_ni_archivo(chat):
    assert telegram_filtro.solo_entradas() is True


@pytest.mark.parametrize("valor,esperado", [("0", False), ("1", True), ("off", False), ("basura", True)])
def test_variable_de_entorno(chat, monkeypatch, valor, esperado):
    monkeypatch.setenv("TELEGRAM_SOLO_ENTRADAS", valor)
    assert telegram_filtro.solo_entradas() is esperado


def test_archivo_manda_sobre_la_variable_y_se_lee_en_cada_envio(chat, monkeypatch, tmp_path):
    flag = tmp_path / "interruptor"
    monkeypatch.setenv("MOMENTUM_TELEGRAM_FLAG_FILE", str(flag))
    monkeypatch.setenv("TELEGRAM_SOLO_ENTRADAS", "1")
    flag.write_text("0\n", encoding="utf-8")
    run_mod.enviar_telegram("WATCHING RKLB")
    assert chat == ["WATCHING RKLB"]
    flag.write_text("1\n", encoding="utf-8")   # sin reiniciar nada
    run_mod.enviar_telegram("WATCHING ASTS")
    assert chat == ["WATCHING RKLB"]


# --------------------------- el sender central ---------------------------

def test_info_queda_en_el_log_y_no_sale(chat, caplog):
    with caplog.at_level(logging.INFO, logger="telegram_filtro"):
        run_mod.enviar_telegram("🚨 SEÑAL DISPARADA RKLB")
    assert chat == []
    assert any("silenciado" in r.getMessage() and "SEÑAL DISPARADA" in r.getMessage() for r in caplog.records)


def test_entrada_y_critico_salen(chat):
    run_mod.enviar_telegram("LLENADA", categoria=telegram_filtro.ENTRADA)
    run_mod.enviar_telegram("ERROR sin stop", categoria=telegram_filtro.CRITICO)
    assert chat == ["LLENADA", "ERROR sin stop"]


def test_flag_cero_restaura_todo(chat, monkeypatch):
    monkeypatch.setenv("TELEGRAM_SOLO_ENTRADAS", "0")
    run_mod.enviar_telegram("WATCHING RKLB")
    notify.enviar("NO ENTRA")
    assert chat == ["WATCHING RKLB", "NO ENTRA"]


def test_notify_enviar_por_default_es_info(chat):
    notify.enviar("🧪 [PAPER] <b>COLOCADA</b>")
    assert chat == []


# --------------------------- entrada: LLENADA ---------------------------

def _orden_llena():
    return {"orden-RKLB": {"status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
        {"type": "limit", "status": "new"}, {"type": "stop", "status": "held"}]}}


def test_llenada_sale(chat, monkeypatch, tmp_path):
    tseg._parchear(monkeypatch, tmp_path, [tseg._revision_con_orden()])
    _sender_real(monkeypatch)
    cambiadas = seguimiento.revisar(tseg._FakeClient(_orden_llena()))
    assert [c.resultado for c in cambiadas] == ["abierta"]
    assert len(chat) == 1 and "LLENADA" in chat[0] and "RKLB" in chat[0]


def test_salida_por_objetivo_queda_en_el_log(chat, monkeypatch, tmp_path):
    tseg._parchear(monkeypatch, tmp_path, [tseg._revision_con_orden(resultado="abierta")])
    _sender_real(monkeypatch)
    client = tseg._FakeClient({"orden-RKLB": {
        "status": "filled", "filled_avg_price": "78.40", "filled_qty": "65", "legs": [
            {"type": "limit", "status": "filled", "filled_avg_price": "82.50"},
            {"type": "stop", "status": "canceled"}]}})
    cambiadas = seguimiento.revisar(client)
    assert [c.resultado for c in cambiadas] == ["objetivo"]   # se persiste igual
    assert chat == []


def test_cancelada_por_vencida_queda_en_el_log(chat, monkeypatch, tmp_path):
    r = tseg._revision_con_orden()
    tseg._parchear(monkeypatch, tmp_path, [r])
    _sender_real(monkeypatch)
    monkeypatch.setattr(seguimiento, "entrada_vencida", lambda *a, **k: 30.0)
    client = tseg._FakeClient({"orden-RKLB": {"status": "new", "legs": []}},
                              abiertas=[{"id": "orden-RKLB", "symbol": "RKLB"}])
    cambiadas = seguimiento.revisar(client)
    assert [c.resultado for c in cambiadas] == ["no_ejecutada"]
    assert chat == []


# --------------------------- evaluación: COLOCADA / NO ENTRA ---------------------------

def test_colocada_queda_en_el_log_y_la_orden_se_coloca_igual(chat, monkeypatch, tmp_path):
    e = tex._entrada_triggered()
    tex._parchear(monkeypatch, tmp_path, [e])
    _sender_real(monkeypatch)
    client = tex._FakeAlpacaClient(cash=10_000.0)
    nuevas = executor.ejecutar(client, tex.CFG, dry_run=False, ahora=tex.AHORA)
    assert len(nuevas) == 1 and len(client.ordenes_colocadas) == 1
    assert chat == []


def test_no_entra_de_la_ia_queda_en_el_log(chat, monkeypatch, tmp_path):
    e = tex._entrada_triggered()
    tex._parchear(monkeypatch, tmp_path, [e], decision=tex._DECISION_NO_ENTRA)
    _sender_real(monkeypatch)
    client = tex._FakeAlpacaClient(cash=10_000.0)
    assert executor.ejecutar(client, tex.CFG, dry_run=False, ahora=tex.AHORA) == []
    assert chat == []


# --------------------------- críticos ---------------------------

def test_posicion_sin_stop_sale(chat, monkeypatch):
    client, _ = trec._parchear(
        monkeypatch, [trec._revision("abierta")],
        [{"symbol": "CTAS", "qty": "3"}],
        [{"symbol": "CTAS", "side": "sell", "type": "limit", "id": "tp"}],
    )
    _sender_real(monkeypatch)
    assert reconciliacion.revisar(client, trec._VIERNES) == ["CTAS"]
    assert len(chat) == 1 and "ERROR" in chat[0] and "stop" in chat[0]


def test_cierre_de_fin_de_dia_fallido_sale(chat):
    cierre._avisar_cierre_fallido("CTAS", "sin_confirmar", datetime(2026, 9, 25, 19, 50, tzinfo=UTC))
    assert len(chat) == 1 and "cierre de fin de día rechazado" in chat[0]


def test_posicion_sigue_abierta_tras_la_liquidacion_sale(chat, monkeypatch, tmp_path):
    r = tseg._revision_con_orden(resultado="abierta")
    tseg._parchear(monkeypatch, tmp_path, [r])
    _sender_real(monkeypatch)
    seguimiento._avisar_sigue_abierta(r, "", datetime(2026, 9, 25, 20, 10, tzinfo=UTC))
    assert len(chat) == 1 and "sigue abierta" in chat[0]


def test_stop_diario_disparado_sale(chat, monkeypatch, tmp_path):
    for var in ("MOMENTUM_STOP_DIARIO_MODO", "MOMENTUM_STOP_DIARIO_PCT", "MOMENTUM_STOP_DIARIO_LIQUIDAR"):
        monkeypatch.delenv(var, raising=False)
    e = tex._entrada_triggered(ahora=tex.AHORA - timedelta(minutes=2))
    tex._parchear(monkeypatch, tmp_path, [e])
    _sender_real(monkeypatch)
    client = tex._FakeAlpacaClient(cash=40_000.0, equity=5000.0 - 70, last_equity=5000.0)
    executor.ejecutar(client, tex.CFG, dry_run=False, ahora=tex.AHORA)
    assert [t for t in chat if "STOP DIARIO" in t] and all("STOP DIARIO" in t for t in chat)


def test_las_categorias_criticas_estan_en_su_lugar():
    """Cinturón por si alguien refactoriza: los cuatro avisos críticos y
    la entrada marcan su categoría explícitamente."""
    leer = lambda m: Path(m.__file__).read_text(encoding="utf-8")
    assert "categoria=notify.CATEGORIA_CRITICO" in leer(reconciliacion)
    assert "categoria=notify.CATEGORIA_CRITICO" in leer(cierre)
    assert "categoria=notify.CATEGORIA_CRITICO" in leer(seguimiento)
    assert "notify.CATEGORIA_ENTRADA" in leer(seguimiento)
    from momentum_paper_trader import stop_diario
    assert "categoria=notify.CATEGORIA_CRITICO" in leer(stop_diario)


# --------------------------- avisos de bash (watchdog, persist, IA) ---------------------------

def _correr_sh(env_extra: dict, tmp_path) -> subprocess.CompletedProcess:
    # Sin token: si el filtro deja pasar, el script falla con "missing
    # token" (rc=1) en vez de llamar a la red.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("TELEGRAM_", "MOMENTUM_TELEGRAM"))}
    env["MOMENTUM_TELEGRAM_FLAG_FILE"] = str(tmp_path / "no_existe")
    env.update(env_extra)
    return subprocess.run(["bash", str(NOTIFY_SH), "ERROR [paper][vps] persist fallido"],
                          capture_output=True, text=True, env=env, timeout=20)


def test_bash_info_silenciado_por_default(tmp_path):
    r = _correr_sh({}, tmp_path)
    assert r.returncode == 0 and "SILENCIADO" in r.stdout and "persist fallido" in r.stdout


def test_bash_critico_pasa_el_filtro(tmp_path):
    r = _correr_sh({"TELEGRAM_CATEGORIA": "critico"}, tmp_path)
    assert "SILENCIADO" not in r.stdout and "missing token" in r.stdout


def test_bash_flag_cero_restaura(tmp_path):
    r = _correr_sh({"TELEGRAM_SOLO_ENTRADAS": "0"}, tmp_path)
    assert "SILENCIADO" not in r.stdout and "missing token" in r.stdout


def test_bash_archivo_manda_sobre_la_variable(tmp_path):
    flag = tmp_path / "interruptor"
    flag.write_text("0\n", encoding="utf-8")
    r = _correr_sh({"TELEGRAM_SOLO_ENTRADAS": "1", "MOMENTUM_TELEGRAM_FLAG_FILE": str(flag)}, tmp_path)
    assert "SILENCIADO" not in r.stdout and "missing token" in r.stdout


# --------------------------- uso de la API (directo a requests) ---------------------------

def test_aviso_de_uso_api_silenciado(chat, monkeypatch):
    import requests

    from uso_api import contador
    posts = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: posts.append(k))
    contador._avisar("⚠️ Uso de la API de Alpaca (datos): 80 %")
    assert posts == []
    monkeypatch.setenv("TELEGRAM_SOLO_ENTRADAS", "0")
    contador._avisar("⚠️ Uso de la API de Alpaca (datos): 80 %")
    assert len(posts) == 1
