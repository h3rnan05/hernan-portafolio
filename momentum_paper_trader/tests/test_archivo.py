"""Archivo de TRIGGERED tras revisión paper -- sin red, archivos en tmp.

Cubre el diagnóstico (C): la watchlist no tenía transición terminal
después de la revisión. No toca umbrales, stops, ATR ni el corte de
confianza de la IA."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import CONFIG as HUNTER_CFG
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion
from momentum_paper_trader import alpaca_client, archivo, estado, ia_decision
from momentum_paper_trader.config import CONFIG as PAPER_CFG

AHORA = datetime(2026, 9, 11, 18, 0, 0, tzinfo=UTC)


def _candidato(ticker: str) -> CandidatoDiario:
    return CandidatoDiario(
        ticker=ticker, nombre=ticker, precio=12.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=0.73),
        catalizador=Catalizador(
            tipo="fda", titular="x", fuente="x", fecha="2026-09-08T12:00:00+00:00"),
        meta=Metadata(ticker=ticker, shares_float=1.0, short_pct_float=0.1),
        puntuacion=Puntuacion(ticker=ticker, score_total=70.0, sub={}),
        es_large_cap=False,
    )


def _triggered(ticker: str, creado: datetime = AHORA) -> watchlist.EntradaWatchlist:
    e = watchlist.desde_candidato_diario(_candidato(ticker), creado)
    watchlist.marcar_triggered(e, "m", "d", "ev", creado)
    watchlist.actualizar_niveles(e, 12.88, 12.77, 13.08, 12.88, creado)
    return e


def _revision(
    ticker: str, creado_en: str, *,
    entro: bool = False, resultado: str | None = None,
    order_id: str | None = None, timestamp: str = "2026-09-11T18:00:05+00:00",
) -> estado.RevisionIA:
    return estado.RevisionIA(
        ticker=ticker, creado_en=creado_en, entro=entro, confianza=7,
        razonamiento="x", timestamp=timestamp, order_id=order_id,
        resultado=resultado,
    )


def _archivar(entradas, revisiones, tmp_path, ahora=AHORA, **kw):
    return archivo.archivar_revisadas(
        entradas=entradas, revisiones=revisiones, ahora=ahora,
        path_watchlist=tmp_path / "watchlist.json",
        path_log=tmp_path / "archivo_triggered.jsonl",
        persistir_watchlist=True, **kw,
    )


def _lineas_log(tmp_path: Path) -> list[dict]:
    path = tmp_path / "archivo_triggered.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_archiva_triggered_tras_rechazo_ia(tmp_path):
    e = _triggered("BEAM")
    r = _revision("BEAM", e.creado_en, entro=False)
    escritos = _archivar([e], [r], tmp_path)

    assert e.estado == watchlist.ESTADO_ARCHIVED
    assert e.transiciones[-1].estado == watchlist.ESTADO_ARCHIVED
    assert len(escritos) == 1
    rec = escritos[0]
    for campo in archivo.CAMPOS_AUDITORIA:
        assert campo in rec, campo
    assert rec["ticker"] == "BEAM"
    assert rec["estado_anterior"] == watchlist.ESTADO_TRIGGERED
    assert rec["estado_nuevo"] == watchlist.ESTADO_ARCHIVED
    assert rec["revision_timestamp"] == r.timestamp
    assert rec["revision_entro"] is False
    assert rec["desenlace_paper"] == "rechazo_ia"
    assert rec["causa_raiz"] == archivo.CAUSA_TRANSICION_AUSENTE
    assert rec["archivado_en"] == "2026-09-11T18:00:00+00:00"
    recargada = watchlist.cargar(tmp_path / "watchlist.json")[0]
    assert recargada.estado == watchlist.ESTADO_ARCHIVED
    assert recargada.ticker == "BEAM"   # no se borró


def test_el_jsonl_conserva_la_banda_de_la_revision(tmp_path):
    # La watchlist se purga a los 7 días; el JSONL es el único sitio
    # donde la banda de cada desenlace sobrevive.
    e = _triggered("BEAM")
    r = _revision("BEAM", e.creado_en, entro=False)
    r.es_large_cap = True
    rec = _archivar([e], [r], tmp_path)[0]
    assert rec["revision_es_large_cap"] is True
    assert _lineas_log(tmp_path)[0]["revision_es_large_cap"] is True


def test_el_jsonl_no_inventa_banda_para_una_revision_vieja(tmp_path):
    e = _triggered("NTLA")
    r = _revision("NTLA", e.creado_en, entro=False)   # sin es_large_cap -> None
    rec = _archivar([e], [r], tmp_path)[0]
    assert "revision_es_large_cap" in rec
    assert rec["revision_es_large_cap"] is None


def test_fuera_de_banda_se_archiva_con_su_propio_desenlace(tmp_path):
    # Una large-cap que la IA aprobó pero la compuerta no operó: es
    # terminal (nunca va a colocar orden), pero NO es rechazo_ia.
    e = _triggered("LLY")
    r = _revision("LLY", e.creado_en, entro=False)
    r.ia_entraria = True
    r.motivo_no_operada = estado.MOTIVO_FUERA_DE_BANDA
    escritos = _archivar([e], [r], tmp_path)

    assert e.estado == watchlist.ESTADO_ARCHIVED
    assert escritos[0]["desenlace_paper"] == "fuera_de_banda"
    assert escritos[0]["revision_entro"] is False
    assert _lineas_log(tmp_path)[0]["desenlace_paper"] == "fuera_de_banda"


def test_fuera_de_banda_gana_aunque_la_ia_haya_dicho_que_no(tmp_path):
    # El "no" de la IA sobre una señal no operable tampoco cuenta como
    # rechazo_ia -- la muestra de rechazos genuinos queda limpia.
    e = _triggered("BEAM")
    r = _revision("BEAM", e.creado_en, entro=False)
    r.ia_entraria = False
    r.motivo_no_operada = estado.MOTIVO_FUERA_DE_BANDA
    assert archivo.desenlace_paper(r) == "fuera_de_banda"


def test_precio_fuera_de_alcance_se_archiva_sin_veredicto_de_ia(tmp_path):
    # (2026-09-22) GS a $949 con $5.000: no cabe ni una acción en el 15 %.
    # Se registró sin consultar a la IA (ia_entraria=None) y es terminal
    # con su propio desenlace -- ni rechazo_ia ni fuera_de_banda.
    e = _triggered("GS")
    r = _revision("GS", e.creado_en, entro=False)
    r.ia_entraria = None
    r.motivo_no_operada = estado.MOTIVO_PRECIO_FUERA_DE_ALCANCE
    escritos = _archivar([e], [r], tmp_path)

    assert e.estado == watchlist.ESTADO_ARCHIVED
    assert escritos[0]["desenlace_paper"] == "precio_fuera_de_alcance"
    assert escritos[0]["revision_entro"] is False


def test_rechazo_ia_sin_motivo_sigue_siendo_rechazo_ia(tmp_path):
    e = _triggered("NTLA")
    r = _revision("NTLA", e.creado_en, entro=False)
    assert archivo.desenlace_paper(r) == "rechazo_ia"


def test_archiva_triggered_tras_stop_como_ntla(tmp_path):
    creado = datetime(2026, 9, 8, 17, 18, 33, tzinfo=UTC)
    e = _triggered("NTLA", creado)
    r = _revision(
        "NTLA", e.creado_en, entro=True, resultado="stop",
        order_id="2e9e4167-873b-4532-8892-39b27d9fb191",
        timestamp="2026-09-08T17:18:39+00:00",
    )
    escritos = _archivar([e], [r], tmp_path, ahora=AHORA)

    assert e.estado == watchlist.ESTADO_ARCHIVED
    rec = escritos[0]
    assert rec["desenlace_paper"] == "stop"
    assert rec["revision_order_id"] == r.order_id
    assert rec["revision_resultado"] == "stop"
    assert rec["causa_raiz"] == "C"
    log = _lineas_log(tmp_path)
    assert len(log) == 1
    assert log[0]["ticker"] == "NTLA"


def test_auditoria_no_desaparece_si_se_purga_la_watchlist(tmp_path):
    e = _triggered("BEAM")
    r = _revision("BEAM", e.creado_en, entro=False)
    _archivar([e], [r], tmp_path)
    # La watchlist operativa se puede purgar a los 7 días; el JSONL no.
    purgadas = watchlist.purgar_antiguas([e], AHORA + timedelta(days=8))
    assert purgadas == []
    assert _lineas_log(tmp_path)[0]["ticker"] == "BEAM"


def test_no_archiva_watching_ni_missed_aunque_haya_revision(tmp_path):
    watching = watchlist.desde_candidato_diario(_candidato("WATCH"), AHORA)
    missed = watchlist.desde_candidato_diario(_candidato("MISS"), AHORA)
    watchlist.marcar_missed(missed, "tarde", AHORA)
    expired = watchlist.desde_candidato_diario(_candidato("EXP"), AHORA - timedelta(hours=3))
    watchlist.expirar_vencidas([expired], minutos_maximos=120, ahora=AHORA)
    revisiones = [
        _revision("WATCH", watching.creado_en, entro=False),
        _revision("MISS", missed.creado_en, entro=False),
        _revision("EXP", expired.creado_en, entro=False),
    ]
    escritos = _archivar([watching, missed, expired], revisiones, tmp_path)
    assert escritos == []
    assert watching.estado == watchlist.ESTADO_WATCHING
    assert missed.estado == watchlist.ESTADO_MISSED
    assert expired.estado == watchlist.ESTADO_EXPIRED
    assert _lineas_log(tmp_path) == []


def test_no_archiva_triggered_sin_revision(tmp_path):
    e = _triggered("VIVA")
    assert _archivar([e], [], tmp_path) == []
    assert e.estado == watchlist.ESTADO_TRIGGERED


def test_no_archiva_trade_paper_todavia_abierto(tmp_path):
    e = _triggered("OPEN")
    r = _revision("OPEN", e.creado_en, entro=True, resultado="abierta", order_id="abc")
    assert _archivar([e], [r], tmp_path) == []
    assert e.estado == watchlist.ESTADO_TRIGGERED


def test_no_archiva_fill_sin_resultado_todavia(tmp_path):
    e = _triggered("PEND")
    r = _revision("PEND", e.creado_en, entro=True, resultado=None, order_id="abc")
    assert _archivar([e], [r], tmp_path) == []
    assert e.estado == watchlist.ESTADO_TRIGGERED


def test_idempotente_no_duplica_jsonl(tmp_path):
    e = _triggered("BEAM")
    r = _revision("BEAM", e.creado_en, entro=False)
    _archivar([e], [r], tmp_path)
    # Segunda pasada: ya ARCHIVED, no se reescribe el log.
    otra = _archivar([e], [r], tmp_path, ahora=AHORA + timedelta(minutes=5))
    assert otra == []
    assert len(_lineas_log(tmp_path)) == 1


def test_si_jsonl_ya_existe_igual_transiciona_sin_duplicar(tmp_path):
    """Crash entre JSONL y watchlist: la entrada sigue TRIGGERED, el
    log ya tiene la línea. Se transiciona; no se duplica el rastro."""
    e = _triggered("BEAM")
    r = _revision("BEAM", e.creado_en, entro=False)
    prev = archivo.registro_auditoria(e, r, AHORA)
    (tmp_path / "archivo_triggered.jsonl").write_text(
        json.dumps(prev, ensure_ascii=False) + "\n")
    escritos = _archivar([e], [r], tmp_path)
    assert e.estado == watchlist.ESTADO_ARCHIVED
    assert len(escritos) == 1
    assert len(_lineas_log(tmp_path)) == 1


def test_no_cambia_umbrales_ni_endpoint_paper():
    """El archivo no es una excusa para mover stops, ATR, el umbral de la
    IA ni live. (El umbral pasó de 7 a 6 el 2026-09-23 por decisión del
    dueño, en su propio PR; acá solo se fija que el archivo no lo toca.)"""
    assert PAPER_CFG.riesgo_dolares_por_operacion == 100.0
    assert PAPER_CFG.maximo_pct_efectivo_por_posicion == 0.15
    assert HUNTER_CFG.minutos_maximos_en_watching == 120
    assert HUNTER_CFG.riesgo_recompensa_minimo == 1.5
    # 5 desde el re-escalado del 2026-09-22 (rechequeo cada 60 s); antes 2.
    assert HUNTER_CFG.verificaciones_tarde_para_missed == 5
    fuente_ia = inspect.getsource(ia_decision)
    assert ia_decision.CONFIANZA_MINIMA_ENTRADA == 6
    assert "confianza < CONFIANZA_MINIMA_ENTRADA" in fuente_ia
    assert alpaca_client._BASE_URL == "https://paper-api.alpaca.markets/v2"
    fuente_archivo = inspect.getsource(archivo)
    assert "riesgo_dolares_por_operacion" not in fuente_archivo
    assert "riesgo_recompensa_minimo" not in fuente_archivo
    assert "confianza < 7" not in fuente_archivo
    assert "api.alpaca.markets" not in fuente_archivo or "paper-api" in fuente_archivo


def test_causa_raiz_invalida_se_guarda_como_desconocida(tmp_path):
    e = _triggered("X")
    r = _revision("X", e.creado_en, entro=False)
    rec = archivo.registro_auditoria(e, r, AHORA, causa_raiz="no-existe")
    assert rec["causa_raiz"] == archivo.CAUSA_DESCONOCIDA
    assert "desconocida" in rec["causa_raiz_detalle"]


def test_archivar_con_flag_vps_escribe_state_no_el_canonico(monkeypatch, tmp_path):
    """En VPS, ARCHIVED no puede suciar watchlist.json: ese PATH es de GHA."""
    monkeypatch.setenv("MOMENTUM_WATCHLIST_VPS_STATE", "1")
    state = tmp_path / "watchlist_vps_state.json"
    monkeypatch.setenv("MOMENTUM_WATCHLIST_STATE", str(state))
    e = _triggered("BEAM")
    r = _revision("BEAM", e.creado_en, entro=False)
    mtime = watchlist.PATH.stat().st_mtime_ns
    escritos = archivo.archivar_revisadas(
        entradas=[e], revisiones=[r], ahora=AHORA,
        path_log=tmp_path / "archivo_triggered.jsonl",
        persistir_watchlist=True,
    )
    assert escritos
    assert state.exists()
    assert json.loads(state.read_text())["entries"]["BEAM"]["estado"] == watchlist.ESTADO_ARCHIVED
    assert watchlist.PATH.stat().st_mtime_ns == mtime
