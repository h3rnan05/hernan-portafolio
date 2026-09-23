"""El despliegue del VPS desde Actions (2026-09-23): solo manual, con la
llave en GitHub Secrets, y el script hace exactamente la secuencia
documentada -- nada más. Estas pruebas fijan eso en el repo."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "deploy_vps.yml"
SCRIPT = ROOT / "scripts" / "deploy_vps.sh"
README = ROOT / "infra" / "systemd" / "README.md"


def test_el_workflow_solo_corre_a_mano_y_no_escribe_en_el_repo():
    yaml = pytest.importorskip("yaml")   # PyYAML no es dependencia del bot; sin él, se salta solo esta
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    disparadores = wf.get("on") or wf.get(True)   # PyYAML lee `on:` como True
    assert list(disparadores) == ["workflow_dispatch"], "nunca por cron ni por push"
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["group"] == "deploy-vps" and wf["concurrency"]["cancel-in-progress"] is False


def test_el_workflow_falla_sin_secretos_y_manda_el_script_por_stdin():
    texto = WF.read_text(encoding="utf-8")
    assert "secrets.VPS_SSH_KEY" in texto and "secrets.VPS_HOST" in texto
    assert "Faltan los secretos" in texto
    assert "bash -s\" < scripts/deploy_vps.sh" in texto, "el VPS no tiene por qué tener ya el script"
    assert "chmod 600 ~/.ssh/vps" in texto and "rm -f ~/.ssh/vps" in texto
    # La llave nunca se imprime: ningún echo/cat sobre ella ni sobre el archivo.
    assert not re.search(r"(echo|cat|printf)[^\n]*VPS_SSH_KEY[^\n]*\n[^\n]*>\s*/dev/std", texto)
    assert "cat ~/.ssh/vps" not in texto and 'echo "$VPS_SSH_KEY"' not in texto


def test_el_script_pasa_bash_n_es_ejecutable_y_falla_en_el_primer_error():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    texto = SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in texto


def test_el_script_hace_la_secuencia_documentada_y_nada_mas():
    texto = SCRIPT.read_text(encoding="utf-8")
    # La misma lista de estado que el README (pull con la telemetría sucia).
    for p in ("momentum_paper_trader/telemetria", "momentum_hunter/telemetria",
              "momentum_paper_trader/revisiones.json", "momentum_paper_trader/archivo_triggered.jsonl",
              "momentum_hunter/watchlist.json", "momentum_hunter/auditoria",
              "momentum_hunter/alertas_enviadas.json", "momentum_hunter/estado_diario.json",
              "momentum_hunter/universo_cache.json"):
        assert p in texto, p
    assert "git stash push --include-untracked" in texto
    assert "git pull --rebase origin main" in texto
    assert "git stash pop" in texto
    assert "install -m 755 infra/systemd/bin/run_watchlist_paper.sh infra/systemd/bin/run_scan_paper.sh" in texto
    # Lo que NUNCA hace: forzar, resetear, reiniciar servicios, tocar
    # credenciales. Se mira el código, no los comentarios que lo explican.
    codigo = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))
    for prohibido in ("--force", "reset --hard", "systemctl restart", "systemctl stop", "paper.env", "rm -rf"):
        assert prohibido not in codigo, prohibido


def test_el_script_se_niega_en_sesion_salvo_que_se_fuerce(tmp_path):
    """Corre el script contra un repo falso con el reloj de sesión: debe
    salir con 3 sin tocar nada. Con FORZAR_EN_SESION=1 sigue (y falla más
    adelante por no ser el VPS, lo cual está bien para esta prueba)."""
    falso = tmp_path / "bin"
    falso.mkdir()
    # `date` falso: martes 15:00 UTC, en sesión.
    (falso / "date").write_text("#!/usr/bin/env bash\ncase \"$*\" in *%u*) echo 2;; *%H%M*) echo 1500;; *) echo 15:00;; esac\n")
    (falso / "date").chmod(0o755)
    env = {**os.environ, "PATH": f"{falso}:{os.environ['PATH']}", "REPO": str(tmp_path / "no-existe")}
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert r.returncode == 3 and "en sesión" in r.stdout
    r2 = subprocess.run(["bash", str(SCRIPT)], env={**env, "FORZAR_EN_SESION": "1"}, capture_output=True, text=True)
    assert r2.returncode != 3, "forzado, pasa la ventana (y falla después por el repo inexistente)"


def test_el_readme_documenta_el_despliegue_desde_actions():
    texto = README.read_text(encoding="utf-8")
    assert "deploy_vps.yml" in texto and "VPS_SSH_KEY" in texto and "VPS_HOST" in texto
