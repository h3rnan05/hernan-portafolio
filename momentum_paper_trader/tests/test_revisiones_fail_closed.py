"""Si el libro no se puede leer, no se abren entradas nuevas."""

from __future__ import annotations

import pytest

from momentum_hunter import rutas_estado
from momentum_paper_trader import estado, executor


def test_legado_que_no_se_pudo_copiar_no_es_libro_vacio(monkeypatch, tmp_path):
    legado_root = tmp_path / "repo"
    archivo = legado_root / "momentum_paper_trader" / "revisiones.json"
    archivo.parent.mkdir(parents=True)
    archivo.write_text('{"revisiones":[{"ticker":"SKHY"}]}', encoding="utf-8")
    monkeypatch.setattr(rutas_estado, "_REPO", legado_root)
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path / "estado"))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "1")

    def _falla(*_a, **_k):
        raise OSError("disco")

    monkeypatch.setattr(rutas_estado.shutil, "copy2", _falla)
    with pytest.raises(estado.RevisionesIlegibles) as exc:
        estado.cargar()
    assert exc.value.origen == "migracion"
    assert "SKHY" not in str(exc.value)


def test_ejecutar_no_abre_entradas_si_el_libro_es_ilegible(monkeypatch):
    def _falla(*_a, **_k):
        raise estado.RevisionesIlegibles("JSONDecodeError")

    monkeypatch.setattr(estado, "cargar", _falla)
    llamadas = []
    monkeypatch.setattr(executor, "_evento", lambda *a, **k: llamadas.append(a))

    class Cliente:
        def __getattr__(self, nombre):
            raise AssertionError(nombre)

    assert executor.ejecutar(Cliente(), dry_run=True) == []
    assert llamadas == []
