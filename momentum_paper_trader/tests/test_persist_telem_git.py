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


def test_vps_es_dueno_de_watchlist_y_auditoria():
    """Desde el 2026-09-21 el escaneo corre en el VPS y el VPS es el dueño
    de watchlist.json + auditoria + telemetría del hunter: los commitea
    después de volcar el overlay al canónico. GitHub solo los toca en
    modo respaldo (ver test_escaneo_vps_infra.py)."""
    texto = VPS.read_text(encoding="utf-8")
    bloque = _vps_paths_de_persistencia(texto)
    assert "momentum_hunter/watchlist.json" in bloque
    assert "momentum_hunter/auditoria" in bloque
    assert "momentum_hunter/telemetria" in bloque
    assert "momentum_paper_trader/telemetria" in bloque
    assert "momentum_paper_trader/revisiones.json" in bloque
    assert "momentum_paper_trader/archivo_triggered.jsonl" in bloque
    assert "momentum_hunter/alertas_enviadas.json" in bloque
    assert "--solo-watchlist" in texto
    assert "MOMENTUM_WATCHLIST_VPS_STATE" in texto
    assert "backup_watchlist_vps_state.sh" in texto
    cron = (ROOT / "infra" / "cron" / "momentum-watchlist-state-backup").read_text(encoding="utf-8")
    assert "15 2 * * *" in cron
    assert "/opt/hernan-portafolio/scripts/backup_watchlist_vps_state.sh" in cron


def test_gha_hunter_sigue_persistiendo_watchlist_y_auditoria():
    """GitHub conserva sus git-add para el modo RESPALDO (VPS callado):
    quitarlos dejaría el respaldo sin escritor."""
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


def test_vps_deja_rastro_cuando_el_persist_falla():
    """Un persist que falla no puede ser silencioso: evento para el
    panel (se pinta en rojo) y Telegram, en los dos caminos en que el
    estado se queda sin subir (git agotó reintentos, o no se consiguió
    el flock). Nunca tumba la unidad ni fuerza el push."""
    texto = VPS.read_text(encoding="utf-8")
    assert "persist_fallido()" in texto
    assert 'dashboard.events persist_fallido' in texto
    assert "notify_telegram.sh" in texto
    # Los dos caminos de fallo llaman al registro.
    assert 'persist_fallido "git persist failed"' in texto
    assert 'persist_fallido "flock timeout"' in texto
    # El registro nunca puede hacer fallar al bot: todo con `|| true` o
    # devolviendo 0, y sin `set -e` activo alrededor de la llamada a Telegram.
    assert "return 0\n}" in texto
    assert "git push --force" not in texto


def test_vps_no_repite_el_telegram_de_persist_fallido_cada_corrida():
    """El script corre cada ~5 min: si git sigue caído, un Telegram por
    corrida es spam. Se marca el día del último aviso y no se repite."""
    texto = VPS.read_text(encoding="utf-8")
    assert "persist_fallido.avisado" in texto
    assert 'date -u +%F' in texto


def test_cli_de_eventos_escribe_el_evento_y_nunca_falla(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    ruta = tmp_path / "events.jsonl"
    env = {"DASH_EVENTOS": str(ruta), "PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, "-m", "dashboard.events", "persist_fallido",
                        "motivo=git persist failed", "intentos=5"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    evento = json.loads(ruta.read_text(encoding="utf-8").strip())
    assert evento["tipo"] == "persist_fallido"
    assert evento["motivo"] == "git persist failed" and evento["intentos"] == 5
    assert "ts" in evento
    # Ruta imposible: no escribe, pero tampoco falla (rc 0, sin traza).
    env["DASH_EVENTOS"] = "/proc/no-existe/events.jsonl"
    r = subprocess.run([sys.executable, "-m", "dashboard.events", "persist_fallido", "motivo=x"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert r.returncode == 0 and "Traceback" not in r.stderr
