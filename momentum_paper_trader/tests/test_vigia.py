"""Vigía (2026-09-22): rechequeo + paper cada 60 s como proceso permanente.
No corre subprocesos reales: el ejecutor se inyecta y el reloj es falso."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from momentum_paper_trader import vigia

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "infra" / "systemd" / "momentum-vigia.service"
WRAPPER = ROOT / "infra" / "systemd" / "bin" / "run_vigia.sh"
WL_SH = ROOT / "infra" / "systemd" / "bin" / "run_watchlist_paper.sh"
WL_SH_TREE = ROOT / "scripts" / "run_watchlist_paper.sh"
SCAN_SH = ROOT / "infra" / "systemd" / "bin" / "run_scan_paper.sh"
README = ROOT / "infra" / "systemd" / "README.md"

LUNES = datetime(2026, 9, 21, tzinfo=UTC)


class Reloj:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def dormir(self, seg: float) -> None:
        self.t += timedelta(seconds=seg)


def _vigia(tmp_path, reloj, ejecutar, **kw):
    return vigia.Vigia(
        ejecutar=ejecutar, reloj=reloj, dormir=reloj.dormir,
        wrapper_persist=str(tmp_path / "wrapper.sh"),
        lock_paper=str(tmp_path / "paper.lock"),
        latido=str(tmp_path / "latido.json"),
        python="python", **kw,
    )


def _nombre(cmd: list[str]) -> str:
    if cmd[0] == "bash":
        return "persistir"
    return {"momentum_hunter.run": "rechequeo", "momentum_paper_trader.run": "paper"}[cmd[2]]


# ───────────── funciones puras ─────────────

def test_ventana_lun_vie_13_a_2001_utc():
    assert vigia.en_ventana(LUNES.replace(hour=13, minute=0))
    assert vigia.en_ventana(LUNES.replace(hour=20, minute=0, second=59))
    assert not vigia.en_ventana(LUNES.replace(hour=20, minute=1))
    assert not vigia.en_ventana(LUNES.replace(hour=12, minute=59, second=59))
    sabado = LUNES + timedelta(days=5)
    assert not vigia.en_ventana(sabado.replace(hour=15))


def test_proximo_tick_cae_a_los_05_y_es_estrictamente_posterior():
    assert vigia.proximo_tick(LUNES.replace(hour=13, minute=0, second=0)) == LUNES.replace(hour=13, minute=0, second=5)
    assert vigia.proximo_tick(LUNES.replace(hour=13, minute=0, second=5)) == LUNES.replace(hour=13, minute=1, second=5)
    assert vigia.proximo_tick(LUNES.replace(hour=13, minute=0, second=6)) == LUNES.replace(hour=13, minute=1, second=5)
    # Cadencia distinta (pruebas manuales): 30 s.
    assert vigia.proximo_tick(LUNES.replace(hour=13, minute=0, second=6), cadencia=30) == LUNES.replace(hour=13, minute=0, second=35)


def test_inicio_proxima_ventana_salta_el_finde():
    viernes_tarde = LUNES + timedelta(days=4, hours=21)
    assert vigia.inicio_proxima_ventana(viernes_tarde) == (LUNES + timedelta(days=7)).replace(hour=13)
    assert vigia.inicio_proxima_ventana(LUNES.replace(hour=5)) == LUNES.replace(hour=13)


# ───────────── el tick ─────────────

def test_tick_corre_rechequeo_luego_paper_y_un_fallo_no_frena_al_otro(tmp_path):
    reloj = Reloj(LUNES.replace(hour=14))
    llamadas = []

    def ejecutar(cmd, timeout, env=None):
        llamadas.append((_nombre(cmd), timeout))
        reloj.dormir(2)
        return 1 if _nombre(cmd) == "rechequeo" else 0

    v = _vigia(tmp_path, reloj, ejecutar)
    res = v.tick()
    assert [n for n, _ in llamadas] == ["rechequeo", "paper"]
    assert [r.rc for r in res] == [1, 0]
    assert all(t > 0 for _, t in llamadas)
    # Marca de vida fuera de git, con el número de tick y los pasos.
    latido = json.loads((tmp_path / "latido.json").read_text())
    assert latido["tick"] == 1 and set(latido["pasos"]) == {"rechequeo", "paper"}


def test_un_timeout_se_registra_como_rc_none_y_el_tick_sigue(tmp_path):
    reloj = Reloj(LUNES.replace(hour=14))

    def ejecutar(cmd, timeout, env=None):
        return None if _nombre(cmd) == "rechequeo" else 0

    v = _vigia(tmp_path, reloj, ejecutar)
    res = v.tick()
    assert res[0].rc is None and not res[0].ok and res[1].ok


def test_persiste_cada_5_ticks_con_el_wrapper_en_modo_solo_persistir(tmp_path):
    reloj = Reloj(LUNES.replace(hour=14))
    llamadas = []

    def ejecutar(cmd, timeout, env=None):
        llamadas.append((_nombre(cmd), env))
        return 0

    v = _vigia(tmp_path, reloj, ejecutar)
    for _ in range(5):
        v.tick()
    persist = [(n, e) for n, e in llamadas if n == "persistir"]
    assert len(persist) == 1
    assert persist[0][1]["MOMENTUM_WRAPPER_SOLO_PERSISTIR"] == "1"
    assert llamadas[-1][0] == "persistir"           # después del paper del tick 5
    assert v.pendiente_persistir is False


def test_el_paper_corre_bajo_candado(tmp_path):
    """Mientras el ejecutor corre, otro proceso (el paso paper del
    escaneo) no puede tomar el mismo candado."""
    reloj = Reloj(LUNES.replace(hour=14))
    visto = {}

    def ejecutar(cmd, timeout, env=None):
        if _nombre(cmd) == "paper":
            with (tmp_path / "paper.lock").open("a+") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    visto["libre"] = True
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    visto["libre"] = False
        return 0

    _vigia(tmp_path, reloj, ejecutar).tick()
    assert visto == {"libre": False}


def test_candado_ocupado_el_tick_no_ejecuta_el_paper(tmp_path, monkeypatch):
    """Fail-closed: si otro ejecutor tiene el candado, este tick no coloca
    nada (el otro ya está mirando esa misma señal)."""
    monkeypatch.setattr(vigia, "ESPERA_CANDADO_PAPER_SEG", 0.2)
    reloj = Reloj(LUNES.replace(hour=14))
    llamadas = []

    def ejecutar(cmd, timeout, env=None):
        llamadas.append(_nombre(cmd))
        return 0

    v = _vigia(tmp_path, reloj, ejecutar)
    v.dormir = lambda s: None
    lock = (tmp_path / "paper.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        res = v.tick()
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    assert llamadas == ["rechequeo"]
    assert res[1].nombre == "paper" and res[1].rc == 75


# ───────────── el bucle ─────────────

def test_el_bucle_tickea_a_los_05_solo_en_ventana_y_persiste_al_cerrar(tmp_path):
    reloj = Reloj(LUNES.replace(hour=19, minute=57, second=40))
    llamadas = []

    def ejecutar(cmd, timeout, env=None):
        llamadas.append((_nombre(cmd), reloj.t))
        reloj.dormir(3)
        return 0

    v = _vigia(tmp_path, reloj, ejecutar, persistir_cada=100)

    def dormir(seg):
        reloj.dormir(seg)
        if reloj.t >= LUNES.replace(hour=20, minute=5):
            v.detener = True   # sin esto el bucle esperaría al martes
    v.dormir = dormir

    v.correr()
    ticks = [t for n, t in llamadas if n == "rechequeo"]
    assert ticks == [LUNES.replace(hour=19, minute=58, second=5), LUNES.replace(hour=19, minute=59, second=5),
                     LUNES.replace(hour=20, minute=0, second=5)]
    # Ningún tick fuera de la ventana, y una sola persistencia al cerrar.
    persist = [t for n, t in llamadas if n == "persistir"]
    assert len(persist) == 1 and persist[0] > ticks[-1]
    assert v.pendiente_persistir is False


def test_fuera_de_ventana_no_hay_ticks_ni_persistencias_sin_cambios(tmp_path):
    reloj = Reloj(LUNES.replace(hour=3))
    llamadas = []

    def ejecutar(cmd, timeout, env=None):
        llamadas.append(_nombre(cmd))
        return 0

    v = _vigia(tmp_path, reloj, ejecutar)
    vueltas = {"n": 0}

    def dormir(seg):
        assert seg <= 60
        reloj.dormir(seg)
        vueltas["n"] += 1
        if vueltas["n"] >= 30:
            v.detener = True
    v.dormir = dormir
    v.correr()
    assert llamadas == []


def test_max_ticks_persiste_lo_pendiente_al_salir(tmp_path):
    reloj = Reloj(LUNES.replace(hour=14))
    llamadas = []

    def ejecutar(cmd, timeout, env=None):
        llamadas.append(_nombre(cmd))
        return 0

    v = _vigia(tmp_path, reloj, ejecutar)
    v.correr(max_ticks=2)
    assert llamadas == ["rechequeo", "paper", "rechequeo", "paper", "persistir"]


def test_pedir_parada_termina_el_bucle(tmp_path):
    reloj = Reloj(LUNES.replace(hour=14))
    v = _vigia(tmp_path, reloj, lambda cmd, timeout, env=None: 0)
    v.pedir_parada()
    assert v.correr() == 0


def test_ejecutar_subproceso_real_devuelve_rc_y_none_en_timeout():
    assert vigia._ejecutar_subproceso(["true"], 5) == 0
    assert vigia._ejecutar_subproceso(["false"], 5) == 1
    assert vigia._ejecutar_subproceso(["sleep", "5"], 0.2) is None


# ───────────── infra ─────────────

def test_unidad_del_vigia_es_permanente_y_excluye_al_timer_viejo():
    texto = UNIT.read_text(encoding="utf-8")
    assert "Type=simple" in texto
    assert "Restart=always" in texto
    assert "Conflicts=momentum-watchlist.timer" in texto
    assert "ExecStart=/opt/momentum/bin/run_vigia.sh" in texto
    assert "User=momentum" in texto and "EnvironmentFile=/etc/momentum/paper.env" in texto
    assert "KillMode=mixed" in texto and "TimeoutStopSec=" in texto


def test_wrapper_del_vigia_pasa_bash_n_y_exporta_lo_mismo_que_el_rechequeo():
    r = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert WRAPPER.stat().st_mode & 0o111
    texto = WRAPPER.read_text(encoding="utf-8")
    for clave in ("MOMENTUM_TELEM_FUENTE=vps", "MOMENTUM_WATCHLIST_VPS_STATE", "MOMENTUM_YAHOO_PAUSA_ARCHIVO",
                  "MOMENTUM_VIGIA_WRAPPER_PERSIST", "MOMENTUM_PAPER_LOCK", "momentum_paper_trader.vigia"):
        assert clave in texto, clave


def test_los_wrappers_del_rechequeo_tienen_modo_solo_persistir():
    """El vigía delega git al wrapper de siempre: en ese modo no corre ni
    rechequeo ni paper, pero sí backup, pull, materializar, commit, push y
    el aviso de persist fallido."""
    for wrapper in (WL_SH, WL_SH_TREE):
        texto = wrapper.read_text(encoding="utf-8")
        assert 'MOMENTUM_WRAPPER_SOLO_PERSISTIR:-0' in texto, wrapper
        modo = texto.index("MOMENTUM_WRAPPER_SOLO_PERSISTIR")
        assert texto.index("momentum_hunter.run --solo-watchlist") > modo, wrapper
        assert texto.index("momentum_paper_trader.run") > modo, wrapper
        assert texto.index("persistir_estado()") > modo, wrapper
        assert "persist_fallido" in texto, wrapper


def test_el_paso_paper_del_escaneo_usa_el_mismo_candado_que_el_vigia():
    texto = SCAN_SH.read_text(encoding="utf-8")
    assert 'PAPER_LOCK="${MOMENTUM_PAPER_LOCK:-' + vigia.LOCK_PAPER_DEFAULT + '}"' in texto
    assert 'flock -w 120 "$PAPER_LOCK" "$PY" -m momentum_paper_trader.run' in texto
    # El escaneo en sí sigue sin candado (decisión del dueño, #152).
    escaneo = texto.index("momentum_hunter.run --limit")
    assert "flock" not in texto[texto[:escaneo].rfind(")"):escaneo]


def test_readme_documenta_instalacion_y_vuelta_atras():
    texto = README.read_text(encoding="utf-8")
    assert "momentum-vigia.service" in texto
    assert "systemctl disable --now momentum-watchlist.timer" in texto
    assert "systemctl enable --now momentum-vigia.service" in texto
    assert "systemctl enable --now momentum-watchlist.timer" in texto   # vuelta atrás


def test_el_hunter_no_conoce_al_vigia():
    """Regla 2: el vigía orquesta ejecución, así que vive en el paper
    trader y ningún módulo del hunter lo importa."""
    for ruta in (ROOT / "momentum_hunter").rglob("*.py"):
        if "tests" in ruta.parts:
            continue
        assert "vigia" not in ruta.read_text(encoding="utf-8"), ruta


@pytest.mark.skipif(os.name != "posix", reason="flock")
def test_smoke_main_con_max_ticks_cero_no_corre_nada(monkeypatch, tmp_path):
    monkeypatch.setenv("MOMENTUM_VIGIA_WRAPPER_PERSIST", str(tmp_path / "w.sh"))
    monkeypatch.setenv("MOMENTUM_PAPER_LOCK", str(tmp_path / "l"))
    monkeypatch.setenv("MOMENTUM_VIGIA_LATIDO", str(tmp_path / "latido.json"))
    llamadas = []
    monkeypatch.setattr(vigia, "_ejecutar_subproceso", lambda cmd, timeout, env=None: llamadas.append(cmd) or 0)
    monkeypatch.setattr(vigia.time, "sleep", lambda s: None)
    # Fuera de ventana con detención inmediata: el bucle no debe tickear.
    monkeypatch.setattr(vigia.Vigia, "correr", lambda self, max_ticks=None: 0)
    assert vigia.main(["--max-ticks", "0"]) == 0
    assert llamadas == []
