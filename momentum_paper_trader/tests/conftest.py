"""Fixtures compartidas de las pruebas del paper trader."""

import pytest


@pytest.fixture(autouse=True)
def _eventos_del_panel_en_tmp(tmp_path, monkeypatch):
    """El ejecutor escribe eventos para el panel (`dashboard.events`). En
    pruebas van a un archivo temporal, nunca a `logs/events.jsonl` del repo.
    Una prueba que necesite otra ruta puede volver a llamar a `setenv`."""
    monkeypatch.setenv("DASH_EVENTOS", str(tmp_path / "panel" / "events.jsonl"))
    # La racha de fallos de IA no debe escribir en /var/lib durante las pruebas.
    monkeypatch.setenv("MOMENTUM_AVISOS_DIR", str(tmp_path / "avisos"))
