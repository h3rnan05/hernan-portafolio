"""Invariantes de persistencia git de la telemetría paper.

No corre git de verdad. Solo verifica que GHA no commitee el diario
que escribe el VPS, que el push nunca se fuerza, y que el script del
VPS toma flock + fuente `vps`."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSIST = ROOT / "scripts" / "git_persist_rebase_push.sh"
VPS = ROOT / "scripts" / "run_watchlist_paper.sh"
GATTR = ROOT / ".gitattributes"
HUNTER_WF = ROOT / ".github" / "workflows" / "momentum_hunter.yml"
WATCHLIST_WF = ROOT / ".github" / "workflows" / "momentum_hunter_watchlist.yml"


def test_scripts_bash_n():
    for path in (PERSIST, VPS):
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_script_persist_existe_y_rebasea_sin_force():
    texto = PERSIST.read_text(encoding="utf-8")
    assert "pull --rebase" in texto
    assert "PERSIST_MAX_INTENTOS" in texto
    assert "RANDOM" in texto  # jitter
    # El único `--force` permitido es el que se RECHAZA.
    assert "force push is forbidden" in texto
    assert "git push --force" not in texto
    assert "force-with-lease" in texto
    assert "--force)" in texto or "--force|" in texto


def _vps_paths_de_persistencia(texto: str) -> str:
    """Solo el array `paths=(...)` de persistir_estado. Un comentario
    que nombre watchlist/auditoria (el POR QUÉ no se staggean) no
    cuenta como git-add."""
    inicio = texto.find("paths=(")
    assert inicio != -1, "persistir_estado sin paths="
    fin = texto.find(")", inicio)
    assert fin != -1, "paths= sin cierre"
    return texto[inicio:fin]


def test_vps_es_escritor_primario_con_flock():
    texto = VPS.read_text(encoding="utf-8")
    assert "MOMENTUM_TELEM_FUENTE=vps" in texto
    assert "flock" in texto
    assert "momentum_paper_trader/telemetria" in texto
    assert "git_persist_rebase_push.sh" in texto
    assert "git push --force" not in texto
    assert "force-with-lease" not in texto


def test_vps_no_stagea_watchlist_ni_auditoria():
    """GHA discovery es el dueño de watchlist.json + auditoria.
    El VPS las actualiza en local para paper (--solo-watchlist) pero
    no las git-add: dos escritores reventaban el rebase
    (CONFLICT, run 34641814733)."""
    texto = VPS.read_text(encoding="utf-8")
    bloque = _vps_paths_de_persistencia(texto)
    assert "momentum_hunter/watchlist.json" not in bloque
    assert "momentum_hunter/auditoria" not in bloque
    assert "momentum_paper_trader/telemetria" in bloque
    assert "momentum_paper_trader/revisiones.json" in bloque
    assert "momentum_paper_trader/archivo_triggered.jsonl" in bloque
    assert "momentum_hunter/alertas_enviadas.json" in bloque
    assert "momentum_hunter/telemetria" in bloque
    # Sigue corriendo --solo-watchlist: paper lee el JSON local.
    assert "--solo-watchlist" in texto


def test_gha_hunter_sigue_persistiendo_watchlist_y_auditoria():
    """El dueño de discovery no se mueve. Quitar estos git-add
    dejaría watchlist/auditoria sin escritor en git."""
    texto = HUNTER_WF.read_text(encoding="utf-8")
    assert "momentum_hunter/watchlist.json" in texto
    assert "momentum_paper_trader/archivo_triggered.jsonl" in texto
    assert "git add momentum_hunter/auditoria" in texto
    assert "git add momentum_hunter/telemetria" in texto


def test_gha_watchlist_sigue_como_escritor_secundario():
    """Fallback de re-chequeo: no se apaga ni se le quita el
    git-add de watchlist/auditoria. El overlap con hunter GHA
    ya estaba aceptado; el VPS ya no es el tercer escritor."""
    texto = WATCHLIST_WF.read_text(encoding="utf-8")
    assert "momentum_hunter/watchlist.json" in texto
    assert "git add momentum_hunter/auditoria" in texto
    assert 'cron: "*/5 13-20 * * 1-5"' in texto


def test_gitattributes_union_en_jsonl_de_telemetria():
    texto = GATTR.read_text(encoding="utf-8")
    assert "momentum_paper_trader/telemetria/**/*.jsonl merge=union" in texto
    assert "momentum_hunter/telemetria/**/*.jsonl merge=union" in texto
    assert "momentum_paper_trader/archivo_triggered.jsonl merge=union" in texto


def test_workflows_gha_no_agregan_paper_telem():
    for path in (HUNTER_WF, WATCHLIST_WF):
        texto = path.read_text(encoding="utf-8")
        assert "git add momentum_paper_trader/telemetria" not in texto
        assert "git add momentum_hunter/telemetria" in texto
        assert "git_persist_rebase_push.sh" in texto
