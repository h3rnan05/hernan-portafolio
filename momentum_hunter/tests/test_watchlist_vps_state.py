"""Overlay VPS de watchlist: state file fuera de git, fail-loud, merge.

POR QUÉ. `--solo-watchlist` en el VPS escribía `watchlist.json` en disco
aunque ya no hiciera `git add`. Eso suciaba el worktree y rompía
`git pull --rebase` (medido 2026-09-18, VRA watching→expired). GHA sigue
siendo el dueño del canónico; el VPS persiste mutaciones en
`/var/lib/momentum/watchlist_vps_state.json` (o `MOMENTUM_WATCHLIST_STATE`).

PAPER ONLY. No toca umbrales, stops, ni el endpoint de Alpaca. No arranca
el timer de rechequeo.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario, CandidatoIntradia
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.early_opportunity import EarlyOpportunity
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion

CFG = MomentumConfig()
AHORA = datetime(2026, 8, 11, 14, 0, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]


def _candidato_diario(ticker="RKLB") -> CandidatoDiario:
    catalizador = Catalizador(
        tipo="contrato", titular="Rocket Lab wins contract",
        fuente="Reuters", fecha="2026-08-11T13:45:00+00:00",
    )
    return CandidatoDiario(
        ticker=ticker, nombre="Rocket Lab Corp", precio=80.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=1.5), catalizador=catalizador,
        meta=Metadata(ticker=ticker, shares_float=180_000_000.0, short_pct_float=0.05),
        puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
        es_large_cap=True,
    )


def _bi(ticker: str) -> BarraIntradia:
    return BarraIntradia(ticker, ["2026-08-11T14:05:00+00:00"], [5.2], [5.2], [5.25], [5.15], [5000.0])


def _candidato_intradia(ticker: str, accionable: bool = True) -> CandidatoIntradia:
    early = EarlyOpportunity(score=90.0, veredicto="temprano", razon="ok", motivo_veredicto="")
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True,
        patron="gap_and_go", temprano=True, early=early,
        penalizaciones=[] if accionable else ["x"],
        score_base=90.0, score_ajustado=90.0 if accionable else 0.0, accionable=accionable,
    )
    return CandidatoIntradia(
        ticker=ticker, nombre=None,
        catalizador=Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                                 fecha="2026-08-11T13:45:00+00:00"),
        minutos_desde_catalizador=10.0,
        factores=FactoresIntradia(
            precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=4.0, aceleracion_volumen=1.5,
            gap_pct=0.10, maximo_premarket=5.00, maximo_dia=5.25, velas_desde_ruptura=1,
        ),
        bi_hoy=_bi(ticker), meta=Metadata(ticker=ticker), atr_diario=0.30, resultado=resultado,
    )


class _FakeProvider(DataProvider):
    def __init__(self, tickers_con_datos: set[str]) -> None:
        self._tickers_con_datos = tickers_con_datos

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {t: _bi(t) for t in tickers if t in self._tickers_con_datos}


def _parchear_efectos(monkeypatch):
    enviados: list[str] = []
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda texto: enviados.append(texto))
    monkeypatch.setattr(run_mod.tracker, "registrar", lambda ops: None)
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: None)
    return enviados


def _activar_vps(monkeypatch, tmp_path) -> Path:
    state = tmp_path / "watchlist_vps_state.json"
    monkeypatch.setenv("MOMENTUM_WATCHLIST_VPS_STATE", "1")
    monkeypatch.setenv("MOMENTUM_WATCHLIST_STATE", str(state))
    return state


def _preparar_vps(monkeypatch, tmp_path, entradas):
    """Canónico en tmp + flag ON. NO parchea `guardar`: si un call site
    se escapa contra PATH, la guardia tiene que explotar."""
    canonical = tmp_path / "watchlist.json"
    watchlist.guardar(entradas, canonical)
    state = _activar_vps(monkeypatch, tmp_path)
    real_cargar = watchlist.cargar
    monkeypatch.setattr(
        watchlist, "cargar",
        lambda path=canonical, apply_vps_state=False: real_cargar(canonical, apply_vps_state=False),
    )
    mtime_repo = watchlist.PATH.stat().st_mtime_ns if watchlist.PATH.exists() else None
    mtime_canonical = canonical.stat().st_mtime_ns
    return canonical, state, mtime_repo, mtime_canonical


def _assert_sin_tocar_canonico(canonical, mtime_repo, mtime_canonical, sha_canonical=None):
    assert canonical.stat().st_mtime_ns == mtime_canonical
    if sha_canonical is not None:
        assert hashlib.sha256(canonical.read_bytes()).hexdigest() == sha_canonical
    if mtime_repo is not None:
        assert watchlist.PATH.stat().st_mtime_ns == mtime_repo


def _fail_si_escribe_path_canonico(monkeypatch):
    """Condición Claude: PATH.write_text sobre el JSON versionado explota."""
    real_write = Path.write_text

    def _guarded(self, *args, **kwargs):
        try:
            mismo = self.resolve() == watchlist.PATH.resolve()
        except OSError:
            mismo = self == watchlist.PATH
        if mismo:
            raise AssertionError(
                f"VPS --solo-watchlist escribió el PATH canónico ({self})"
            )
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _guarded)


# ------------------------- fail-loud + call sites (SPEC Claude) -------------------------
# Anclas en origin/main 2026-09-18: L930 / L988 / L1034 / L1067.
# Tras el wrapper, viven en `_revisar_watchlist_cuerpo` vía `_persistir_rechequeo`.

def test_revisar_watchlist_cuerpo_tiene_exactamente_cuatro_persistencias_y_cero_guardar():
    src = inspect.getsource(run_mod._revisar_watchlist_cuerpo)
    assert "watchlist.guardar(" not in src
    assert src.count("_persistir_rechequeo(") == 4


def test_full_scan_guardar_still_writes_canonical():
    """GHA full scan (main L1295 / `_actualizar_watchlist` L707) no se desvía."""
    src_upd = inspect.getsource(run_mod._actualizar_watchlist)
    src_main = inspect.getsource(run_mod.main)
    assert "watchlist.guardar(" in src_upd
    assert "guardar_vps_state" not in src_upd
    assert "watchlist.guardar(" in src_main


def test_cargar_aplica_overlay_por_ticker(tmp_path, monkeypatch):
    """L916: canónico read-only + overlay por ticker. RKLB sin overlay no se toca."""
    vra = watchlist.desde_candidato_diario(_candidato_diario("VRA"), AHORA)
    rklb = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    canonical = tmp_path / "watchlist.json"
    watchlist.guardar([vra, rklb], canonical)
    overlay_e = watchlist.desde_candidato_diario(_candidato_diario("VRA"), AHORA)
    watchlist.expirar_vencidas(
        [overlay_e], minutos_maximos=1, ahora=AHORA + timedelta(hours=3))
    state = tmp_path / "state.json"
    watchlist.guardar_vps_state(
        [overlay_e], path=state, ahora=AHORA + timedelta(hours=3))
    monkeypatch.setenv("MOMENTUM_WATCHLIST_STATE", str(state))
    fused = {e.ticker: e for e in watchlist.cargar(canonical, apply_vps_state=True)}
    assert fused["VRA"].estado == watchlist.ESTADO_EXPIRED
    assert fused["RKLB"].estado == watchlist.ESTADO_WATCHING
    assert fused["VRA"].catalizador_tipo == "contrato"


def test_solo_watchlist_canonical_write_fail_loud(monkeypatch, tmp_path):
    """Forzar `guardar(PATH)` bajo flag VPS → RuntimeError. No suciar el tree."""
    e = watchlist.desde_candidato_diario(_candidato_diario(), AHORA)
    _preparar_vps(monkeypatch, tmp_path, [e])
    _parchear_efectos(monkeypatch)

    def persistir_malo(entradas):
        watchlist.guardar(entradas)

    monkeypatch.setattr(run_mod, "_persistir_rechequeo", persistir_malo)
    with pytest.raises(watchlist.EscrituraWatchlistCanonicaProhibida):
        run_mod.revisar_watchlist(CFG, _FakeProvider(set()), dry_run=False, ahora=AHORA)

    with watchlist.prohibir_escritura_canonica():
        with pytest.raises(watchlist.EscrituraWatchlistCanonicaProhibida):
            watchlist.guardar([e])
    with pytest.raises(watchlist.EscrituraWatchlistCanonicaProhibida):
        watchlist.guardar_vps_state([e], path=watchlist.PATH)


def test_solo_watchlist_early_empty_writes_state_not_canonical(monkeypatch, tmp_path):
    """main L930: early exit vacío / purga. State sí, PATH.write_text no."""
    vieja = watchlist.desde_candidato_diario(
        _candidato_diario("RKLB"), AHORA - timedelta(days=10))
    watchlist.marcar_missed(vieja, "x", AHORA - timedelta(days=10))
    canonical, state, mtime_repo, mtime_canonical = _preparar_vps(
        monkeypatch, tmp_path, [vieja])
    sha = hashlib.sha256(canonical.read_bytes()).hexdigest()
    _parchear_efectos(monkeypatch)
    _fail_si_escribe_path_canonico(monkeypatch)

    run_mod.revisar_watchlist(CFG, _FakeProvider(set()), dry_run=False, ahora=AHORA)

    _assert_sin_tocar_canonico(canonical, mtime_repo, mtime_canonical, sha)
    assert state.exists()
    data = json.loads(state.read_text())
    assert data["schema"] == 1
    assert data["source"] == "vps-solo-watchlist"
    assert "RKLB" not in data["entries"]


def test_solo_watchlist_no_candidates_expires_to_state_only(monkeypatch, tmp_path):
    """main L988: sin candidatos → expirar al state, canónico intacto."""
    vieja = watchlist.desde_candidato_diario(
        _candidato_diario("VRA"), AHORA - timedelta(hours=3))
    canonical, state, mtime_repo, mtime_canonical = _preparar_vps(
        monkeypatch, tmp_path, [vieja])
    sha = hashlib.sha256(canonical.read_bytes()).hexdigest()
    _parchear_efectos(monkeypatch)
    _fail_si_escribe_path_canonico(monkeypatch)

    run_mod.revisar_watchlist(CFG, _FakeProvider(set()), dry_run=False, ahora=AHORA)

    _assert_sin_tocar_canonico(canonical, mtime_repo, mtime_canonical, sha)
    assert json.loads(canonical.read_text())["entradas"][0]["estado"] == watchlist.ESTADO_WATCHING
    data = json.loads(state.read_text())
    assert data["entries"]["VRA"]["estado"] == watchlist.ESTADO_EXPIRED
    recargadas = watchlist.aplicar_overlay(watchlist.cargar(canonical), state)
    assert recargadas[0].estado == watchlist.ESTADO_EXPIRED


def test_solo_watchlist_commit_before_telegram_state_only(monkeypatch, tmp_path):
    """main L1034: COMMIT pre-Telegram. Canónico sha/mtime intacto; state tiene ticker."""
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    canonical, state, mtime_repo, mtime_canonical = _preparar_vps(
        monkeypatch, tmp_path, [e])
    sha = hashlib.sha256(canonical.read_bytes()).hexdigest()
    _parchear_efectos(monkeypatch)
    _fail_si_escribe_path_canonico(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    primeras: list[str] = []
    real = watchlist.guardar_vps_state

    def spy(entradas, path=None, ahora=None):
        if not primeras:
            primeras.append(entradas[0].estado)
        return real(entradas, path=path, ahora=ahora)

    monkeypatch.setattr(watchlist, "guardar_vps_state", spy)

    run_mod.revisar_watchlist(
        CFG, _FakeProvider({"RKLB"}), dry_run=False, ahora=AHORA)

    _assert_sin_tocar_canonico(canonical, mtime_repo, mtime_canonical, sha)
    assert primeras == [watchlist.ESTADO_TRIGGERED]
    assert json.loads(state.read_text())["entries"]["RKLB"]["estado"] == watchlist.ESTADO_TRIGGERED
    recargadas = watchlist.aplicar_overlay(watchlist.cargar(canonical), state)
    assert recargadas[0].estado == watchlist.ESTADO_TRIGGERED


def test_solo_watchlist_latency_second_flush_state_only(monkeypatch, tmp_path):
    """main L1067: segundo flush post-Telegram. Latencia en state, no en PATH."""
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    canonical, state, mtime_repo, mtime_canonical = _preparar_vps(
        monkeypatch, tmp_path, [e])
    sha = hashlib.sha256(canonical.read_bytes()).hexdigest()
    _parchear_efectos(monkeypatch)
    _fail_si_escribe_path_canonico(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    llamadas: list[float | None] = []
    real = watchlist.guardar_vps_state

    def spy(entradas, path=None, ahora=None):
        llamadas.append(entradas[0].signal_latency_ms)
        return real(entradas, path=path, ahora=ahora)

    monkeypatch.setattr(watchlist, "guardar_vps_state", spy)

    run_mod.revisar_watchlist(
        CFG, _FakeProvider({"RKLB"}), dry_run=False, ahora=AHORA)

    _assert_sin_tocar_canonico(canonical, mtime_repo, mtime_canonical, sha)
    assert len(llamadas) == 2
    assert llamadas[0] is None
    assert llamadas[1] is not None
    recargadas = watchlist.aplicar_overlay(watchlist.cargar(canonical), state)
    assert recargadas[0].signal_latency_ms is not None


def test_dry_run_con_flag_no_escribe_state_ni_canonico(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    canonical, state, mtime_repo, mtime_canonical = _preparar_vps(
        monkeypatch, tmp_path, [e])
    _parchear_efectos(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    run_mod.revisar_watchlist(
        CFG, _FakeProvider({"RKLB"}), dry_run=True, ahora=AHORA)

    _assert_sin_tocar_canonico(canonical, mtime_repo, mtime_canonical)
    assert not state.exists()


def test_actualizar_watchlist_con_flag_no_escribe_state(monkeypatch, tmp_path):
    # Full scan / GHA: el flag no desvía `_actualizar_watchlist`.
    _activar_vps(monkeypatch, tmp_path)
    path = tmp_path / "watchlist.json"
    watchlist.guardar([], path)
    real_cargar, real_guardar = watchlist.cargar, watchlist.guardar
    monkeypatch.setattr(watchlist, "cargar", lambda p=path, apply_vps_state=False: real_cargar(p))
    monkeypatch.setattr(watchlist, "guardar", lambda es, p=path, ahora=None: real_guardar(es, p, ahora=ahora))
    c_diario = _candidato_diario("RKLB")
    c_intradia = _candidato_intradia("RKLB", accionable=True)

    run_mod._actualizar_watchlist(
        [c_diario], [c_intradia], {"RKLB"}, CFG, dry_run=False, ahora=AHORA)

    recargadas = watchlist.cargar(path)
    assert recargadas[0].estado == watchlist.ESTADO_TRIGGERED
    state = Path(os.environ["MOMENTUM_WATCHLIST_STATE"])
    assert not state.exists()


def test_flag_apagado_sigue_usando_guardar(monkeypatch, tmp_path):
    monkeypatch.setenv("MOMENTUM_WATCHLIST_VPS_STATE", "0")
    path = tmp_path / "watchlist.json"
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.guardar([e], path)
    real_cargar, real_guardar = watchlist.cargar, watchlist.guardar
    monkeypatch.setattr(watchlist, "cargar", lambda p=path, apply_vps_state=False: real_cargar(p))
    monkeypatch.setattr(watchlist, "guardar", lambda es, p=path, ahora=None: real_guardar(es, p, ahora=ahora))
    _parchear_efectos(monkeypatch)
    monkeypatch.setattr(
        run_mod, "_construir_candidato_intradia",
        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))

    run_mod.revisar_watchlist(
        CFG, _FakeProvider({"RKLB"}), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_TRIGGERED


# ------------------------- reglas de conflicto -------------------------


def test_overlay_ticker_ausente_del_canonico_se_descarta(tmp_path):
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    fantasma = watchlist.desde_candidato_diario(_candidato_diario("GHOST"), AHORA)
    watchlist.marcar_missed(fantasma, "x", AHORA + timedelta(minutes=5))
    state = tmp_path / "state.json"
    watchlist.guardar_vps_state([canon, fantasma], path=state, ahora=AHORA + timedelta(minutes=5))
    fused = watchlist.aplicar_overlay([canon], state)
    assert [e.ticker for e in fused] == ["RKLB"]


def test_canonico_terminal_gana_si_overlay_cambia_estado():
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_triggered(canon, "m", "d", "ev", AHORA)
    overlay = {
        "estado": watchlist.ESTADO_WATCHING,
        "actualizado_en": (AHORA + timedelta(minutes=10)).isoformat(timespec="seconds"),
        "overlay_ts": (AHORA + timedelta(minutes=10)).isoformat(timespec="seconds"),
        "transiciones_append": [],
    }
    fused = watchlist._fusionar_overlay(canon, overlay)
    assert fused.estado == watchlist.ESTADO_TRIGGERED


def test_watching_con_overlay_mas_nuevo_gana_vps():
    canon = watchlist.desde_candidato_diario(_candidato_diario("VRA"), AHORA)
    overlay_e = watchlist.desde_candidato_diario(_candidato_diario("VRA"), AHORA)
    watchlist.expirar_vencidas([overlay_e], minutos_maximos=1, ahora=AHORA + timedelta(hours=3))
    overlay = watchlist._entrada_a_overlay(
        overlay_e, (AHORA + timedelta(hours=3)).isoformat(timespec="seconds"))
    fused = watchlist._fusionar_overlay(canon, overlay)
    assert fused.estado == watchlist.ESTADO_EXPIRED
    assert fused.catalizador_tipo == "contrato"   # snapshot sigue siendo GHA
    assert fused.nombre == "Rocket Lab Corp"


def test_empate_de_timestamps_gana_gha():
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    overlay = {
        "estado": watchlist.ESTADO_EXPIRED,
        "actualizado_en": canon.actualizado_en,
        "overlay_ts": canon.actualizado_en,
        "transiciones_append": [{"a": "expired", "motivo": "x", "en": canon.actualizado_en}],
    }
    fused = watchlist._fusionar_overlay(canon, overlay)
    assert fused.estado == watchlist.ESTADO_WATCHING


def test_overlay_no_pisa_catalizador_aunque_venga_en_el_json():
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    overlay = {
        "estado": watchlist.ESTADO_WATCHING,
        "actualizado_en": (AHORA + timedelta(minutes=1)).isoformat(timespec="seconds"),
        "overlay_ts": (AHORA + timedelta(minutes=1)).isoformat(timespec="seconds"),
        "catalizador_tipo": "fda",
        "catalizador_titular": "NO",
        "nombre": "HACK",
        "score_base": 1.0,
        "tarde_consecutivas": 2,
        "transiciones_append": [],
    }
    fused = watchlist._fusionar_overlay(canon, overlay)
    assert fused.catalizador_tipo == "contrato"
    assert fused.nombre == "Rocket Lab Corp"
    assert fused.score_base == 88.0
    assert fused.tarde_consecutivas == 2


def test_transiciones_append_no_borra_el_historial_canonico():
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    n_antes = len(canon.transiciones)
    overlay_e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    watchlist.marcar_missed(overlay_e, "tarde", AHORA + timedelta(minutes=20))
    overlay = watchlist._entrada_a_overlay(
        overlay_e, (AHORA + timedelta(minutes=20)).isoformat(timespec="seconds"))
    fused = watchlist._fusionar_overlay(canon, overlay)
    assert len(fused.transiciones) == n_antes + 1
    assert fused.transiciones[0].estado == watchlist.ESTADO_WATCHING
    assert fused.transiciones[-1].estado == watchlist.ESTADO_MISSED


def test_niveles_sin_cambiar_actualizado_en_igual_se_aplican():
    # `actualizar_niveles` no toca actualizado_en. overlay_ts desempata.
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    overlay = {
        "estado": watchlist.ESTADO_WATCHING,
        "actualizado_en": canon.actualizado_en,
        "overlay_ts": (AHORA + timedelta(minutes=7)).isoformat(timespec="seconds"),
        "ultima_entrada": 5.30,
        "ultimo_stop": 5.00,
        "ultimo_objetivo": 5.90,
        "ultimos_niveles_ts": (AHORA + timedelta(minutes=7)).isoformat(timespec="seconds"),
        "transiciones_append": [],
    }
    fused = watchlist._fusionar_overlay(canon, overlay)
    assert fused.estado == watchlist.ESTADO_WATCHING
    assert fused.ultima_entrada == 5.30
    assert fused.ultimo_stop == 5.00


def test_state_corrupto_no_inventa_overlay(tmp_path):
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    state = tmp_path / "state.json"
    state.write_text("{no json")
    fused = watchlist.aplicar_overlay([canon], state)
    assert fused[0].estado == watchlist.ESTADO_WATCHING


def test_schema_desconocido_se_ignora(tmp_path):
    canon = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"schema": 99, "entries": {"RKLB": {"estado": "expired"}}}))
    fused = watchlist.aplicar_overlay([canon], state)
    assert fused[0].estado == watchlist.ESTADO_WATCHING


def test_vps_state_habilitado_lee_el_flag(monkeypatch):
    monkeypatch.delenv("MOMENTUM_WATCHLIST_VPS_STATE", raising=False)
    assert watchlist.vps_state_habilitado() is False
    monkeypatch.setenv("MOMENTUM_WATCHLIST_VPS_STATE", "1")
    assert watchlist.vps_state_habilitado() is True
    monkeypatch.setenv("MOMENTUM_WATCHLIST_VPS_STATE", "0")
    assert watchlist.vps_state_habilitado() is False


# ------------------------- backup diario (Claude #1) -------------------------

def test_script_backup_copia_a_fecha_monterrey(tmp_path):
    script = REPO / "scripts" / "backup_watchlist_vps_state.sh"
    state = tmp_path / "state.json"
    dest = tmp_path / "momentum"
    state.write_text('{"schema": 1, "entries": {"VRA": {"estado": "expired"}}}')
    env = {
        **os.environ,
        "TZ": "America/Monterrey",
        "MOMENTUM_WATCHLIST_STATE": str(state),
        "MOMENTUM_WATCHLIST_STATE_BACKUP_DIR": str(dest),
    }
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    stamp = subprocess.check_output(["date", "+%F"], env=env, text=True).strip()
    copia = dest / f"watchlist_vps_state-{stamp}.json"
    assert copia.exists()
    assert "expired" in copia.read_text()


def test_script_backup_sin_state_no_falla(tmp_path):
    script = REPO / "scripts" / "backup_watchlist_vps_state.sh"
    env = {
        **os.environ,
        "MOMENTUM_WATCHLIST_STATE": str(tmp_path / "no-existe.json"),
        "MOMENTUM_WATCHLIST_STATE_BACKUP_DIR": str(tmp_path / "backups"),
    }
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "nothing to backup" in r.stdout


def test_script_backup_borra_copias_de_mas_de_14_dias(tmp_path):
    script = REPO / "scripts" / "backup_watchlist_vps_state.sh"
    state = tmp_path / "state.json"
    dest = tmp_path / "momentum"
    dest.mkdir()
    state.write_text('{"schema": 1}')
    vieja = dest / "watchlist_vps_state-2000-01-01.json"
    vieja.write_text("old")
    os.utime(vieja, (0, 0))
    env = {
        **os.environ,
        "TZ": "America/Monterrey",
        "MOMENTUM_WATCHLIST_STATE": str(state),
        "MOMENTUM_WATCHLIST_STATE_BACKUP_DIR": str(dest),
    }
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not vieja.exists()
    stamp = subprocess.check_output(["date", "+%F"], env=env, text=True).strip()
    assert (dest / f"watchlist_vps_state-{stamp}.json").exists()


def test_crontab_backup_coincide_con_el_spec():
    cron = (REPO / "infra" / "cron" / "momentum-watchlist-state-backup").read_text()
    assert "CRON_TZ=America/Monterrey" in cron
    assert "15 2 * * *" in cron
    assert "/var/backups/momentum/watchlist_vps_state-" in cron
    assert "-mtime +14 -delete" in cron
    assert "momentum-watchlist.timer" not in cron.split("15 2")[1]  # la línea cron no arranca el timer


def test_unidades_backup_existen_y_el_timer_de_rechequeo_no_se_toca():
    backup_service = REPO / "infra" / "systemd" / "momentum-watchlist-state-backup.service"
    backup_timer = REPO / "infra" / "systemd" / "momentum-watchlist-state-backup.timer"
    assert backup_service.is_file()
    assert backup_timer.is_file()
    texto_svc = backup_service.read_text()
    texto_tmr = backup_timer.read_text()
    assert "backup_watchlist_vps_state.sh" in texto_svc
    assert "America/Monterrey" in texto_svc
    assert "02:15:00" in texto_tmr
    rechequeo = (REPO / "infra" / "systemd" / "momentum-watchlist.timer").read_text()
    assert "OnCalendar=" in rechequeo
