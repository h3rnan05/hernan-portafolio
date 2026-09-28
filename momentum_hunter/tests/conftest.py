"""Fixtures compartidas de las pruebas del hunter.

1) Guardia de acciones corporativas (`data/acciones_corporativas.py`):
fail-closed sin credenciales. Las pruebas de `run.py` que no tratan de
eso no salen a la red: aquí se reemplazan los dos puntos de entrada por
una guardia vacía y disponible. Las pruebas de la guardia misma vuelven
a parchearlos.

2) Catálogo local de activos: si existe en el árbol de un desarrollador,
no puede colarse en las pruebas. Cada prueba apunta a una ruta que no
existe; la que quiera un archivo fresco vuelve a poner
`MOMENTUM_CATALOGO_ACTIVOS`.
"""

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


@pytest.fixture(autouse=True)
def _catalogo_de_activos_ausente(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_CATALOGO_ACTIVOS", str(tmp_path / "sin_catalogo_activos.json"))
