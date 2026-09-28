"""Un solo proceso versiona. El resto no invoca git."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SIN_GIT = (
    ROOT / "infra" / "systemd" / "bin" / "run_scan_paper.sh",
    ROOT / "infra" / "systemd" / "bin" / "run_movers_sombra.sh",
    ROOT / "infra" / "systemd" / "bin" / "run_vigia.sh",
    ROOT / ".github" / "workflows" / "momentum_hunter.yml",
    ROOT / ".github" / "workflows" / "momentum_hunter_watchlist.yml",
    ROOT / ".github" / "workflows" / "momentum_hunter_outcomes.yml",
    ROOT / ".github" / "scripts" / "momentum_paper_cadence_bridge.py",
)

CON_GIT = (
    ROOT / "infra" / "systemd" / "bin" / "run_watchlist_paper.sh",
    ROOT / "scripts" / "run_watchlist_paper.sh",
)


def test_nadie_fuera_del_wrapper_de_rechequeo_invoca_git():
    for path in SIN_GIT:
        texto = path.read_text(encoding="utf-8")
        assert "git " not in texto, path
        assert "git_pull" not in texto, path
        assert "git_persist" not in texto, path


def test_el_wrapper_de_rechequeo_es_el_unico_que_sube_el_latido():
    for path in CON_GIT:
        texto = path.read_text(encoding="utf-8")
        assert "git_pull_con_estado_local.sh" in texto, path
        assert "git add -- vps_latido.json" in texto, path
        assert "flock" in texto, path


def test_las_unidades_declaran_el_directorio_de_estado():
    for nombre in (
        "momentum-vigia.service",
        "momentum-scan.service",
        "momentum-movers-sombra.service",
        "momentum-watchlist.service",
    ):
        texto = (ROOT / "infra" / "systemd" / nombre).read_text(encoding="utf-8")
        assert "StateDirectory=momentum" in texto, nombre
        assert "ReadWritePaths=/var/lib/momentum" in texto, nombre
        assert "ProtectSystem=strict" not in texto, nombre
