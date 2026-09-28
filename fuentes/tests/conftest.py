"""Fixtures del paquete: caché en tmp, sin red, sin credenciales heredadas."""

from __future__ import annotations

import pytest

from fuentes import cache as cache_mod


@pytest.fixture(autouse=True)
def _cache_en_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv(cache_mod.ENV_DIR, str(tmp_path / "fuentes_cache"))
    for var in ("FUENTES_SEC_USER_AGENT", "FINNHUB_API_KEY", "FUENTES_USER_AGENT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cache(tmp_path):
    return cache_mod.Cache(tmp_path / "fuentes_cache")


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    import requests

    def _prohibido(*a, **k):
        raise AssertionError("las pruebas de fuentes no salen a la red")

    monkeypatch.setattr(requests, "get", _prohibido)
    monkeypatch.setattr(requests, "post", _prohibido)
