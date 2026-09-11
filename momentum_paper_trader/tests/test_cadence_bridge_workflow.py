"""Invariantes del puente temporal de cadencia paper (GHA).

No corre el loop de 5 h ni toca Alpaca. Solo verifica que el YAML y el
orquestador no re-mezclen los grupos de concurrency de #110, no
apunten a live, respeten el tope de 6 h, y que la ventana / el
re-dispatch / el kill switch se comporten como está documentado.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "momentum_paper_cadence_bridge.yml"
WATCHLIST_WF = ROOT / ".github" / "workflows" / "momentum_hunter_watchlist.yml"
HUNTER_WF = ROOT / ".github" / "workflows" / "momentum_hunter.yml"
SCRIPT = ROOT / ".github" / "scripts" / "momentum_paper_cadence_bridge.py"

LIVE_ALPACA = "https://api.alpaca.markets"
PAPER_ALPACA = "https://paper-api.alpaca.markets"


def _cargar_script():
    spec = importlib.util.spec_from_file_location("cadence_bridge", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _grupo_concurrency(texto: str) -> str:
    m = re.search(r"^  group:\s*(\S+)\s*$", texto, re.M)
    assert m, "workflow sin concurrency.group"
    return m.group(1)


def test_workflow_y_script_existen():
    assert WORKFLOW.is_file()
    assert SCRIPT.is_file()


def test_concurrency_comparte_watchlist_no_hunter():
    """PR #110: hunter y watchlist NO se re-mezclan. El puente se
    serializa con el re-chequeo corto (mismo grupo) para no duplicar
    commits de watchlist.json cada 5 min."""
    puente = WORKFLOW.read_text(encoding="utf-8")
    watchlist = WATCHLIST_WF.read_text(encoding="utf-8")
    hunter = HUNTER_WF.read_text(encoding="utf-8")
    assert _grupo_concurrency(puente) == "momentum-opportunity-hunter-watchlist"
    assert _grupo_concurrency(watchlist) == "momentum-opportunity-hunter-watchlist"
    assert _grupo_concurrency(hunter) == "momentum-opportunity-hunter"
    assert _grupo_concurrency(puente) != _grupo_concurrency(hunter)
    assert re.search(r"cancel-in-progress:\s*false", puente)


def test_cron_corto_de_watchlist_sigue_en_pie():
    """El puente no reemplaza el cron de 5 min: es extra, no migración
    silenciosa. Apagar el puente deja el fallback."""
    watchlist = WATCHLIST_WF.read_text(encoding="utf-8")
    assert 'cron: "*/5 13-20 * * 1-5"' in watchlist


def test_timeout_bajo_el_tope_hosted_de_6h():
    texto = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"timeout-minutes:\s*(\d+)", texto)
    assert m, "falta timeout-minutes"
    timeout = int(m.group(1))
    assert timeout <= 350
    assert timeout < 360


def test_no_apunta_a_alpaca_live():
    """Prohibido el endpoint live. Mencionar `api.alpaca.markets` en un
    comentario de 'nunca apuntar ahí' está bien; la URL https no."""
    for path in (WORKFLOW, SCRIPT):
        texto = path.read_text(encoding="utf-8")
        assert LIVE_ALPACA not in texto
        assert PAPER_ALPACA not in texto or "paper-api" in texto


def test_invoca_el_mismo_camino_watchlist_paper():
    fuente = SCRIPT.read_text(encoding="utf-8")
    assert "momentum_hunter.run" in fuente
    assert "--solo-watchlist" in fuente
    assert "momentum_paper_trader.run" in fuente
    assert "revisiones.json" in fuente
    assert "archivo_triggered.jsonl" in fuente


def test_gha_no_persiste_telemetria_paper_del_vps():
    """El VPS es el escritor primario. `git add` del diario paper
    desde GHA reventaba el rebase (run 34624961161)."""
    for path in (HUNTER_WF, WATCHLIST_WF):
        texto = path.read_text(encoding="utf-8")
        assert "git add momentum_paper_trader/telemetria" not in texto
        assert "git_persist_rebase_push.sh" in texto
        assert "--force" not in texto
        assert "force-with-lease" not in texto
    b = _cargar_script()
    assert "momentum_paper_trader/telemetria" not in b.DIRS_A_PERSISTIR
    assert "git_persist_rebase_push.sh" in SCRIPT.read_text(encoding="utf-8")


def test_documenta_kill_switch_y_minutos_privados():
    texto = WORKFLOW.read_text(encoding="utf-8")
    assert "MOMENTUM_CADENCE_BRIDGE" in texto
    assert "Disable workflow" in texto
    assert "privado" in texto.lower()
    assert "2 000" in texto or "2000" in texto


def test_ventana_habil_13_21_utc():
    b = _cargar_script()
    # viernes 13:00 y 20:59 están dentro; 21:00 y sábado no
    assert b.en_ventana_mercado(datetime(2026, 9, 11, 13, 0, tzinfo=UTC)) is True
    assert b.en_ventana_mercado(datetime(2026, 9, 11, 20, 59, tzinfo=UTC)) is True
    assert b.en_ventana_mercado(datetime(2026, 9, 11, 21, 0, tzinfo=UTC)) is False
    assert b.en_ventana_mercado(datetime(2026, 9, 11, 12, 59, tzinfo=UTC)) is False
    assert b.en_ventana_mercado(datetime(2026, 9, 12, 15, 0, tzinfo=UTC)) is False  # sábado


def test_kill_switch():
    b = _cargar_script()
    assert b.esta_habilitado("") is True
    assert b.esta_habilitado("off") is False
    assert b.esta_habilitado("0") is False
    assert b.esta_habilitado("false") is False
    assert b.esta_habilitado("ON") is True


def test_presupuesto_y_espera_sin_deuda():
    b = _cargar_script()
    inicio = datetime(2026, 9, 11, 13, 0, tzinfo=UTC)
    assert b.presupuesto_agotado(inicio, inicio + timedelta(minutes=339), 340) is False
    assert b.presupuesto_agotado(inicio, inicio + timedelta(minutes=340), 340) is True
    iter_inicio = inicio
    # iteración de 40 s → dormir 260 s de un intervalo de 300
    ahora = iter_inicio + timedelta(seconds=40)
    assert b.segundos_de_espera(iter_inicio, ahora, 300) == 260
    # iteración más larga que el intervalo → 0, no se acumula
    ahora = iter_inicio + timedelta(seconds=400)
    assert b.segundos_de_espera(iter_inicio, ahora, 300) == 0


def test_redispatch_solo_por_presupuesto_dentro_de_ventana():
    b = _cargar_script()
    dentro = datetime(2026, 9, 11, 18, 40, tzinfo=UTC)
    fuera = datetime(2026, 9, 11, 21, 5, tzinfo=UTC)
    assert b.debe_redispatch("presupuesto", dentro) is True
    assert b.debe_redispatch("presupuesto", fuera) is False
    assert b.debe_redispatch("fuera_de_ventana", dentro) is False
    assert b.debe_redispatch("deshabilitado", dentro) is False


def test_minutos_hasta_ventana_no_inventa_si_ya_estamos():
    b = _cargar_script()
    assert b.minutos_hasta_ventana(datetime(2026, 9, 11, 14, 0, tzinfo=UTC)) is None
    # viernes 12:50 → 10 min al 13:00 del mismo día
    delta = b.minutos_hasta_ventana(datetime(2026, 9, 11, 12, 50, tzinfo=UTC))
    assert delta is not None
    assert 9.9 < delta < 10.1
    # viernes 21:10 → lunes 13:00 (no inventa un sábado)
    viernes_noche = datetime(2026, 9, 11, 21, 10, tzinfo=UTC)
    lunes_13 = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    delta = b.minutos_hasta_ventana(viernes_noche)
    assert delta is not None
    assert abs(delta - (lunes_13 - viernes_noche).total_seconds() / 60) < 0.01
