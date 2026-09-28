"""Contador de consultas a Alpaca: sin red, en `tmp_path`."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from uso_api import contador as uc

T0 = datetime(2026, 9, 29, 14, 5, 12, tzinfo=UTC)   # 08:05 en Monterrey (UTC−6)


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    monkeypatch.setenv(uc.ENV_DIR, str(tmp_path))
    avisos = []
    monkeypatch.setattr(uc, "_avisar", avisos.append)
    return tmp_path, avisos


def _dia(tmp_path):
    return json.loads((tmp_path / "2026-09-29.json").read_text())


def test_cuenta_por_host_y_por_minuto_utc(carpeta):
    tmp, _ = carpeta
    for _ in range(3):
        uc.registrar(uc.DATOS, T0)
    uc.registrar(uc.TRADING, T0)
    uc.registrar(uc.DATOS, T0 + timedelta(minutes=1))
    d = _dia(tmp)
    assert d["datos"] == {"14:05": 3, "14:06": 1}
    assert d["trading"] == {"14:05": 1}


def test_avisa_al_70_por_ciento_una_vez_y_respeta_la_pausa(carpeta):
    tmp, avisos = carpeta
    for _ in range(139):
        uc.registrar(uc.TRADING, T0)
    assert avisos == []                                   # 139/200 < 70 %
    uc.registrar(uc.TRADING, T0)                          # 140 = 70 %
    uc.registrar(uc.TRADING, T0)
    assert len(avisos) == 1
    assert "140" in avisos[0] and "70%" in avisos[0] and "14:05 UTC" in avisos[0] and "08:05 Monterrey" in avisos[0]
    # 5 min después sigue saturado: todavía dentro de la pausa de 15 min.
    for _ in range(140):
        uc.registrar(uc.TRADING, T0 + timedelta(minutes=5))
    assert len(avisos) == 1
    for _ in range(140):
        uc.registrar(uc.TRADING, T0 + timedelta(minutes=16))
    assert len(avisos) == 2


def test_el_umbral_de_datos_es_7000(carpeta):
    _, avisos = carpeta
    for _ in range(6999):
        uc.registrar(uc.DATOS, T0)
    assert avisos == []
    uc.registrar(uc.DATOS, T0)
    assert len(avisos) == 1 and "10.000" in avisos[0]


def test_el_aviso_no_lleva_credenciales(monkeypatch):
    monkeypatch.setenv("MOMENTUM_TELEGRAM_BOT_TOKEN", "123:secreto")
    assert "secreto" not in uc.texto_aviso(uc.TRADING, 150, T0)


def test_varios_procesos_no_pierden_cuentas(carpeta):
    tmp, _ = carpeta
    hilos = [threading.Thread(target=lambda: [uc.registrar(uc.DATOS, T0) for _ in range(50)]) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert _dia(tmp)["datos"]["14:05"] == 400


def test_sin_directorio_no_hace_nada_ni_rompe(monkeypatch, tmp_path):
    monkeypatch.delenv(uc.ENV_DIR, raising=False)
    uc.registrar(uc.DATOS, T0)              # bajo pytest y sin ENV_DIR: no escribe
    assert not any(tmp_path.iterdir())


def test_un_error_al_contar_no_levanta(carpeta, monkeypatch):
    monkeypatch.setattr(uc, "_incrementar", lambda *a: 1 / 0)
    uc.registrar(uc.DATOS, T0)               # no levanta


def test_host_desconocido_se_ignora(carpeta):
    tmp, _ = carpeta
    uc.registrar("otro", T0)
    assert not (tmp / "2026-09-29.json").exists()


def test_archivo_corrupto_se_reinicia(carpeta):
    tmp, _ = carpeta
    (tmp / "2026-09-29.json").write_text("{no es json")
    uc.registrar(uc.DATOS, T0)
    assert _dia(tmp)["datos"] == {"14:05": 1}


def test_resumen_da_pico_y_minutos_sobre_70(carpeta):
    tmp, _ = carpeta
    for _ in range(150):
        uc.registrar(uc.TRADING, T0)
    for _ in range(3):
        uc.registrar(uc.TRADING, T0 + timedelta(minutes=2))
    r = uc.resumen("2026-09-29", tmp)
    assert r["trading"]["pico"] == 150 and r["trading"]["pico_minuto"] == "14:05"
    assert r["trading"]["minutos_sobre_70"] == 1 and r["trading"]["total"] == 153
    assert r["datos"]["total"] == 0
    texto = uc.formatear("2026-09-29", r)
    assert "pico 150/200" in texto and "sin consultas registradas" in texto


# --------------------------------------------------- los tres puntos de salida


def test_cada_intento_al_host_de_datos_se_cuenta(monkeypatch):
    from momentum_hunter.data import alpaca_datos

    vistos = []
    monkeypatch.setattr(alpaca_datos, "registrar_uso", vistos.append)

    class _R:
        status_code = 429
        headers = {}

    monkeypatch.setattr(alpaca_datos.requests, "get", lambda *a, **kw: _R())
    p = alpaca_datos.AlpacaProvider(api_key="k", api_secret="s", reintentos=3, dormir=lambda s: None)
    with pytest.raises(alpaca_datos.ErrorDatosAlpaca):
        p._get("/v2/stocks/bars", {})
    assert vistos == ["datos"] * 3              # los reintentos también gastan cupo


def test_cada_consulta_al_host_de_trading_se_cuenta(monkeypatch):
    from momentum_paper_trader import alpaca_client

    vistos = []
    monkeypatch.setattr(alpaca_client, "registrar_uso", vistos.append)
    monkeypatch.setattr(alpaca_client.requests, "get", lambda *a, **kw: "ok")
    assert alpaca_client._http("get", "https://paper-api.alpaca.markets/v2/clock") == "ok"
    assert vistos == ["trading"]


def test_el_panel_cuenta_sus_consultas_de_trading(monkeypatch):
    from dashboard import build_dashboard as bd

    vistos = []
    monkeypatch.setattr(uc, "registrar", vistos.append)
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "s")

    def _falla(*a, **kw):
        raise bd.urllib.error.URLError("sin red")

    monkeypatch.setattr(bd.urllib.request, "urlopen", _falla)
    datos, err = bd.alpaca_get("/v2/account")
    assert datos is None and err
    assert vistos == ["trading"]
