"""Escaneo en el VPS: unidades, wrappers, compuertas de GitHub y regla de
respaldo (2026-09-21). No ejecuta nada: lee texto y prueba funciones puras."""

from __future__ import annotations

import importlib.util
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_SH = ROOT / "infra" / "systemd" / "bin" / "run_scan_paper.sh"
WL_SH = ROOT / "infra" / "systemd" / "bin" / "run_watchlist_paper.sh"
WL_SH_TREE = ROOT / "scripts" / "run_watchlist_paper.sh"
SCAN_SERVICE = ROOT / "infra" / "systemd" / "momentum-scan.service"
SCAN_TIMER = ROOT / "infra" / "systemd" / "momentum-scan.timer"
HUNTER_WF = ROOT / ".github" / "workflows" / "momentum_hunter.yml"
WATCHLIST_WF = ROOT / ".github" / "workflows" / "momentum_hunter_watchlist.yml"


def _cargar_respaldo():
    ruta = ROOT / ".github" / "scripts" / "respaldo_gha.py"
    spec = importlib.util.spec_from_file_location("respaldo_gha", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ───────────── unidades y wrappers ─────────────

def test_scripts_del_vps_pasan_bash_n_y_son_ejecutables():
    for path in (SCAN_SH, WL_SH, WL_SH_TREE):
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    assert SCAN_SH.stat().st_mode & 0o111


def test_timer_del_escaneo_cada_30_min_en_sesion_sin_pisar_al_rechequeo():
    texto = SCAN_TIMER.read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 13..20:01,31:00 UTC" in texto
    assert "Persistent=false" in texto
    assert "Unit=momentum-scan.service" in texto
    servicio = SCAN_SERVICE.read_text(encoding="utf-8")
    assert "ExecStart=/opt/momentum/bin/run_scan_paper.sh" in servicio
    assert "User=momentum" in servicio and "EnvironmentFile=/etc/momentum/paper.env" in servicio


def test_el_escaneo_corre_sin_candado_y_solo_se_bloquea_para_git():
    """Regla del dueño: candado solo en las escrituras y el commit, nunca
    durante los ~9 min de escaneo."""
    texto = SCAN_SH.read_text(encoding="utf-8")
    escaneo = texto.index("momentum_hunter.run --limit")
    persist = texto.index("persistir_estado()")
    # El comando de escaneo está fuera de cualquier subshell con flock:
    # ninguna llamada a flock antes del escaneo sigue abierta al llegar a él.
    antes = texto[:escaneo]
    assert antes.count("(") == antes.count(")"), "el escaneo quedó dentro de un bloque abierto"
    assert "flock" not in texto[antes.rfind(")"):escaneo]
    # El paper también corre sin candado, y el commit sí lleva flock.
    assert texto.index("momentum_paper_trader.run") < persist
    bloque_commit = texto[texto.index("persistir_estado\n) 9>") - 400:texto.index("persistir_estado\n) 9>")]
    assert "flock -w 180 9" in bloque_commit
    # Nunca --force.
    assert "git push --force" not in texto and "force-with-lease" not in texto


def test_el_rechequeo_nunca_se_salta_por_el_escaneo():
    for path in (WL_SH, WL_SH_TREE):
        texto = path.read_text(encoding="utf-8")
        assert "flock -n" not in texto, path
        assert "se salta" not in texto, path
        assert "momentum_hunter.run --solo-watchlist" in texto


def test_el_vps_es_el_dueno_de_watchlist_y_auditoria_y_materializa_antes_de_commitear():
    for path in (SCAN_SH, WL_SH, WL_SH_TREE):
        texto = path.read_text(encoding="utf-8")
        inicio = texto.index("paths=(")
        bloque = texto[inicio:texto.index(")", inicio)]
        assert "momentum_hunter/watchlist.json" in bloque, path
        assert "momentum_hunter/auditoria" in bloque, path
        assert "momentum_hunter/telemetria" in bloque, path
        assert "momentum_paper_trader/revisiones.json" in bloque, path
        assert "--materializar-overlay" in texto[:inicio], path   # antes del git add


def test_yahoo_pausa_del_bot_distinta_de_la_del_panel():
    for path in (SCAN_SH, WL_SH, WL_SH_TREE):
        texto = path.read_text(encoding="utf-8")
        assert 'MOMENTUM_YAHOO_PAUSA_ARCHIVO="${MOMENTUM_YAHOO_PAUSA_ARCHIVO:-/var/lib/momentum/yahoo_pausa_bot.json}"' in texto, path
    panel = (ROOT / "deploy" / "momentum-dashboard.service").read_text(encoding="utf-8")
    assert "DASH_YAHOO_PAUSA_BOT=/var/lib/momentum/yahoo_pausa_bot.json" in panel
    # El archivo del panel (yahoo_pausa.json en su caché) es otro: el bot no lo nombra.
    assert "dashboard_cache" not in SCAN_SH.read_text(encoding="utf-8")


def test_vuelta_atras_sin_tocar_codigo():
    texto = SCAN_SH.read_text(encoding="utf-8")
    assert 'if [ "${MOMENTUM_SCAN_VPS:-1}" = "0" ]' in texto
    assert texto.index("MOMENTUM_SCAN_VPS") < texto.index("git pull")


# ───────────── compuertas de GitHub ─────────────

def test_github_solo_actua_como_respaldo_y_el_paper_esta_apagado_por_defecto():
    for wf in (HUNTER_WF, WATCHLIST_WF):
        texto = wf.read_text(encoding="utf-8")
        assert "run: python .github/scripts/respaldo_gha.py" in texto, wf
        assert texto.count("if: steps.vps.outputs.respaldo == 'true'") >= 2, wf   # correr + persistir
        assert "if: steps.vps.outputs.respaldo == 'true' && vars.MOMENTUM_PAPER_GHA == 'on'" in texto, wf
        assert 'MOMENTUM_TELEGRAM_PREFIJO: "[RESPALDO GITHUB]"' in texto, wf
        assert "forzar:" in texto, wf


def test_revisiones_json_tiene_un_solo_escritor():
    for wf in (HUNTER_WF, WATCHLIST_WF):
        texto = wf.read_text(encoding="utf-8")
        assert "momentum_paper_trader/revisiones.json" not in texto.replace("# revisiones.json NO", "").replace("revisiones.json NO", ""), wf


# ───────────── regla de respaldo ─────────────

def test_respaldo_decide_con_gracia_y_silencio(tmp_path):
    r = _cargar_respaldo()
    dia = datetime(2026, 9, 21, tzinfo=UTC)
    # Antes de las 13:20 UTC nunca, aunque no haya latido.
    assert r.decidir(dia.replace(hour=13, minute=10), None) == (False, "faltan 10 min de gracia desde las 13:00 UTC")
    # A las 13:20 sin latido: respaldo.
    ok, razon = r.decidir(dia.replace(hour=13, minute=20), None)
    assert ok and "no dejó telemetría" in razon
    # Latido hace 5 min: el VPS está vivo.
    ok, razon = r.decidir(dia.replace(hour=15), dia.replace(hour=14, minute=55))
    assert not ok and "vivo" in razon and "14:55" in razon
    # Latido hace 21 min: respaldo.
    ok, razon = r.decidir(dia.replace(hour=15), dia.replace(hour=14, minute=39))
    assert ok and "21 min" in razon
    # Exactamente 20 min no es "más de 20".
    assert r.decidir(dia.replace(hour=15), dia.replace(hour=14, minute=40))[0] is False
    # Forzado a mano: siempre, incluso en la gracia.
    assert r.decidir(dia.replace(hour=13, minute=5), dia.replace(hour=13, minute=4), forzar=True)[0] is True


def test_ultimo_latido_lee_las_dos_telemetrias_del_vps_de_hoy(tmp_path, monkeypatch):
    r = _cargar_respaldo()
    ahora = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)
    paper = tmp_path / "paper" / "2026-09-21" / "vps"
    hunter = tmp_path / "hunter" / "2026-09-21" / "vps"
    paper.mkdir(parents=True)
    hunter.mkdir(parents=True)
    (paper / "events.jsonl").write_text('{"timestamp": "2026-09-21T14:40:00+00:00"}\nbasura\n{"timestamp": "2026-09-21T14:50:00+00:00"}\n')
    (hunter / "events.jsonl").write_text('{"timestamp": "2026-09-21T14:46:00Z"}\n')
    assert r.ultimo_latido_vps(ahora, (tmp_path / "paper", tmp_path / "hunter")) == datetime(2026, 9, 21, 14, 50, tzinfo=UTC)
    # Sin archivos de hoy: None. Un archivo de ayer no cuenta.
    ayer = tmp_path / "paper" / "2026-09-20" / "vps"
    ayer.mkdir(parents=True)
    (ayer / "events.jsonl").write_text('{"timestamp": "2026-09-20T19:55:00+00:00"}\n')
    assert r.ultimo_latido_vps(ahora, (tmp_path / "nada",)) is None
    assert r.ultimo_latido_vps(ahora - timedelta(days=1), (tmp_path / "paper",)) == datetime(2026, 9, 20, 19, 55, tzinfo=UTC)
