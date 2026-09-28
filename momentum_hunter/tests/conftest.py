"""Fixtures compartidas de las pruebas del hunter.

La guardia de acciones corporativas (`data/acciones_corporativas.py`) es
fail-closed: sin credenciales bloquea todo disparo. Las pruebas de
`run.py` que no tratan de eso no tienen por qué salir a la red ni
heredar ese bloqueo, así que aquí se reemplazan los dos puntos de
entrada por una guardia vacía y disponible. Las pruebas de la guardia
misma vuelven a parchearlos (el monkeypatch de la prueba gana)."""

from __future__ import annotations

import pytest

from momentum_hunter.data import acciones_corporativas as acc_corp


@pytest.fixture(autouse=True)
def _guardia_corporativa_sin_red(monkeypatch):
    from momentum_hunter import run as run_mod

    def _historia(barras, ahora):
        return barras, acc_corp.Guardia(fecha=acc_corp.hoy_ny(ahora), disponible=True)

    def _guardia(tickers, ahora, base=None):
        return acc_corp.Guardia(fecha=acc_corp.hoy_ny(ahora), disponible=True)

    monkeypatch.setattr(run_mod, "_verificar_historia_corporativa", _historia)
    monkeypatch.setattr(run_mod, "_guardia_corporativa", _guardia)
    # Nunca escribir el JSONL real desde una prueba.
    monkeypatch.setenv(acc_corp.ENV_LOG, "/dev/null")
