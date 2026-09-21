"""Escaneo completo en el VPS sobre el overlay (2026-09-21).

Tres cosas que tienen que ser verdad para que el escaneo (~9 min) y el
rechequeo (cada 5 min) convivan en el mismo host sin pisarse:

  1. El escaneo escribe el CANÓNICO con el overlay aplicado en el instante
     de escribir: un TRIGGERED del rechequeo de hace 3 minutos sobrevive.
  2. Si los dos llegaron a un terminal distinto, gana la decisión anterior.
  3. El candado se toma solo para escribir (milisegundos), nunca durante
     el escaneo: el rechequeo jamás espera al escaneo.

Y el freno de Yahoo: el bot escribe SU archivo de pausa ante un 429 y no
reintenta; nunca obedece un archivo escrito por el panel."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter import run as run_mod
from momentum_hunter import watchlist
from momentum_hunter.data import provider as prov
from momentum_hunter.tests.test_watchlist_vps_state import _activar_vps, _candidato_diario

AHORA = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)


def _state(tmp_path) -> Path:
    return Path(os.environ["MOMENTUM_WATCHLIST_STATE"])


def test_escaneo_escribe_el_canonico_con_el_triggered_del_rechequeo(monkeypatch, tmp_path):
    _activar_vps(monkeypatch, tmp_path)
    canon = tmp_path / "watchlist.json"
    # Foto con la que arrancó el escaneo: RKLB en WATCHING.
    foto = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.guardar([foto], canon)
    # Tres minutos después el rechequeo disparó RKLB en el overlay.
    disparada = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(disparada, "m", "d", "ev", AHORA + timedelta(minutes=3))
    watchlist.guardar_vps_state([disparada], ahora=AHORA + timedelta(minutes=3))
    # El escaneo termina a los 9 min con su foto vieja (RKLB WATCHING) más un ticker nuevo.
    nueva = watchlist.desde_candidato_diario(_candidato_diario("VRA"), AHORA + timedelta(minutes=9))
    escritas = watchlist.guardar_canonico_fusionado([foto, nueva], canon, ahora=AHORA + timedelta(minutes=9))
    por_ticker = {e.ticker: e for e in watchlist.cargar(canon)}
    assert por_ticker["RKLB"].estado == watchlist.ESTADO_TRIGGERED   # no se pisó el disparo
    assert por_ticker["VRA"].estado == watchlist.ESTADO_WATCHING      # el ticker nuevo entró
    assert {e.ticker for e in escritas} == {"RKLB", "VRA"}
    assert watchlist.lock_path().exists()                             # hubo candado


def test_dos_terminales_distintos_gana_la_decision_anterior():
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_missed(canon, "tarde", AHORA + timedelta(minutes=9))     # el escaneo, después
    overlay_e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(overlay_e, "m", "d", "ev", AHORA + timedelta(minutes=3))  # el rechequeo, antes
    overlay = watchlist._entrada_a_overlay(overlay_e, (AHORA + timedelta(minutes=3)).isoformat(timespec="seconds"))
    fused = watchlist._fusionar_overlay(canon, overlay)
    assert fused.estado == watchlist.ESTADO_TRIGGERED
    # Al revés (el canónico decidió antes) el canónico se queda.
    canon2 = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_missed(canon2, "tarde", AHORA + timedelta(minutes=1))
    assert watchlist._fusionar_overlay(canon2, overlay).estado == watchlist.ESTADO_MISSED
    # Empate: canónico, como siempre.
    canon3 = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_missed(canon3, "tarde", AHORA + timedelta(minutes=3))
    assert watchlist._fusionar_overlay(canon3, overlay).estado == watchlist.ESTADO_MISSED


def test_materializar_vuelca_el_overlay_al_canonico(monkeypatch, tmp_path):
    _activar_vps(monkeypatch, tmp_path)
    canon = tmp_path / "watchlist.json"
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.guardar([e], canon)
    e2 = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.expirar_vencidas([e2], minutos_maximos=1, ahora=AHORA + timedelta(hours=3))
    watchlist.guardar_vps_state([e2], ahora=AHORA + timedelta(hours=3))
    assert watchlist.materializar_overlay(canon) == 1
    assert watchlist.cargar(canon)[0].estado == watchlist.ESTADO_EXPIRED
    # Idempotente: una segunda pasada deja el archivo igual.
    antes = canon.read_text()
    watchlist.materializar_overlay(canon)
    assert canon.read_text() == antes


def test_el_candado_no_se_sostiene_fuera_de_la_escritura(monkeypatch, tmp_path):
    import fcntl
    _activar_vps(monkeypatch, tmp_path)
    canon = tmp_path / "watchlist.json"
    watchlist.guardar([], canon)
    watchlist.materializar_overlay(canon)
    # Terminada la escritura, el candado está libre: otro proceso lo toma sin esperar.
    with watchlist.lock_path().open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)   # lanzaría BlockingIOError si siguiera tomado
        fcntl.flock(fh, fcntl.LOCK_UN)


def test_persistir_escaneo_con_flag_devuelve_lo_escrito(monkeypatch, tmp_path):
    _activar_vps(monkeypatch, tmp_path)
    canon = tmp_path / "watchlist.json"
    # Como las pruebas existentes: `guardar`/`cargar` redirigidos al tmp, sin tocar PATH.
    real_cargar, real_guardar = watchlist.cargar, watchlist.guardar
    monkeypatch.setattr(watchlist, "cargar", lambda p=canon, apply_vps_state=False: real_cargar(p))
    monkeypatch.setattr(watchlist, "guardar", lambda es, p=canon, ahora=None: real_guardar(es, p, ahora=ahora))
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.guardar([e])
    e2 = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_invalidated(e2, "x", AHORA + timedelta(minutes=2))
    watchlist.guardar_vps_state([e2], ahora=AHORA + timedelta(minutes=2))
    escritas = run_mod._persistir_escaneo([e])
    assert escritas[0].estado == watchlist.ESTADO_INVALIDATED
    assert watchlist.cargar(canon)[0].estado == watchlist.ESTADO_INVALIDATED


# ───────────────────────── freno de Yahoo ─────────────────────────

class _Resp:
    def __init__(self, status, cuerpo=None):
        self.status_code, self._cuerpo = status, cuerpo or {}

    def json(self):
        return self._cuerpo


def test_429_escribe_la_pausa_del_bot_y_no_reintenta(monkeypatch, tmp_path):
    ruta = tmp_path / "yahoo_pausa_bot.json"
    llamadas = []
    monkeypatch.setattr(prov.requests, "get", lambda *a, **k: llamadas.append(a) or _Resp(429))
    p = prov.YahooProvider(pausa=0, pausa_429=prov.PausaYahoo(str(ruta)))
    assert p.barras(["AAA", "BBB"]) == {}
    assert len(llamadas) == 1                       # el segundo ticker ni se pidió
    pausa = json.loads(ruta.read_text())
    assert pausa["motivo"] == "429" and pausa["origen"] == "bot"
    hasta = datetime.fromisoformat(pausa["hasta"])
    assert timedelta(minutes=14) < hasta - datetime.now(UTC) <= timedelta(minutes=15)


def test_con_la_pausa_del_bot_activa_no_se_pide_nada(monkeypatch, tmp_path):
    ruta = tmp_path / "yahoo_pausa_bot.json"
    ruta.write_text(json.dumps({"hasta": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}))
    def explota(*a, **k):
        raise AssertionError("no debía pedir")
    monkeypatch.setattr(prov.requests, "get", explota)
    p = prov.YahooProvider(pausa=0, pausa_429=prov.PausaYahoo(str(ruta)))
    assert p.barras(["AAA"]) == {} and p.barras_intradia(["AAA"]) == {}


def test_la_pausa_vencida_o_ilegible_no_frena(monkeypatch, tmp_path):
    ruta = tmp_path / "yahoo_pausa_bot.json"
    ruta.write_text(json.dumps({"hasta": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}))
    llamadas = []
    monkeypatch.setattr(prov.requests, "get", lambda *a, **k: llamadas.append(1) or _Resp(200, {"chart": {"result": [{}]}}))
    p = prov.YahooProvider(pausa=0, reintentos=1, pausa_429=prov.PausaYahoo(str(ruta)))
    p.barras(["AAA"])
    ruta.write_text("basura")
    p.barras(["AAA"])
    assert len(llamadas) == 2


def test_el_bot_ignora_la_pausa_escrita_por_el_panel(monkeypatch, tmp_path):
    # El panel escribe SU archivo (yahoo_pausa.json); el bot solo mira el suyo.
    del_panel = tmp_path / "yahoo_pausa.json"
    del_panel.write_text(json.dumps({"hasta": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(), "motivo": "429"}))
    del_bot = tmp_path / "yahoo_pausa_bot.json"
    llamadas = []
    monkeypatch.setattr(prov.requests, "get", lambda *a, **k: llamadas.append(1) or _Resp(200, {"chart": {"result": [{}]}}))
    monkeypatch.setenv(prov.ENV_PAUSA_YAHOO, str(del_bot))
    prov.YahooProvider(pausa=0, reintentos=1).barras(["AAA"])
    assert len(llamadas) == 1 and not del_bot.exists()


def test_sin_variable_no_hay_archivo_pero_el_429_tampoco_se_reintenta(monkeypatch, tmp_path):
    monkeypatch.delenv(prov.ENV_PAUSA_YAHOO, raising=False)
    llamadas = []
    monkeypatch.setattr(prov.requests, "get", lambda *a, **k: llamadas.append(1) or _Resp(429))
    p = prov.YahooProvider(pausa=0)
    assert p.barras(["AAA"]) == {} and len(llamadas) == 1
    assert not list(tmp_path.iterdir())


# ───────────────────────── Telegram del respaldo ─────────────────────────

def test_prefijo_de_respaldo_en_telegram(monkeypatch):
    enviados = []
    monkeypatch.setenv("MOMENTUM_TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("MOMENTUM_TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(run_mod.requests, "post", lambda url, json=None, timeout=None: enviados.append(json["text"]))
    monkeypatch.setenv("MOMENTUM_TELEGRAM_PREFIJO", "[RESPALDO GITHUB]")
    run_mod.enviar_telegram("hola")
    monkeypatch.delenv("MOMENTUM_TELEGRAM_PREFIJO")
    run_mod.enviar_telegram("hola")
    assert enviados == ["[RESPALDO GITHUB] hola", "hola"]
