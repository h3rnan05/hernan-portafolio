"""Job del catálogo de activos. HTTP simulado: no sale a la red y no
usa claves de verdad. Un fallo no puede dejar el archivo a medias."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from momentum_paper_trader import assets_job
from momentum_paper_trader.alpaca_client import _BASE_URL

AHORA = datetime(2026, 9, 28, 12, 5, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]


def _fila(symbol, **extra):
    base = {
        "id": "no-se-guarda",
        "class": "us_equity",
        "symbol": symbol,
        "exchange": "NASDAQ",
        "name": f"{symbol} Inc",
        "status": "active",
        "tradable": True,
        "marginable": True,
        "shortable": True,
        "easy_to_borrow": True,
        "fractionable": False,
    }
    base.update(extra)
    return base


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _previo(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fecha_generacion":"2026-09-27T12:05:00+00:00","assets":[{"symbol":"VIEJO"}]}\n', encoding="utf-8")


def _correr(monkeypatch, path: Path, paginas: list, secret="SECRETO-NO-LOG"):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "KEY-PAPER")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", secret)
    llamadas = []

    def _get(url, params=None, headers=None, timeout=None):
        llamadas.append((url, dict(params or {}), dict(headers or {}), timeout))
        cuerpo = paginas[len(llamadas) - 1]
        if isinstance(cuerpo, Exception):
            raise cuerpo
        status, payload = cuerpo if isinstance(cuerpo, tuple) else (200, cuerpo)
        return _Resp(payload, status)

    def _post(*_a, **_k):
        raise AssertionError("el job no coloca ni modifica nada")

    monkeypatch.setattr(assets_job.requests, "get", _get)
    monkeypatch.setattr(assets_job.requests, "post", _post)
    return llamadas


def _leer(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_el_host_es_el_paper_y_no_el_real():
    assert _BASE_URL == "https://paper-api.alpaca.markets/v2"
    assert assets_job._URL_ASSETS == f"{_BASE_URL}/assets"
    assert not assets_job._URL_ASSETS.startswith("https://api.alpaca.markets")
    fuente = Path(assets_job.__file__).read_text(encoding="utf-8")
    assert "https://api.alpaca.markets" not in fuente


def test_lista_completa_en_una_respuesta_y_sin_campos_que_no_decidimos(monkeypatch, tmp_path):
    path = tmp_path / "datos" / "alpaca_assets.json"
    _previo(path)
    llamadas = _correr(monkeypatch, path, [[_fila("BBB"), _fila("AAA", fractionable=True, tradable=False)]])
    n = assets_job.correr(salida=path, ahora=AHORA)
    assert n == 2
    assert len(llamadas) == 1
    url, params, headers, timeout = llamadas[0]
    assert url == "https://paper-api.alpaca.markets/v2/assets"
    assert params == {"status": "active", "asset_class": "us_equity"}
    assert headers["APCA-API-KEY-ID"] == "KEY-PAPER"
    assert headers["APCA-API-SECRET-KEY"] == "SECRETO-NO-LOG"
    assert timeout == assets_job._TIMEOUT_S
    data = _leer(path)
    assert data["fecha_generacion"] == "2026-09-28T12:05:00+00:00"
    assert [a["symbol"] for a in data["assets"]] == ["AAA", "BBB"]
    aaa = data["assets"][0]
    assert aaa == {
        "symbol": "AAA",
        "exchange": "NASDAQ",
        "tradable": False,
        "fractionable": True,
        "status": "active",
        "name": "AAA Inc",
    }
    texto = path.read_text(encoding="utf-8")
    for sobra in ("shortable", "marginable", "easy_to_borrow", "SECRETO-NO-LOG", "KEY-PAPER", "VIEJO"):
        assert sobra not in texto
    assert list(path.parent.glob(".alpaca_assets.*.tmp")) == []


def test_paginacion_sigue_el_token_y_la_ultima_fila_gana(monkeypatch, tmp_path):
    path = tmp_path / "alpaca_assets.json"
    llamadas = _correr(monkeypatch, path, [
        {"assets": [_fila("AAA", tradable=False), _fila("BBB")], "next_page_token": " pag-2 "},
        {"assets": [_fila("CCC"), _fila("AAA", tradable=True, exchange="NYSE")], "next_page_token": None},
    ])
    assert assets_job.correr(salida=path, ahora=AHORA) == 3
    assert llamadas[0][1] == {"status": "active", "asset_class": "us_equity"}
    assert llamadas[1][1]["page_token"] == "pag-2"
    assert llamadas[1][1]["status"] == "active"
    data = _leer(path)
    por = {a["symbol"]: a for a in data["assets"]}
    assert set(por) == {"AAA", "BBB", "CCC"}
    assert por["AAA"]["tradable"] is True
    assert por["AAA"]["exchange"] == "NYSE"


def test_un_bool_que_no_es_bool_queda_en_null(monkeypatch, tmp_path):
    path = tmp_path / "alpaca_assets.json"
    _correr(monkeypatch, path, [[_fila("AAA", tradable="false", fractionable=0, status=" active ")]])
    assets_job.correr(salida=path, ahora=AHORA)
    aaa = _leer(path)["assets"][0]
    assert aaa["tradable"] is None
    assert aaa["fractionable"] is None
    assert aaa["status"] == "active"


def test_http_no_sobrescribe_el_archivo_previo(monkeypatch, tmp_path, caplog):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    _correr(monkeypatch, path, [(500, {"message": "no"})])
    caplog.set_level("ERROR", logger="momentum_paper_trader.assets_job")
    with pytest.raises(assets_job.ErrorCatalogo) as exc:
        assets_job.correr(salida=path, ahora=AHORA)
    assert exc.value.codigo == "http_500"
    assert path.read_text(encoding="utf-8") == antes
    assert "SECRETO-NO-LOG" not in caplog.text
    assert list(path.parent.glob(".alpaca_assets.*.tmp")) == []


def test_la_segunda_pagina_que_falla_no_publica_la_primera(monkeypatch, tmp_path):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    _correr(monkeypatch, path, [
        {"assets": [_fila("AAA")], "next_page_token": "siguiente"},
        (503, {"assets": [_fila("BBB")]}),
    ])
    with pytest.raises(assets_job.ErrorCatalogo) as exc:
        assets_job.correr(salida=path, ahora=AHORA)
    assert exc.value.codigo == "http_503"
    assert path.read_text(encoding="utf-8") == antes
    assert "AAA" not in path.read_text(encoding="utf-8")


def test_error_de_red_no_registra_el_texto_ni_toca_el_archivo(monkeypatch, tmp_path, caplog):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    _correr(
        monkeypatch, path,
        [requests.ConnectionError("https://paper-api.alpaca.markets/v2/assets SECRETO-NO-LOG")],
    )
    caplog.set_level("ERROR", logger="momentum_paper_trader.assets_job")
    with pytest.raises(assets_job.ErrorCatalogo) as exc:
        assets_job.correr(salida=path, ahora=AHORA)
    assert exc.value.codigo == "red"
    assert path.read_text(encoding="utf-8") == antes
    assert "SECRETO-NO-LOG" not in caplog.text
    assert "ConnectionError" in caplog.text


def test_cuerpo_ilegible_y_lista_vacia_no_sobrescriben(monkeypatch, tmp_path):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    _correr(monkeypatch, path, [(200, {"message": "ok"})])
    with pytest.raises(assets_job.ErrorCatalogo) as exc:
        assets_job.correr(salida=path, ahora=AHORA)
    assert exc.value.codigo == "cuerpo"
    assert path.read_text(encoding="utf-8") == antes

    _correr(monkeypatch, path, [[]])
    with pytest.raises(assets_job.ErrorCatalogo) as vacio:
        assets_job.correr(salida=path, ahora=AHORA)
    assert vacio.value.codigo == "vacio"
    assert path.read_text(encoding="utf-8") == antes


def test_token_repetido_no_escribe(monkeypatch, tmp_path):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    _correr(monkeypatch, path, [
        {"assets": [_fila("AAA")], "next_page_token": "otra-vez"},
        {"assets": [_fila("BBB")], "next_page_token": "otra-vez"},
    ])
    with pytest.raises(assets_job.ErrorCatalogo) as exc:
        assets_job.correr(salida=path, ahora=AHORA)
    assert exc.value.codigo == "paginacion"
    assert path.read_text(encoding="utf-8") == antes


def test_sin_credenciales_no_llama_y_no_toca_el_archivo(monkeypatch, tmp_path):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    llamadas = []
    monkeypatch.setattr(assets_job.requests, "get", lambda *_a, **_k: llamadas.append(1))
    with pytest.raises(assets_job.ErrorCatalogo) as exc:
        assets_job.correr(salida=path, ahora=AHORA)
    assert exc.value.codigo == "sin_credenciales"
    assert llamadas == []
    assert path.read_text(encoding="utf-8") == antes


def test_fallo_antes_del_replace_conserva_el_archivo_y_borra_el_temporal(monkeypatch, tmp_path):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    _correr(monkeypatch, path, [[_fila("AAA")]])

    def _revienta(_src, _dst):
        raise OSError("disco lleno")

    monkeypatch.setattr(assets_job.os, "replace", _revienta)
    with pytest.raises(assets_job.ErrorCatalogo) as exc:
        assets_job.correr(salida=path, ahora=AHORA)
    assert exc.value.codigo == "escritura"
    assert path.read_text(encoding="utf-8") == antes
    assert list(tmp_path.glob(".alpaca_assets.*.tmp")) == []


def test_main_devuelve_1_sin_pisar_y_0_cuando_escribe(monkeypatch, tmp_path, caplog):
    path = tmp_path / "alpaca_assets.json"
    _previo(path)
    antes = path.read_text(encoding="utf-8")
    _correr(monkeypatch, path, [(401, {})])
    caplog.set_level("ERROR", logger="momentum_paper_trader.assets_job")
    assert assets_job.main(["--salida", str(path)]) == 1
    assert path.read_text(encoding="utf-8") == antes
    assert "SECRETO-NO-LOG" not in caplog.text

    _correr(monkeypatch, path, [[_fila("AAA")]])
    assert assets_job.main(["--salida", str(path)]) == 0
    assert _leer(path)["assets"][0]["symbol"] == "AAA"


def test_el_json_esta_en_gitignore_y_la_ruta_es_la_que_lee_el_hunter(monkeypatch):
    texto = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "momentum_hunter/datos/alpaca_assets.json" in texto
    monkeypatch.delenv("MOMENTUM_CATALOGO_ACTIVOS", raising=False)
    monkeypatch.delenv("MOMENTUM_ESTADO_DIR", raising=False)
    defecto = assets_job.ruta_por_defecto()
    assert defecto == Path("/var/lib/momentum/estado/datos/alpaca_assets.json")
    assert REPO not in defecto.parents
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", "/tmp/estado-momentum")
    assert assets_job.ruta_por_defecto() == Path("/tmp/estado-momentum/datos/alpaca_assets.json")
    monkeypatch.setenv("MOMENTUM_CATALOGO_ACTIVOS", "/tmp/otro.json")
    assert assets_job.ruta_por_defecto() == Path("/tmp/otro.json")


def test_timer_diario_1205_utc_y_el_readme_dice_como_habilitarlo():
    raiz = REPO / "infra" / "systemd"
    timer = (raiz / "momentum-assets.timer").read_text(encoding="utf-8")
    servicio = (raiz / "momentum-assets.service").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 12:05:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "NO se habilita solo" in timer
    assert "ExecStart=/opt/momentum/bin/run_assets.sh" in servicio
    assert "EnvironmentFile=/etc/momentum/paper.env" in servicio
    assert "StateDirectory=momentum/estado/datos" in servicio
    assert "ReadWritePaths=/var/lib/momentum/estado" in servicio
    assert "Environment=MOMENTUM_ESTADO_DIR=/var/lib/momentum/estado" in servicio
    readme = (raiz / "README.md").read_text(encoding="utf-8")
    assert "systemctl enable --now momentum-assets.timer" in readme
    assert "NO se habilita solo" in readme
    assert "mkdir -p /var/lib/momentum/estado/datos" in readme
    assert "chown momentum:momentum" in readme
    wrapper = (raiz / "bin" / "run_assets.sh").read_text(encoding="utf-8")
    assert "momentum_paper_trader.assets_job" in wrapper
    assert 'MOMENTUM_ESTADO_DIR="${MOMENTUM_ESTADO_DIR:-/var/lib/momentum/estado}"' in wrapper
    assert os.access(raiz / "bin" / "run_assets.sh", os.X_OK)
