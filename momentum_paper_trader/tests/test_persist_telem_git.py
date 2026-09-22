"""Invariantes de persistencia git de la telemetría paper.

Verifica que GHA no commitee el diario que escribe el VPS, que el push
nunca se fuerza, que el script del VPS toma flock + fuente `vps`, y que
un pull con la telemetría sucia trae main sin perder esas líneas (repo
git temporal; no toca el checkout de trabajo)."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSIST = ROOT / "scripts" / "git_persist_rebase_push.sh"
VPS = ROOT / "scripts" / "run_watchlist_paper.sh"
# El wrapper que de verdad corre en el VPS (instalado en /opt/momentum/bin/);
# `scripts/` es código muerto allá (infra/systemd/README.md). Todo lo que
# el VPS tiene que hacer se exige en los DOS.
VPS_INSTALADO = ROOT / "infra" / "systemd" / "bin" / "run_watchlist_paper.sh"
WRAPPERS_VPS = (VPS, VPS_INSTALADO)
GATTR = ROOT / ".gitattributes"
HUNTER_WF = ROOT / ".github" / "workflows" / "momentum_hunter.yml"
WATCHLIST_WF = ROOT / ".github" / "workflows" / "momentum_hunter_watchlist.yml"


HELPER = ROOT / "scripts" / "git_pull_con_estado_local.sh"
SCAN = ROOT / "infra" / "systemd" / "bin" / "run_scan_paper.sh"


def test_scripts_bash_n():
    for path in (PERSIST, HELPER, VPS, VPS_INSTALADO, SCAN):
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, (path, r.stderr)


def test_script_persist_existe_y_rebasea_sin_force():
    texto = PERSIST.read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")
    # El pull ya no es un rebase a pelo: con telemetría sucia git se niega.
    # Lo hace el helper (stash de los paths de estado, rebase, ff-only
    # solo si no hay commits locales).
    assert "git_pull_con_estado_local.sh" in texto
    assert "pull --rebase" in helper
    assert "pull --ff-only" in helper
    assert "git reset --hard" not in helper
    assert "PERSIST_MAX_INTENTOS" in texto
    assert "RANDOM" in texto  # jitter
    # El único `--force` permitido es el que se RECHAZA.
    assert "force push is forbidden" in texto
    assert "force push is forbidden" in helper
    assert "git push --force" not in texto
    assert "git push --force" not in helper
    assert "force-with-lease" in texto
    assert "--force)" in texto or "--force|" in texto
    # Un pop fallido no se reintenta: otro stash anidaría la telemetría.
    assert "rc_pull" in texto and "eq 3" in texto


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
    el flock). Nunca tumba la unidad ni fuerza el push. Se exige en el
    wrapper del árbol Y en el instalado: #150 solo tocó el primero y en
    el VPS no cambió nada."""
    for wrapper in WRAPPERS_VPS:
        texto = wrapper.read_text(encoding="utf-8")
        assert "persist_fallido()" in texto, wrapper
        assert 'dashboard.events persist_fallido' in texto, wrapper
        assert "notify_telegram.sh" in texto, wrapper
        # Los dos caminos de fallo llaman al registro.
        assert 'persist_fallido "git persist failed"' in texto, wrapper
        assert 'persist_fallido "flock timeout"' in texto, wrapper
        # El registro nunca puede hacer fallar al bot: todo con `|| true` o
        # devolviendo 0, y sin `set -e` activo alrededor de la llamada a Telegram.
        assert "return 0\n}" in texto, wrapper
        assert "git push --force" not in texto, wrapper


def test_vps_no_repite_el_telegram_de_persist_fallido_cada_corrida():
    """El script corre cada ~5 min: si git sigue caído, un Telegram por
    corrida es spam. Se marca el día del último aviso y no se repite."""
    for wrapper in WRAPPERS_VPS:
        texto = wrapper.read_text(encoding="utf-8")
        assert "persist_fallido.avisado" in texto, wrapper
        assert 'date -u +%F' in texto, wrapper


def _env_git(tmp_path: Path) -> dict[str, str]:
    """Sin config global: el stash y el commit usan solo la del repo de prueba."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r


def _repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Worktree con telemetría trackeada y un origin bare un commit atrás."""
    env = _env_git(tmp_path)
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, env, "init", "--bare", "-b", "main", str(bare))
    _git(tmp_path, env, "init", "-b", "main", str(work))
    _git(work, env, "config", "user.email", "test@example.com")
    _git(work, env, "config", "user.name", "test")
    _git(work, env, "config", "protocol.file.allow", "always")
    telem = work / "momentum_paper_trader" / "telemetria" / "2026-09-22" / "vps"
    telem.mkdir(parents=True)
    (telem / "events.jsonl").write_text('{"timestamp": "2026-09-22T14:00:00+00:00"}\n', encoding="utf-8")
    (work / "README.md").write_text("base\n", encoding="utf-8")
    _git(work, env, "add", "README.md", "momentum_paper_trader/telemetria")
    _git(work, env, "commit", "-m", "base")
    _git(work, env, "remote", "add", "origin", str(bare))
    _git(work, env, "push", "-u", "origin", "HEAD:main")
    return work, env


