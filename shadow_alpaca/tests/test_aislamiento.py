"""La sombra no entra al camino que opera, y ese camino no la importa."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from shadow_alpaca.cliente import armar_url
from shadow_alpaca.jsonl_log import exigir_directorio_aislado

RAIZ = Path(__file__).resolve().parents[2]
PAQUETE = RAIZ / "shadow_alpaca"
OPERATIVOS = ("momentum_hunter", "momentum_paper_trader")
HOSTS_DE_TRADING = (
    "paper-api.alpaca.markets",
    "https://api.alpaca.markets",
    "broker-api.alpaca.markets",
)


def _py_de(directorio: Path):
    return [p for p in directorio.rglob("*.py") if p.is_file()]


def test_los_paquetes_operativos_no_importan_la_sombra():
    for nombre in OPERATIVOS:
        for path in _py_de(RAIZ / nombre):
            texto = path.read_text(encoding="utf-8")
            assert "shadow_alpaca" not in texto, path
            arbol = ast.parse(texto)
            for nodo in ast.walk(arbol):
                if isinstance(nodo, ast.Import):
                    mods = [a.name for a in nodo.names]
                elif isinstance(nodo, ast.ImportFrom):
                    mods = [nodo.module or ""]
                else:
                    continue
                for mod in mods:
                    assert mod != "shadow_alpaca" and not mod.startswith("shadow_alpaca."), path


def test_la_sombra_no_importa_el_paper_trader_ni_el_host_de_trading():
    for path in _py_de(PAQUETE):
        if path.name.startswith("test_"):
            continue
        texto = path.read_text(encoding="utf-8")
        # El nombre del paquete puede aparecer para RECHAZAR escribir ahí.
        # Lo que no puede aparecer es un import: eso engancharía la sombra
        # al ejecutor.
        assert "import momentum_paper_trader" not in texto, path
        assert "from momentum_paper_trader" not in texto, path
        for host in HOSTS_DE_TRADING:
            assert host not in texto, (path, host)
        arbol = ast.parse(texto)
        for nodo in ast.walk(arbol):
            mods: list[str] = []
            if isinstance(nodo, ast.Import):
                mods = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                mods = [nodo.module]
            for mod in mods:
                assert not mod.startswith("momentum_paper_trader"), path


def test_el_unico_host_de_alpaca_es_el_de_datos():
    assert armar_url("/v1beta1/news") == "https://data.alpaca.markets/v1beta1/news"
    with pytest.raises(Exception):
        armar_url("https://paper-api.alpaca.markets/v2/orders")
    with pytest.raises(Exception):
        armar_url("https://api.alpaca.markets/v2/orders")


def test_no_se_puede_escribir_dentro_del_hunter_ni_del_paper(tmp_path):
    for nombre in OPERATIVOS:
        with pytest.raises(ValueError, match="directorio_operativo"):
            exigir_directorio_aislado(RAIZ / nombre / "telemetria")
    exigir_directorio_aislado(tmp_path / "salida")


def test_las_keywords_no_estan_copiadas_en_la_sombra():
    """Si alguien pega la lista, este texto aparece. Tiene que seguir
    siendo el objeto del hunter, no una copia que se pueda desactualizar."""
    from momentum_hunter.catalysts import detector
    from shadow_alpaca import noticias

    fuente = (PAQUETE / "noticias.py").read_text(encoding="utf-8")
    assert "fda approval" not in fuente
    assert noticias.CATALYST_KEYWORDS is detector.CATALYST_KEYWORDS
    assert noticias.detectar_catalizador is detector.detectar_catalizador


def test_unidades_shadow_no_son_el_camino_operativo_y_no_se_instalan_solas():
    sombra = RAIZ / "infra" / "systemd" / "shadow"
    for nombre in (
        "momentum-shadow-noticias.timer",
        "momentum-shadow-screener.timer",
    ):
        texto = (sombra / nombre).read_text(encoding="utf-8")
        assert "NO se instala solo" in texto
        assert "shadow" in nombre
    for nombre in ("run_shadow_noticias.sh", "run_shadow_screener.sh"):
        texto = (RAIZ / "infra" / "systemd" / "bin" / nombre).read_text(encoding="utf-8")
        assert 'SHADOW_ALPACA:-0}" != "1"' in texto
        assert "flock -n 9" in texto
        assert "momentum-paper-git.lock" not in texto
        codigo = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))
        assert "watchlist.json" not in codigo
        assert "git " not in codigo
    readme = (RAIZ / "infra" / "systemd" / "README.md").read_text(encoding="utf-8")
    assert "SHADOW_ALPACA=1" in readme
    assert "momentum-shadow-noticias.timer" in readme
    assert "/var/lib/momentum/shadow_alpaca" in readme
