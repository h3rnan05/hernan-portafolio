"""Invariantes del watchdog VPS de silencio (paper only).

No habla con Telegram ni con systemd reales. Comprueba que el script
existe, es bash válido, y que la gracia de open de sesión no cuenta el
silencio del fin de semana contra el umbral de 1200s. También: si el
timer de rechequeo está OFF a propósito, el silencio no alerta."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "watchdog_timer_miss.sh"
RUNBOOK = ROOT / "docs" / "RUNBOOK-PAPER-CEO.md"

# Lunes 2026-09-14, el día del FP: last_ok viernes ~20:55 UTC (~64h).
LUNES_OPEN = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
LUNES_13_05 = datetime(2026, 9, 14, 13, 5, tzinfo=UTC)
LUNES_13_21 = datetime(2026, 9, 14, 13, 21, tzinfo=UTC)
LUNES_12_00 = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
VIERNES_LAST_OK = datetime(2026, 9, 11, 20, 55, tzinfo=UTC)
SABADO_15 = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)


def _run(now: datetime, last_ok: datetime | None = None, extra_env: dict | None = None):
    env = {
        "WATCHDOG_DRY_RUN": "1",
        "WATCHDOG_NOW_EPOCH": str(int(now.timestamp())),
        "WATCHDOG_THRESHOLD_SEC": "1200",
        # CI no tiene la unidad; el default de producción consulta systemctl.
        "WATCHDOG_TIMER_ACTIVE": "active",
        "WATCHDOG_STATE_DIR": tempfile.mkdtemp(prefix="watchdog-test-"),
    }
    if last_ok is None:
        env["WATCHDOG_LAST_OK_EPOCH"] = ""
    else:
        env["WATCHDOG_LAST_OK_EPOCH"] = str(int(last_ok.timestamp()))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=False,
    )


def test_script_existe_y_bash_n():
    assert SCRIPT.is_file()
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_paper_only_sin_endpoint_live():
    texto = SCRIPT.read_text(encoding="utf-8")
    assert "PAPER ONLY" in texto
    assert "https://api.alpaca.markets" not in texto
    assert "notify_telegram.sh" in texto
    assert "WATCHDOG_THRESHOLD_SEC" in texto
    assert "session_open" in texto
    assert 'bash "$NOTIFY"' in texto
    assert "No START/OK spam" in texto
    assert "TELEGRAM_TRADES_ONLY" not in texto
    assert "systemctl is-active" in texto
    assert "momentum-watchlist.timer" in texto


def test_runbook_documenta_gracia_de_sesion():
    texto = RUNBOOK.read_text(encoding="utf-8")
    assert "watchdog_timer_miss.sh" in texto
    assert "13:00 UTC" in texto
    assert "1200" in texto
    assert "momentum-watchlist.timer" in texto
    assert "timer inactive" in texto


def test_finde_y_fuera_de_ventana_no_alertan():
    sab = _run(SABADO_15, VIERNES_LAST_OK)
    assert sab.returncode == 0
    assert "weekend" in sab.stdout
    pre = _run(LUNES_12_00, VIERNES_LAST_OK)
    assert pre.returncode == 0
    assert "outside US window" in pre.stdout


def test_sin_timestamp_no_false_positive():
    r = _run(LUNES_13_21, last_ok=None)
    assert r.returncode == 0
    assert "no known success timestamp" in r.stdout


def test_lunes_open_no_cuenta_silencio_del_viernes():
    """El FP: ~64h desde viernes vs umbral 1200s. Con gracia, a las
    13:05 del lunes la edad es 300s desde session_open, no ~230k s."""
    r = _run(LUNES_13_05, VIERNES_LAST_OK)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "basis=session_open" in r.stdout
    assert "age=300s" in r.stdout
    assert "ERROR" not in r.stdout


def test_lunes_open_alerta_si_siguen_20_min_sin_ok():
    r = _run(LUNES_13_21, VIERNES_LAST_OK)
    assert r.returncode == 1
    assert "ERROR" in r.stdout
    assert "basis=session_open" in r.stdout
    assert "silent 1260s" in r.stdout


def test_silencio_intra_sesion_usa_last_ok():
    last_ok = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    ok = _run(datetime(2026, 9, 14, 13, 10, tzinfo=UTC), last_ok)
    assert ok.returncode == 0
    assert "basis=last_ok" in ok.stdout
    assert "age=600s" in ok.stdout

    late = _run(datetime(2026, 9, 14, 13, 21, tzinfo=UTC), last_ok)
    assert late.returncode == 1
    assert "basis=last_ok" in late.stdout
    assert "silent 1260s" in late.stdout
    # Ancla de sesión usada en los casos del lunes: 13:00 UTC.
    assert int(LUNES_OPEN.timestamp()) == int(last_ok.timestamp())


def test_timer_inactivo_omite_silencio_sin_telegram():
    """Post-#132 el timer de rechequeo está OFF a propósito. El silencio
    del oneshot no es un fallo; no se dispara Telegram por esa causa."""
    r = _run(
        LUNES_13_21,
        VIERNES_LAST_OK,
        extra_env={"WATCHDOG_TIMER_ACTIVE": "inactive"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "timer inactive; skip" in r.stdout
    assert "ERROR" not in r.stdout
    assert "silent " not in r.stdout
    # Los otros chequeos W1 siguen corriendo: no hay exit 0 global.
    assert "persist_fail_count" in r.stdout
    assert "git_ahead" in r.stdout


def test_silencio_dedupe_diario_un_solo_telegram():
    """Silencio usa el mismo dedupe diario que persist_fail / ahead / zero_push."""
    state = tempfile.mkdtemp(prefix="watchdog-silence-")
    extra = {"WATCHDOG_STATE_DIR": state, "WATCHDOG_TIMER_ACTIVE": "active"}
    first = _run(LUNES_13_21, VIERNES_LAST_OK, extra_env=extra)
    assert first.returncode == 1, first.stdout + first.stderr
    assert "silent 1260s" in first.stdout
    assert "dry-run skip notify" in first.stdout

    second = _run(LUNES_13_21, VIERNES_LAST_OK, extra_env=extra)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "silence already fired today" in second.stdout
    assert "dry-run skip notify" not in second.stdout


def test_timer_inactivo_no_silencia_chequeos_w1():
    """El skip del timer solo cubre el silencio; persist/ahead/zero_push
    siguen en el script con su dedupe existente, sin exit inmediato."""
    texto = SCRIPT.read_text(encoding="utf-8")
    skip_idx = texto.find("INFO: timer inactive; skip")
    persist_idx = texto.find('_already_fired_today "persist_fail"')
    ahead_idx = texto.find('_already_fired_today "ahead"')
    zero_idx = texto.find('_already_fired_today "zero_push_session"')
    silence_dedupe_idx = texto.find('_already_fired_today "silence"')
    assert skip_idx > 0
    assert persist_idx > skip_idx
    assert ahead_idx > skip_idx
    assert zero_idx > skip_idx
    assert silence_dedupe_idx > skip_idx
    after_skip = texto[skip_idx:skip_idx + 180]
    assert "exit 0" not in after_skip