def _avanzar_origin(tmp_path: Path, env: dict[str, str], bare_name: str = "origin.git") -> None:
    """Otro clon commitea código (no telemetría) y lo empuja a origin."""
    other = tmp_path / "other"
    bare = tmp_path / bare_name
    _git(tmp_path, env, "clone", "-b", "main", str(bare), str(other))
    _git(other, env, "config", "user.email", "test@example.com")
    _git(other, env, "config", "user.name", "test")
    _git(other, env, "config", "protocol.file.allow", "always")
    (other / "README.md").write_text("base\ncodigo nuevo\n", encoding="utf-8")
    _git(other, env, "add", "README.md")
    _git(other, env, "commit", "-m", "deploy")
    _git(other, env, "push", "origin", "HEAD:main")


def _correr_helper(work: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HELPER), *args], cwd=work, env=env, capture_output=True, text=True,
    )


def test_pull_con_telemetria_sucia_trae_main_y_conserva_el_estado(tmp_path):
    """El caso medido en el VPS: events.jsonl trackeado y sucio, sesion.json
    nuevo (todavía sin trackear), origin avanzó con código. El rebase a
    pelo se niega; el helper tiene que traer main y no perder líneas."""
    work, env = _repo(tmp_path)
    _avanzar_origin(tmp_path, env)
    telem = work / "momentum_paper_trader" / "telemetria" / "2026-09-22" / "vps"
    events = telem / "events.jsonl"
    events.write_text(
        '{"timestamp": "2026-09-22T14:00:00+00:00"}\n'
        '{"timestamp": "2026-09-22T14:05:00+00:00"}\n',
        encoding="utf-8",
    )
    sesion = telem / "sesion.json"
    sesion.write_text('{"fuente": "vps"}\n', encoding="utf-8")

    r = _correr_helper(work, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "estado local apartado" in r.stdout
    assert "codigo nuevo" in (work / "README.md").read_text(encoding="utf-8")
    assert "14:05:00" in events.read_text(encoding="utf-8")
    assert sesion.read_text(encoding="utf-8").startswith('{"fuente": "vps"}')
    stash = _git(work, env, "stash", "list")
    assert stash.stdout.strip() == ""
    # No quedó un rebase a medias.
    assert not (work / ".git" / "rebase-merge").exists()
    assert not (work / ".git" / "rebase-apply").exists()


def test_rebasea_el_commit_local_en_vez_de_ff_only(tmp_path):
    """ff-only con commits locales los dejaría atrás. Con un commit de
    estado sin pushear, el camino es rebase + devolver la telemetría."""
    work, env = _repo(tmp_path)
    _avanzar_origin(tmp_path, env)
    revisiones = work / "momentum_paper_trader" / "revisiones.json"
    revisiones.write_text("{}\n", encoding="utf-8")
    _git(work, env, "add", "momentum_paper_trader/revisiones.json")
    _git(work, env, "commit", "-m", "persist local")
    events = work / "momentum_paper_trader" / "telemetria" / "2026-09-22" / "vps" / "events.jsonl"
    events.write_text(
        events.read_text(encoding="utf-8") + '{"timestamp": "2026-09-22T14:05:00+00:00"}\n',
        encoding="utf-8",
    )

    r = _correr_helper(work, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ff-only" not in r.stdout
    log = _git(work, env, "log", "--oneline", "origin/main..HEAD")
    assert "persist local" in log.stdout
    assert revisiones.read_text(encoding="utf-8") == "{}\n"
    assert "14:05:00" in events.read_text(encoding="utf-8")
    assert "codigo nuevo" in (work / "README.md").read_text(encoding="utf-8")
    assert _git(work, env, "stash", "list").stdout.strip() == ""


def test_ff_only_si_lo_sucio_no_es_estado_y_no_hay_commits_locales(tmp_path):
    """Un archivo trackeado fuera de la lista sigue pudiendo negar el
    rebase. Sin commits locales, ff-only trae main y no toca ese archivo
    ni la telemetría (que sí se aparta y se devuelve)."""
    work, env = _repo(tmp_path)
    notas = work / "notas.txt"
    notas.write_text("local\n", encoding="utf-8")
    _git(work, env, "add", "notas.txt")
    _git(work, env, "commit", "-m", "notas")
    _git(work, env, "push", "origin", "HEAD:main")
    _avanzar_origin(tmp_path, env)
    notas.write_text("local\nsucio\n", encoding="utf-8")
    events = work / "momentum_paper_trader" / "telemetria" / "2026-09-22" / "vps" / "events.jsonl"
    events.write_text(
        events.read_text(encoding="utf-8") + '{"timestamp": "2026-09-22T14:05:00+00:00"}\n',
        encoding="utf-8",
    )

    r = _correr_helper(work, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ff-only" in r.stdout
    assert notas.read_text(encoding="utf-8") == "local\nsucio\n"
    assert "14:05:00" in events.read_text(encoding="utf-8")
    assert "codigo nuevo" in (work / "README.md").read_text(encoding="utf-8")
    assert _git(work, env, "stash", "list").stdout.strip() == ""


def test_pop_con_conflicto_no_deja_marcadores(tmp_path):
    """sesion.json no es merge=union. Si origin también lo tocó, el pop
    choca: no pueden quedar marcadores (el git add de después los
    commitearía) y el JSONL que aplicó limpio no se tira. El stash se
    conserva."""
    work, env = _repo(tmp_path)
    telem = work / "momentum_paper_trader" / "telemetria" / "2026-09-22" / "vps"
    sesion = telem / "sesion.json"
    events = telem / "events.jsonl"
    sesion.write_text('{"fuente": "vps", "n": 1}\n', encoding="utf-8")
    _git(work, env, "add", "momentum_paper_trader/telemetria")
    _git(work, env, "commit", "-m", "sesion base")
    _git(work, env, "push", "origin", "HEAD:main")

    other = tmp_path / "other-conflict"
    _git(tmp_path, env, "clone", "-b", "main", str(tmp_path / "origin.git"), str(other))
    _git(other, env, "config", "user.email", "test@example.com")
    _git(other, env, "config", "user.name", "test")
    _git(other, env, "config", "protocol.file.allow", "always")
    (other / "momentum_paper_trader" / "telemetria" / "2026-09-22" / "vps" / "sesion.json").write_text(
        '{"fuente": "vps", "n": 2}\n', encoding="utf-8",
    )
    (other / "README.md").write_text("base\ncodigo nuevo\n", encoding="utf-8")
    _git(other, env, "add", "momentum_paper_trader/telemetria", "README.md")
    _git(other, env, "commit", "-m", "origin toca sesion")
    _git(other, env, "push", "origin", "HEAD:main")

    sesion.write_text('{"fuente": "vps", "n": 3}\n', encoding="utf-8")
    events.write_text(
        events.read_text(encoding="utf-8") + '{"timestamp": "2026-09-22T14:05:00+00:00"}\n',
        encoding="utf-8",
    )
    r = _correr_helper(work, env)
    assert r.returncode == 3, r.stdout + r.stderr
    arbol = "\n".join(p.read_text(encoding="utf-8") for p in telem.rglob("*") if p.is_file())
    assert "<<<<<<<" not in arbol
    assert "14:05:00" in events.read_text(encoding="utf-8")
    assert '"n": 2' in sesion.read_text(encoding="utf-8")
    assert "codigo nuevo" in (work / "README.md").read_text(encoding="utf-8")
    assert _git(work, env, "stash", "list").stdout.strip() != ""


def test_dry_run_no_toca_el_arbol_ni_hace_pull(tmp_path):
    work, env = _repo(tmp_path)
    _avanzar_origin(tmp_path, env)
    events = work / "momentum_paper_trader" / "telemetria" / "2026-09-22" / "vps" / "events.jsonl"
    events.write_text(events.read_text(encoding="utf-8") + '{"timestamp": "x"}\n', encoding="utf-8")
    antes = _git(work, env, "rev-parse", "HEAD").stdout.strip()
    env = dict(env)
    env["GIT_PULL_ESTADO_DRY_RUN"] = "1"
    r = _correr_helper(work, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "apartaría" in r.stdout
    assert _git(work, env, "rev-parse", "HEAD").stdout.strip() == antes
    assert '{"timestamp": "x"}' in events.read_text(encoding="utf-8")
    assert _git(work, env, "stash", "list").stdout.strip() == ""
    # origin avanzó y el dry-run no lo trajo.
    assert "codigo nuevo" not in (work / "README.md").read_text(encoding="utf-8")


def test_helper_rechaza_force_sin_tocar_git(tmp_path):
    r = subprocess.run(
        ["bash", str(HELPER), "--force"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert "force push is forbidden" in r.stderr


def test_el_helper_aparta_todos_los_paths_que_los_wrappers_commitean():
    helper = HELPER.read_text(encoding="utf-8")
    for wrapper in (VPS, VPS_INSTALADO, SCAN):
        bloque = _vps_paths_de_persistencia(wrapper.read_text(encoding="utf-8"))
        for linea in bloque.splitlines():
            linea = linea.strip().strip('"').strip("'")
            if linea.startswith("momentum_"):
                assert linea in helper, (wrapper.name, linea)


def test_los_wrappers_dejan_de_hacer_pull_rebase_a_pelo():
    """El pull crudo anterior al git add es el que se negaba con la
    telemetría sucia. Los tres wrappers pasan por el helper."""
    for wrapper in (VPS, VPS_INSTALADO, SCAN):
        texto = wrapper.read_text(encoding="utf-8")
        assert "git_pull_con_estado_local.sh" in texto, wrapper
        assert "git pull --rebase origin main" not in texto, wrapper


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
