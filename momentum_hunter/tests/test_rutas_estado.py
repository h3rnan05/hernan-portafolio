"""El estado sale del checkout. Copia una vez, no pisa, y las pruebas
no arrastran el árbol real."""

from __future__ import annotations

from pathlib import Path

from momentum_hunter import rutas_estado

ROOT = Path(__file__).resolve().parents[2]


def test_el_default_honra_el_entorno(monkeypatch, tmp_path):
    destino = tmp_path / "estado"
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(destino))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "0")
    ruta = rutas_estado.RutaEstado("momentum_hunter/watchlist.json")
    assert ruta.resolver() == destino / "momentum_hunter" / "watchlist.json"
    assert not ruta.exists()


def test_migra_una_vez_y_no_pisa(monkeypatch, tmp_path):
    legado_root = tmp_path / "repo"
    archivo = legado_root / "momentum_paper_trader" / "revisiones.json"
    archivo.parent.mkdir(parents=True)
    archivo.write_text('{"revisiones":[{"ticker":"SKHY"}]}', encoding="utf-8")
    destino = tmp_path / "estado"
    monkeypatch.setattr(rutas_estado, "_REPO", legado_root)
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(destino))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "1")
    ruta = rutas_estado.RutaEstado("momentum_paper_trader/revisiones.json")
    assert "SKHY" in ruta.read_text(encoding="utf-8")
    archivo.write_text("mas nuevo en el legado", encoding="utf-8")
    assert "SKHY" in ruta.read_text(encoding="utf-8")


def test_con_migracion_apagada_no_copia(monkeypatch, tmp_path):
    legado_root = tmp_path / "repo"
    archivo = legado_root / "momentum_hunter" / "alertas_enviadas.json"
    archivo.parent.mkdir(parents=True)
    archivo.write_text("[]", encoding="utf-8")
    destino = tmp_path / "estado"
    monkeypatch.setattr(rutas_estado, "_REPO", legado_root)
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(destino))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "0")
    ruta = rutas_estado.RutaEstado("momentum_hunter/alertas_enviadas.json")
    assert not ruta.exists()
    assert archivo.read_text(encoding="utf-8") == "[]"


def test_listas_de_migracion_coinciden_y_gitignore_las_cubre():
    script = (ROOT / "scripts" / "migrar_estado_fuera_del_repo.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_vps.sh").read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for rel in rutas_estado.RELATIVOS:
        assert rel in script, rel
        assert rel in deploy, rel
        assert f"/{rel}" in ignore, rel
    assert "vps_latido.json" not in ignore
