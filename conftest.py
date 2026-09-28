"""Las pruebas no migran el árbol real ni escriben en /var/lib.

`RutaEstado` resuelve `MOMENTUM_ESTADO_DIR` en cada uso. Sin esto, un
test que toca el default copiaría la telemetría versionada (decenas de
MB) o intentaría crear `/var/lib/momentum/estado`.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _estado_fuera_del_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path / "estado"))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "0")
