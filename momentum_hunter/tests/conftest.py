"""El catálogo local de activos, si existe en el árbol de un desarrollador,
no puede colarse en las pruebas: sin archivo el hunter se comporta como
antes. Cada prueba apunta a una ruta que no existe; la que quiera un
archivo fresco vuelve a poner `MOMENTUM_CATALOGO_ACTIVOS`."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _catalogo_de_activos_ausente(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_CATALOGO_ACTIVOS", str(tmp_path / "sin_catalogo_activos.json"))
